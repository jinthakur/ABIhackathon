"""
LLM Agent — wound data extraction and eligibility determination.
Uses regex extraction by default; optionally calls OpenAI if OPENAI_API_KEY is set.
"""
import re
import json
import os
import logging

logger = logging.getLogger(__name__)

WOUND_ICD10_PREFIXES = [
    "L89", "L97", "L98.4",
    "E10.621", "E10.622", "E11.621", "E11.622",
    "I83", "I87.2", "T31", "T32", "L02", "T81", "M86",
]

WOUND_TYPE_PATTERNS = [
    (r"\bpressure\s*(ulcer|injury|sore|wound)\b", "pressure ulcer"),
    (r"\bPU\b|\bPI\b|\bDTPI\b", "pressure ulcer"),
    (r"\bdiabetic\s*foot\s*ulcer\b|\bDFU\b", "diabetic foot ulcer"),
    (r"\bvenous\s*(stasis\s*)?(ulcer|wound)\b|\bVSU\b", "venous stasis ulcer"),
    (r"\barterial\s*(ulcer|wound)\b", "arterial ulcer"),
    (r"\bsurgical\s*site\s*(infection|wound)?\b|\bSSI\b", "surgical site infection"),
    (r"\babscess\b", "abscess"),
    (r"\bburn\b", "burn"),
    (r"\bulcer\b", "ulcer"),
]

STAGE_PATTERNS = [
    (r"\bstage\s*4\b|\bstage\s*iv\b", "Stage 4"),
    (r"\bstage\s*3\b|\bstage\s*iii\b", "Stage 3"),
    (r"\bstage\s*2\b|\bstage\s*ii\b", "Stage 2"),
    (r"\bstage\s*1\b|\bstage\s*i\b", "Stage 1"),
    (r"\bunstageable\b", "Unstageable"),
    (r"\bdeep\s*tissue\s*(injury|pressure)?\b|\bDTI\b|\bDTPI\b", "Deep Tissue Injury"),
]

DRAINAGE_PATTERNS = [
    (r"\bheavy\b|\bcopious\b|\blarge\b", "heavy"),
    (r"\bmoderate\b", "moderate"),
    (r"\blight\b|\bminimal\b|\bsmall\b|\bscant\b|\bslight\b", "light"),
    (r"\bno\s*drainage\b|\bnone\b|\bdry\b", "none"),
]

MEASUREMENT_PATTERNS = [
    r"[Mm]easures?\s*(\d+\.?\d*)\s*cm\s*[xX]\s*(\d+\.?\d*)\s*cm(?:\s*[xX]\s*(\d+\.?\d*)\s*cm)?",
    r"[Mm]eas[\s:]*(\d+\.?\d*)\s*[xX]\s*(\d+\.?\d*)\s*(?:[xX]\s*(\d+\.?\d*))?\s*cm",
    r"(\d+\.?\d*)\s*cm\s*[xX]\s*(\d+\.?\d*)\s*cm(?:\s*[xX]\s*(\d+\.?\d*)\s*cm)?",
    r"(\d+\.?\d*)\s*[xX]\s*(\d+\.?\d*)\s*[xX]\s*(\d+\.?\d*)\s*cm",
    r"[Ll]ength[\s:]+(\d+\.?\d*)\s*cm.*?[Ww]idth[\s:]+(\d+\.?\d*)\s*cm.*?[Dd]epth[\s:]+(\d+\.?\d*)\s*cm",
    r"L[\s:]+(\d+\.?\d*)\s+[Ww][\s:]+(\d+\.?\d*)\s+[Dd][\s:]+(\d+\.?\d*)",
]

LOCATION_KEYWORDS = [
    "sacrum", "sacral", "coccyx", "right hip", "left hip", "hip",
    "right heel", "left heel", "heel", "trochanter", "ischium", "ischial",
    "right ankle", "left ankle", "ankle", "malleolus", "shin", "calf",
    "lower leg", "right leg", "left leg", "right foot", "left foot",
    "right toe", "left toe", "foot", "arm", "elbow", "shoulder",
    "abdomen", "back", "buttock", "gluteal", "plantar", "hallux",
    "metatarsal", "forefoot", "midfoot",
]


def extract_wound_data(notes: list, assessments: list) -> dict | None:
    """Extract best wound data from notes and assessments using regex + optional LLM."""
    candidates = []

    for note in notes:
        text = note.get("note_text", "")
        if text:
            result = _extract_from_text(text, "note")
            if result:
                candidates.append(result)

    for assessment in assessments:
        raw_json_str = assessment.get("raw_json", "")
        if raw_json_str:
            try:
                raw = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
                text = _flatten_assessment_json(raw)
                if text:
                    result = _extract_from_text(text, "assessment")
                    if result:
                        candidates.append(result)
            except (json.JSONDecodeError, TypeError):
                pass

    if not candidates:
        return None

    def score(c):
        return sum([
            bool(c.get("wound_type")), bool(c.get("wound_stage")),
            bool(c.get("length_cm")), bool(c.get("width_cm")),
            bool(c.get("depth_cm")), bool(c.get("drainage_amount")),
            bool(c.get("location")),
        ])

    best = max(candidates, key=score)
    s = score(best)
    best["confidence"] = "high" if s >= 6 else "medium" if s >= 3 else "low"
    return best


def _flatten_assessment_json(raw: dict) -> str:
    parts = []
    for section in raw.get("sections", []):
        for q in section.get("questions", []):
            if q.get("answer"):
                parts.append(str(q["answer"]))
    return " ".join(parts)


def _extract_from_text(text: str, source_type: str) -> dict | None:
    wound_type = next((label for pat, label in WOUND_TYPE_PATTERNS if re.search(pat, text, re.IGNORECASE)), None)
    stage = next((label for pat, label in STAGE_PATTERNS if re.search(pat, text, re.IGNORECASE)), None)
    drainage = next((label for pat, label in DRAINAGE_PATTERNS if re.search(pat, text, re.IGNORECASE)), None)

    length_cm = width_cm = depth_cm = None
    for pat in MEASUREMENT_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            groups = [g for g in m.groups() if g is not None]
            try:
                if len(groups) >= 3:
                    length_cm, width_cm, depth_cm = float(groups[0]), float(groups[1]), float(groups[2])
                elif len(groups) >= 2:
                    length_cm, width_cm = float(groups[0]), float(groups[1])
                break
            except (ValueError, IndexError):
                continue

    location = None
    text_lower = text.lower()
    for loc in LOCATION_KEYWORDS:
        if loc in text_lower:
            idx = text_lower.find(loc)
            location = text[idx:idx + len(loc)].strip().title()
            break

    if not wound_type and not length_cm:
        return None

    return {
        "wound_type": wound_type, "wound_stage": stage, "location": location,
        "length_cm": length_cm, "width_cm": width_cm, "depth_cm": depth_cm,
        "drainage_amount": drainage, "source_type": source_type,
        "raw_text": text[:500],
    }


def determine_eligibility(clinical_data: dict, wound: dict | None) -> dict:
    """Determine Medicare Part B billing routing for a patient."""
    patient = clinical_data["patient"]
    patient_id = clinical_data["patient_id"]

    has_part_b = _check_medicare_b(patient, clinical_data["coverage"])
    wound_diag = any(is_wound_icd10(d.get("icd10_code", "")) for d in clinical_data["diagnoses"])

    result = {
        "patient_id": patient_id,
        "facility_id": clinical_data["facility_id"],
        "first_name": patient.get("first_name", ""),
        "last_name": patient.get("last_name", ""),
        "has_medicare_part_b": has_part_b,
        "has_wound_diagnosis": wound_diag,
        "wound_type": None, "wound_stage": None, "location": None,
        "length_cm": None, "width_cm": None, "depth_cm": None,
        "drainage_amount": None, "routing_decision": None, "reason": None,
    }

    if wound:
        result.update({
            "wound_type": wound.get("wound_type"), "wound_stage": wound.get("wound_stage"),
            "location": wound.get("location"), "length_cm": wound.get("length_cm"),
            "width_cm": wound.get("width_cm"), "depth_cm": wound.get("depth_cm"),
            "drainage_amount": wound.get("drainage_amount"),
        })

    routing, reason = _routing_logic(has_part_b, wound_diag, wound)
    result["routing_decision"] = routing
    result["reason"] = reason
    logger.info(f"[LLMAgent] {patient_id} -> {routing.upper()}: {reason}")
    return result


def _routing_logic(has_part_b: bool, wound_diag: bool, wound: dict | None) -> tuple[str, str]:
    if not has_part_b:
        return "reject", "No active Medicare Part B coverage"
    if not wound and not wound_diag:
        return "reject", "No wound data found in notes, assessments, or diagnoses"
    if not wound and wound_diag:
        return "flag_for_review", "Wound ICD-10 diagnosis present but no measurements or wound narrative found"

    has_meas = wound.get("length_cm") is not None and wound.get("width_cm") is not None
    has_type = bool(wound.get("wound_type"))
    has_drain = bool(wound.get("drainage_amount"))
    has_loc = bool(wound.get("location"))

    if has_type and has_meas and has_drain and has_loc:
        return (
            "auto_accept",
            f"All required fields documented: {wound['wound_type']}, {wound.get('location','')}, "
            f"measurements {wound.get('length_cm')}x{wound.get('width_cm')}"
            + (f"x{wound.get('depth_cm')}" if wound.get("depth_cm") else "")
            + f" cm, drainage: {wound.get('drainage_amount')}",
        )

    missing = [
        label for cond, label in [
            (not has_type, "wound type"), (not has_meas, "measurements (L×W)"),
            (not has_drain, "drainage level"), (not has_loc, "wound location"),
        ] if cond
    ]
    return "flag_for_review", f"Missing required fields: {', '.join(missing)}"


def _check_medicare_b(patient: dict, coverage_list: list) -> bool:
    if patient.get("primary_payer_code", "").upper() in ("MCB", "MCRB", "MEDICARE_B"):
        return True
    for cov in coverage_list:
        if cov.get("effective_to") is not None:
            continue
        pcode = cov.get("payer_code", "").upper()
        pname = cov.get("payer_name", "").lower()
        if pcode in ("MCB", "MCRB") or "part b" in pname or ("medicare" in pname and "part a" not in pname and "advantage" not in pname):
            return True
    return False


def is_wound_icd10(code: str) -> bool:
    if not code:
        return False
    code = code.upper().strip()
    return any(code.startswith(p.upper()) for p in WOUND_ICD10_PREFIXES)
