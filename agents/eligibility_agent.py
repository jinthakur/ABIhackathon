"""
Eligibility Agent — determines Medicare Part B wound billing routing.
  auto_accept    : all required fields present, safe to bill
  flag_for_review: incomplete or ambiguous data
  reject         : not eligible or no extractable wound data
"""
import logging

logger = logging.getLogger(__name__)


def has_medicare_part_b_coverage(patient: dict, coverage_list: list) -> bool:
    # Fast check: primary_payer_code on the patient record
    if patient.get("primary_payer_code", "").upper() in ("MCB", "MCRB", "MEDICARE_B"):
        return True

    # Full check: walk coverage records
    for cov in coverage_list:
        payer_code = cov.get("payer_code", "").upper()
        payer_name = cov.get("payer_name", "").lower()
        payer_type = cov.get("payer_type", "").lower()
        is_active = cov.get("effective_to") is None  # null effective_to = currently active

        if not is_active:
            continue

        if payer_code in ("MCB", "MCRB", "MEDICARE_B"):
            return True
        if "part b" in payer_name or "medicare b" in payer_name:
            return True
        if "medicare" in payer_type and "part b" in payer_name:
            return True

    return False


def determine_eligibility(clinical_data: dict, wound: dict | None) -> dict:
    patient_id = clinical_data["patient_id"]
    patient = clinical_data["patient"]
    facility_id = clinical_data["facility_id"]

    result = {
        "patient_id": patient_id,
        "facility_id": facility_id,
        "first_name": patient.get("first_name", ""),
        "last_name": patient.get("last_name", ""),
        "has_medicare_part_b": False,
        "has_wound_diagnosis": False,
        "wound_type": None,
        "wound_stage": None,
        "location": None,
        "length_cm": None,
        "width_cm": None,
        "depth_cm": None,
        "drainage_amount": None,
        "routing_decision": None,
        "reason": None,
    }

    has_part_b = has_medicare_part_b_coverage(patient, clinical_data["coverage"])
    wound_diag = _has_wound_icd10(clinical_data["diagnoses"])

    result["has_medicare_part_b"] = has_part_b
    result["has_wound_diagnosis"] = wound_diag

    if wound:
        result.update({
            "wound_type": wound.get("wound_type"),
            "wound_stage": wound.get("wound_stage"),
            "location": wound.get("location"),
            "length_cm": wound.get("length_cm"),
            "width_cm": wound.get("width_cm"),
            "depth_cm": wound.get("depth_cm"),
            "drainage_amount": wound.get("drainage_amount"),
        })

    routing, reason = _routing_decision(has_part_b, wound_diag, wound)
    result["routing_decision"] = routing
    result["reason"] = reason

    logger.info(f"[EligibilityAgent] {patient_id} -> {routing.upper()}: {reason}")
    return result


def _routing_decision(has_part_b: bool, wound_diag: bool, wound: dict | None) -> tuple[str, str]:
    if not has_part_b:
        return "reject", "No active Medicare Part B coverage"

    if not wound and not wound_diag:
        return "reject", "No wound data found in notes, assessments, or diagnoses"

    if not wound and wound_diag:
        return (
            "flag_for_review",
            "Wound ICD-10 diagnosis present but no measurements or wound narrative found",
        )

    has_measurements = (
        wound.get("length_cm") is not None
        and wound.get("width_cm") is not None
    )
    has_type = bool(wound.get("wound_type"))
    has_drainage = bool(wound.get("drainage_amount"))
    has_location = bool(wound.get("location"))

    if has_type and has_measurements and has_drainage and has_location:
        return (
            "auto_accept",
            (
                f"All required fields documented: {wound['wound_type']}, "
                f"{wound.get('location', '')}, "
                f"measurements {wound.get('length_cm')}x{wound.get('width_cm')}"
                + (f"x{wound.get('depth_cm')}" if wound.get("depth_cm") else "")
                + f" cm, drainage: {wound.get('drainage_amount')}"
            ),
        )

    missing = []
    if not has_type:
        missing.append("wound type")
    if not has_measurements:
        missing.append("measurements (L×W)")
    if not has_drainage:
        missing.append("drainage level")
    if not has_location:
        missing.append("wound location")

    return "flag_for_review", f"Missing required fields: {', '.join(missing)}"


def _has_wound_icd10(diagnoses: list) -> bool:
    from agents.extraction_agent import is_wound_icd10
    return any(is_wound_icd10(d.get("icd10_code", "")) for d in diagnoses)
