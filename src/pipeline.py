"""
Pipeline — Core ETL orchestrator.
Coordinates Extractors → LLM Agent → Loaders for all 300 patients.
"""
import logging
import threading
import yaml
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.extractors import fetch_all_patients, fetch_clinical_data, get_failed_calls
from src.llm_agent import extract_wound_data, determine_eligibility
import src.loaders as loaders

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "pipeline_config.yaml")
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

MAX_WORKERS = _cfg["pipeline"]["max_workers"]
TEST_MODE = _cfg["pipeline"]["test_mode"]

_db_lock = threading.Lock()
_progress = {"total": 0, "done": 0, "auto_accept": 0, "flag_for_review": 0, "reject": 0, "error": 0, "running": False}


def get_progress() -> dict:
    return dict(_progress)


def run_pipeline() -> dict:
    global _progress
    _progress = {"total": 0, "done": 0, "auto_accept": 0, "flag_for_review": 0, "reject": 0, "error": 0, "running": True}

    logger.info("=" * 60)
    logger.info("WOUND BILLING PIPELINE STARTING")
    logger.info("=" * 60)

    loaders.create_schema()

    all_patients = fetch_all_patients(test_mode=TEST_MODE)
    _progress["total"] = len(all_patients)

    if not all_patients:
        logger.error("No patients found")
        _progress["running"] = False
        return _progress

    logger.info(f"Processing {len(all_patients)} patients with {MAX_WORKERS} workers")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_patient, p): p for p in all_patients}
        for future in as_completed(futures):
            try:
                decision = future.result()
                if decision:
                    _progress[decision] = _progress.get(decision, 0) + 1
                else:
                    _progress["error"] += 1
            except Exception as e:
                p = futures[future]
                logger.error(f"Error {p.get('patient_id')}: {e}")
                _progress["error"] += 1
            _progress["done"] += 1

            if _progress["done"] % 10 == 0 or _progress["done"] == _progress["total"]:
                logger.info(
                    f"Progress: {_progress['done']}/{_progress['total']} | "
                    f"accept={_progress['auto_accept']} review={_progress['flag_for_review']} "
                    f"reject={_progress['reject']} error={_progress['error']}"
                )

    # Save failed API calls to retry bucket
    failed = get_failed_calls()
    if failed:
        conn = loaders.get_connection()
        try:
            for fc in failed:
                loaders.log_failed_api_call(
                    conn, fc["endpoint"], fc["api_url"],
                    fc["params"], str(fc["params"].get("patient_id", "")),
                    fc["last_error"],
                )
            conn.commit()
            logger.warning(f"{len(failed)} failed API calls saved to retry bucket")
        finally:
            conn.close()

    _progress["running"] = False
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    for k in ("auto_accept", "flag_for_review", "reject", "error"):
        logger.info(f"  {k:<20}: {_progress[k]}")
    logger.info("=" * 60)

    return _progress


def _process_patient(patient: dict) -> str | None:
    pid = patient.get("patient_id", "?")
    facility_id = patient.get("_facility_id")
    try:
        clinical_data = fetch_clinical_data(patient)
        wound = extract_wound_data(clinical_data["notes"], clinical_data["assessments"])
        eligibility = determine_eligibility(clinical_data, wound)

        with _db_lock:
            conn = loaders.get_connection()
            try:
                loaders.upsert_patient(conn, patient, facility_id)
                loaders.insert_diagnoses(conn, pid, clinical_data["diagnoses"])
                loaders.insert_coverage(conn, pid, clinical_data["coverage"])
                loaders.insert_notes(conn, clinical_data["patient_int_id"], pid, clinical_data["notes"])
                loaders.insert_assessments(conn, clinical_data["patient_int_id"], pid, clinical_data["assessments"])
                loaders.upsert_wound_extraction(conn, pid, wound)
                loaders.upsert_eligibility(conn, eligibility)
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                conn.close()

        return eligibility["routing_decision"]
    except Exception as e:
        logger.error(f"Failed {pid}: {e}")
        return None
