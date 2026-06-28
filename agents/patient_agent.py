"""
Patient Agent — fetches all patients from all facilities.
"""
import logging
from api_client import curl_get
from config import FACILITIES, TEST_MODE

logger = logging.getLogger(__name__)


def fetch_patients(facility_id: int) -> list[dict]:
    logger.info(f"[PatientAgent] Fetching patients for facility {facility_id}")
    data = curl_get("/pcc/patients", params={"facility_id": facility_id})

    if data is None:
        logger.error(f"[PatientAgent] No data returned for facility {facility_id}")
        return []

    patients = data if isinstance(data, list) else data.get("patients", data.get("data", []))

    if TEST_MODE:
        patients = patients[:5]
        logger.info(f"[PatientAgent] TEST_MODE: limiting to 5 patients for facility {facility_id}")

    logger.info(f"[PatientAgent] Got {len(patients)} patients for facility {facility_id}")
    return patients


def fetch_all_patients() -> list[dict]:
    all_patients = []
    for fid in FACILITIES:
        patients = fetch_patients(fid)
        for p in patients:
            p["_facility_id"] = fid
        all_patients.extend(patients)

    logger.info(f"[PatientAgent] Total patients across all facilities: {len(all_patients)}")
    return all_patients
