"""
Orchestrator Agent — coordinates all agents to run the full pipeline.
Processes patients concurrently using a thread pool.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from config import MAX_WORKERS
from agents.patient_agent import fetch_all_patients
from agents.clinical_agent import fetch_clinical_data
from agents.extraction_agent import extract_wound_data
from agents.eligibility_agent import determine_eligibility

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()


def run_pipeline():
    logger.info("=" * 60)
    logger.info("STARTING WOUND BILLING PIPELINE")
    logger.info("=" * 60)

    # Step 1: Create schema
    logger.info("[Orchestrator] Setting up database schema...")
    db.create_schema()

    # Step 2: Fetch all patients
    logger.info("[Orchestrator] Fetching patients from all facilities...")
    all_patients = fetch_all_patients()

    if not all_patients:
        logger.error("[Orchestrator] No patients found — aborting")
        return

    logger.info(f"[Orchestrator] Processing {len(all_patients)} patients with {MAX_WORKERS} workers")

    # Step 3: Process patients in parallel
    stats = {"auto_accept": 0, "flag_for_review": 0, "reject": 0, "error": 0}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_patient, p): p for p in all_patients}

        for i, future in enumerate(as_completed(futures), 1):
            patient = futures[future]
            pid = patient.get("patient_id", "?")
            try:
                decision = future.result()
                if decision:
                    stats[decision] = stats.get(decision, 0) + 1
                else:
                    stats["error"] += 1
                if i % 10 == 0 or i == len(all_patients):
                    logger.info(
                        f"[Orchestrator] Progress: {i}/{len(all_patients)} | "
                        f"accept={stats['auto_accept']} review={stats['flag_for_review']} "
                        f"reject={stats['reject']} error={stats['error']}"
                    )
            except Exception as e:
                logger.error(f"[Orchestrator] Error processing {pid}: {e}")
                stats["error"] += 1

    # Save failed API calls to the retry bucket table
    from api_client import get_failed_calls
    failed_calls = get_failed_calls()
    if failed_calls:
        logger.warning(f"[Orchestrator] Saving {len(failed_calls)} failed API calls to retry bucket...")
        conn = db.get_connection()
        try:
            for fc in failed_calls:
                db.log_failed_api_call(
                    conn,
                    endpoint=fc["endpoint"],
                    url=fc["api_url"],
                    params=fc["params"],
                    patient_id=str(fc["params"].get("patient_id", "")),
                    error=fc["last_error"],
                )
            conn.commit()
            logger.info(f"[Orchestrator] {len(failed_calls)} calls saved to failed_api_calls table")
        except Exception as e:
            logger.error(f"[Orchestrator] Error saving failed calls: {e}")
        finally:
            conn.close()

    # Final summary
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  auto_accept    : {stats['auto_accept']}")
    logger.info(f"  flag_for_review: {stats['flag_for_review']}")
    logger.info(f"  reject         : {stats['reject']}")
    logger.info(f"  errors         : {stats['error']}")
    logger.info(f"  total          : {sum(stats.values())}")
    logger.info("=" * 60)

    return stats


def _process_patient(patient: dict) -> str | None:
    pid = patient.get("patient_id", "?")
    facility_id = patient.get("_facility_id")

    try:
        # Fetch clinical data
        clinical_data = fetch_clinical_data(patient)

        # Extract wound data from notes and assessments
        wound = extract_wound_data(
            clinical_data["notes"],
            clinical_data["assessments"],
        )

        # Determine eligibility routing
        eligibility = determine_eligibility(clinical_data, wound)

        # Write everything to the database (serialized per patient, lock for thread safety)
        with _db_lock:
            conn = db.get_connection()
            try:
                db.upsert_patient(conn, patient, facility_id)
                db.insert_diagnoses(conn, pid, clinical_data["diagnoses"])
                db.insert_coverage(conn, pid, clinical_data["coverage"])
                db.insert_notes(conn, clinical_data["patient_int_id"], pid, clinical_data["notes"])
                db.insert_assessments(conn, clinical_data["patient_int_id"], pid, clinical_data["assessments"])
                db.upsert_wound_extraction(conn, pid, wound)
                db.upsert_eligibility(conn, eligibility)
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"[Orchestrator] DB error for {pid}: {e}")
                raise
            finally:
                conn.close()

        logger.debug(f"[Orchestrator] {pid} -> {eligibility['routing_decision']}")
        return eligibility["routing_decision"]

    except Exception as e:
        logger.error(f"[Orchestrator] Failed to process {pid}: {e}")
        return None
