"""
Clinical Agent — fetches diagnoses, coverage, notes, assessments for a patient.
Uses string patient_id for diagnoses/coverage, integer id for notes/assessments.
"""
import logging
from api_client import curl_get

logger = logging.getLogger(__name__)


def fetch_clinical_data(patient: dict) -> dict:
    patient_id = patient.get("patient_id")    # string e.g. FA-001
    patient_int_id = patient.get("id")        # integer e.g. 1
    facility_id = patient.get("_facility_id")

    logger.info(f"[ClinicalAgent] Processing {patient_id} (int_id={patient_int_id})")

    diagnoses = _fetch_diagnoses(patient_id)
    coverage = _fetch_coverage(patient_id)
    notes = _fetch_notes(patient_int_id)
    assessments = _fetch_assessments(patient_int_id)

    return {
        "patient": patient,
        "patient_id": patient_id,
        "patient_int_id": patient_int_id,
        "facility_id": facility_id,
        "diagnoses": diagnoses or [],
        "coverage": coverage or [],
        "notes": notes or [],
        "assessments": assessments or [],
    }


def _fetch_diagnoses(patient_id: str) -> list:
    if not patient_id:
        return []
    data = curl_get("/pcc/diagnoses", params={"patient_id": patient_id})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("diagnoses", data.get("data", []))


def _fetch_coverage(patient_id: str) -> list:
    if not patient_id:
        return []
    data = curl_get("/pcc/coverage", params={"patient_id": patient_id})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("coverage", data.get("data", []))


def _fetch_notes(patient_int_id: int) -> list:
    if not patient_int_id:
        return []
    data = curl_get("/pcc/notes", params={"patient_id": patient_int_id})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("notes", data.get("data", []))


def _fetch_assessments(patient_int_id: int) -> list:
    if not patient_int_id:
        return []
    data = curl_get("/pcc/assessments", params={"patient_id": patient_int_id})
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("assessments", data.get("data", []))
