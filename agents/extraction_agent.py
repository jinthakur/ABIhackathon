"""
Extraction Agent — extracts wound data from notes and assessments.
Handles SOAP, Prose, Multi-wound (Envive), and structured assessment formats.
"""
import re
import json
import logging
from config import WOUND_ICD10_PREFIXES

logger = logging.getLogger(__name__)

WOUND_TYPE_PATTERNS = [
    (r"\bpressure\s*(ulcer|injury|sore|wound)\b", "pressure ulcer"),
    (r"\bPU\b|\bPI\b|\bDTPI\b", "pressure ulcer"),
    (r"\bdiabetic\s*foot\s*ulcer\b", "diabetic foot ulcer"),
    (r"\bDFU\b", "diabetic foot ulcer"),
    (r"\bvenous\s*(stasis\s*)?(ulcer|wound)\b", "venous stasis ulcer"),
    (r"\bVSU\b", "venous stasis ulcer"),
    (r"\barterial\s*(ulcer|wound)\b", "arterial ulcer"),
    (r"\bsurgical\s*site\s*(infection|wound)?\b", "surgical site infection"),
    (r"\bSSI\b", "surgical site infection"),
    (r"\babscess\b", "abscess"),
    (r"\bburn\b", "burn"),
    (r"\bulcer\b", "ulcer"),
]

STAGE_PATTERNS = [
    (r"\bstage\s*4\b|\bstage\s*iv\b|\bstage\s*IV\b", "Stage 4"),
    (r"\bstage\s*3\b|\bstage\s*iii\b|\bstage\s*III\b", "Stage 3"),
    (r"\bstage\s*2\b|\bstage\s*ii\b|\bstage\s*II\b", "Stage 2"),
    (r"\bstage\s*1\b|\bstage\s*i\b", "Stage 1"),
    (r"\bunstageable\b", "Unstageable"),
    (r"\bdeep\s*tissue\s*(injury|pressure)?\b|\bDTI\b|\bDTPI\b", "Deep Tissue Injury"),
]

DRAINAGE_PATTERNS = [
    (r"\bheavy\b|\bcopious\b|\blarge\b", "heavy"),
    (r"\bmoderate\b", "moderate"),
    (r"\blight\b|\bminimal\b|\bsmall\b|\bscant\b|\bslight\b", "light"),
    (r"\bno\s*drainage\b|\bnone\b|\bdry\b|\bnon.?draining\b", "none"),
]

MEASUREMENT_PATTERNS = [
    # "Measures 2.9 cm x 2.8 cm"  or  "Meas 4.2x3.1x1.5cm"
    r"[Mm]easures?\s*(\d+\.?\d*)\s*cm\s*[xX×]\s*(\d+\.?\d*)\s*cm(?:\s*[xX×]\s*(\d+\.?\d*)\s*cm)?",
    r"[Mm]eas(?:urement)?[\s:]*(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*(?:[xX×]\s*(\d+\.?\d*))?\s*cm",
    # "4.2cm x 3.1cm x 1.5cm"
    r"(\d+\.?\d*)\s*cm\s*[xX×]\s*(\d+\.?\d*)\s*cm(?:\s*[xX×]\s*(\d+\.?\d*)\s*cm)?",
    # "4.2 x 3.1 x 1.5 cm"
    r"(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*cm",
    # "Length: 4.2 cm Width: 3.1 cm Depth: 1.5 cm"
    r"[Ll]ength[\s:]+(\d+\.?\d*)\s*cm.*?[Ww]idth[\s:]+(\d+\.?\d*)\s*cm.*?[Dd]epth[\s:]+(\d+\.?\d*)\s*cm",
    # "L:4.2 W:3.1 D:1.5"
    r"L[\s:]+(\d+\.?\d*)\s+[Ww][\s:]+(\d+\.?\d*)\s+[Dd][\s:]+(\d+\.?\d*)",
]

LOCATION_KEYWORDS = [
    "sacrum", "sacral", "coccyx", "right hip", "left hip", "hip",
    "right heel", "left heel", "heel", "trochanter", "ischium", "ischial",
    "elbow", "shoulder", "right ankle", "left ankle", "ankle",
    "malleolus", "shin", "calf", "lower leg", "right leg", "left leg",
    "right foot", "left foot", "foot", "toe", "finger", "hand", "arm",
    "abdomen", "back", "lower back", "buttock", "gluteal",
    "lateral", "medial", "dorsal", "plantar", "hallux",
    "metatarsal", "forefoot", "midfoot",
]


def extract_wound_data(notes: list, assessments: list) -> dict | None:
    """Extract wound data from all notes and assessments, return best result."""
    candidates = []

    for note in notes:
        text = note.get("note_text", "")
        if text:
            result = _extract_from_text(text, source_type="note")
            if result:
                candidates.append(result)

    for assessment in assessments:
        # Try structured fields from raw_json first
        raw_json_str = assessment.get("raw_json", "")
        if raw_json_str:
            try:
                raw_json = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
                text = _flatten_assessment_json(raw_json)
                if text:
                    result = _extract_from_text(text, source_type="assessment")
                    if result:
                        candidates.append(result)
            except (json.JSONDecodeError, TypeError):
                pass

        # Also try any top-level text fields
        flat_text = " ".join(
            str(v) for k, v in assessment.items()
            if isinstance(v, str) and k not in ("raw_json", "org_id")
        )
        if flat_text:
            result = _extract_from_text(flat_text, source_type="assessment")
            if result:
                candidates.append(result)

    if not candidates:
        return None

    def score(c):
        return sum([
            bool(c.get("wound_type")),
            bool(c.get("wound_stage")),
            bool(c.get("length_cm")),
            bool(c.get("width_cm")),
            bool(c.get("depth_cm")),
            bool(c.get("drainage_amount")),
            bool(c.get("location")),
        ])

    best = max(candidates, key=score)
    s = score(best)
    best["confidence"] = "high" if s >= 6 else "medium" if s >= 3 else "low"

    return best


def _flatten_assessment_json(raw_json: dict) -> str:
    """Extract all text from sections/questions in the assessment JSON."""
    parts = []
    sections = raw_json.get("sections", [])
    for section in sections:
        for q in section.get("questions", []):
            answer = q.get("answer", "")
            if answer:
                parts.append(str(answer))
    return " ".join(parts)


def _extract_from_text(text: str, source_type: str = "note") -> dict | None:
    wound_type = None
    for pattern, label in WOUND_TYPE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            wound_type = label
            break

    stage = None
    for pattern, label in STAGE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            stage = label
            break

    length_cm = width_cm = depth_cm = None
    for pattern in MEASUREMENT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            groups = [g for g in m.groups() if g is not None]
            try:
                if len(groups) >= 3:
                    length_cm, width_cm, depth_cm = float(groups[0]), float(groups[1]), float(groups[2])
                elif len(groups) == 2:
                    length_cm, width_cm = float(groups[0]), float(groups[1])
                break
            except (ValueError, IndexError):
                continue

    drainage = None
    for pattern, label in DRAINAGE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            drainage = label
            break

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
        "wound_type": wound_type,
        "wound_stage": stage,
        "location": location,
        "length_cm": length_cm,
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "drainage_amount": drainage,
        "source_type": source_type,
        "raw_text": text[:500],
    }


def is_wound_icd10(code: str) -> bool:
    if not code:
        return False
    code = code.upper().strip()
    for prefix in WOUND_ICD10_PREFIXES:
        if code.startswith(prefix.upper()):
            return True
    return False


def has_wound_diagnosis(diagnoses: list) -> bool:
    return any(is_wound_icd10(d.get("icd10_code", "")) for d in diagnoses)
