"""
Extractors — fetches raw data from the PCC API.
Handles all 5 endpoints with retry logic (20 retries, 10s delay on 429).

Endpoints:
  GET /pcc/patients?facility_id=101       uses facility_id
  GET /pcc/diagnoses?patient_id=FA-001    uses patient_id (string)
  GET /pcc/coverage?patient_id=FA-001     uses patient_id (string)
  GET /pcc/notes?patient_id=1             uses id (integer)
  GET /pcc/assessments?patient_id=1       uses id (integer)
"""
import requests
import time
import threading
import logging
import yaml
import os

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "pipeline_config.yaml")

def _load_config():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

_cfg = _load_config()
BASE_URL = _cfg["api"]["base_url"]
MAX_RETRIES = _cfg["api"]["max_retries"]
RETRY_DELAY = _cfg["api"]["retry_delay"]
FACILITIES = _cfg["api"]["facilities"]

from requests.adapters import HTTPAdapter
_session = requests.Session()
_session.headers.update({"Accept": "application/json"})
_adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30)
_session.mount("https://", _adapter)

_failed_calls: list[dict] = []
_failed_lock = threading.Lock()


def _get(endpoint: str, params: dict = None) -> dict | list | None:
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", RETRY_DELAY))
                logger.warning(f"429 {endpoint} attempt {attempt}/{MAX_RETRIES} wait {wait}s")
                time.sleep(wait)
            elif resp.status_code in (500, 502, 503, 504):
                logger.warning(f"{resp.status_code} on {endpoint} attempt {attempt}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
            else:
                logger.error(f"HTTP {resp.status_code} on {endpoint}")
                return None
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning(f"Network error {endpoint} attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"Unexpected {endpoint} attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    logger.error(f"FAILED after {MAX_RETRIES} retries: {endpoint} params={params}")
    with _failed_lock:
        _failed_calls.append({
            "api_url": f"{url}?{params}",
            "endpoint": endpoint,
            "params": params or {},
            "attempts": MAX_RETRIES,
            "last_error": f"Exhausted {MAX_RETRIES} retries",
        })
    return None


def _to_list(data, *keys) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    for k in keys:
        if k in data:
            return data[k]
    return []


def fetch_patients(facility_id: int, test_mode: bool = False) -> list[dict]:
    logger.info(f"[Extractor] Fetching patients facility={facility_id}")
    data = _get("/pcc/patients", params={"facility_id": facility_id})
    patients = _to_list(data, "patients", "data")
    if test_mode:
        patients = patients[:5]
    for p in patients:
        p["_facility_id"] = facility_id
    logger.info(f"[Extractor] {len(patients)} patients from facility {facility_id}")
    return patients


def fetch_all_patients(test_mode: bool = False) -> list[dict]:
    all_patients = []
    for fid in FACILITIES:
        all_patients.extend(fetch_patients(fid, test_mode=test_mode))
    logger.info(f"[Extractor] Total: {len(all_patients)} patients")
    return all_patients


def fetch_diagnoses(patient_id: str) -> list:
    data = _get("/pcc/diagnoses", params={"patient_id": patient_id})
    return _to_list(data, "diagnoses", "data")


def fetch_coverage(patient_id: str) -> list:
    data = _get("/pcc/coverage", params={"patient_id": patient_id})
    return _to_list(data, "coverage", "data")


def fetch_notes(patient_int_id: int) -> list:
    data = _get("/pcc/notes", params={"patient_id": patient_int_id})
    return _to_list(data, "notes", "data")


def fetch_assessments(patient_int_id: int) -> list:
    data = _get("/pcc/assessments", params={"patient_id": patient_int_id})
    return _to_list(data, "assessments", "data")


def fetch_clinical_data(patient: dict) -> dict:
    patient_id = patient.get("patient_id")     # string: FA-001
    patient_int_id = patient.get("id")          # integer: 1
    logger.info(f"[Extractor] Clinical data for {patient_id} (id={patient_int_id})")
    return {
        "patient": patient,
        "patient_id": patient_id,
        "patient_int_id": patient_int_id,
        "facility_id": patient.get("_facility_id"),
        "diagnoses": fetch_diagnoses(patient_id),
        "coverage": fetch_coverage(patient_id),
        "notes": fetch_notes(patient_int_id),
        "assessments": fetch_assessments(patient_int_id),
    }


def get_failed_calls() -> list[dict]:
    with _failed_lock:
        return list(_failed_calls)
