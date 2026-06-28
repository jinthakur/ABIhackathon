"""
Loaders — writes data to SQLite (or optionally Supabase).
All writes use upsert: INSERT if patient_id not present, UPDATE if it is.
"""
import sqlite3
import json
import os
import logging
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "pipeline_config.yaml")
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", _cfg["database"]["path"])


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
    with open(schema_path) as f:
        schema_sql = f.read()
    conn = get_connection()
    try:
        conn.executescript(schema_sql)
        conn.commit()
        logger.info(f"Schema ready: {DB_PATH}")
    finally:
        conn.close()


def upsert_patient(conn: sqlite3.Connection, patient: dict, facility_id: int):
    conn.execute("""
        INSERT INTO patients (id, patient_id, facility_id, first_name, last_name, date_of_birth, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            raw_data = excluded.raw_data
    """, (
        patient.get("id"),
        patient.get("patient_id"),
        facility_id,
        patient.get("first_name"),
        patient.get("last_name"),
        patient.get("birth_date"),
        json.dumps(patient),
    ))


def insert_diagnoses(conn: sqlite3.Connection, patient_id: str, diagnoses: list):
    from src.llm_agent import is_wound_icd10
    conn.execute("DELETE FROM diagnoses WHERE patient_id = ?", (patient_id,))
    for d in diagnoses:
        code = d.get("icd10_code", "")
        conn.execute("""
            INSERT INTO diagnoses (patient_id, icd10_code, description, is_wound_related, raw_data)
            VALUES (?, ?, ?, ?, ?)
        """, (patient_id, code, d.get("icd10_description"), 1 if is_wound_icd10(code) else 0, json.dumps(d)))


def insert_coverage(conn: sqlite3.Connection, patient_id: str, coverage_list: list):
    conn.execute("DELETE FROM coverage WHERE patient_id = ?", (patient_id,))
    for c in coverage_list:
        pcode = c.get("payer_code", "")
        pname = c.get("payer_name", "")
        is_b = 1 if _is_medicare_b(pcode, pname) else 0
        is_active = 1 if c.get("effective_to") is None else 0
        conn.execute("""
            INSERT INTO coverage (patient_id, payer_name, plan_type, is_active, is_medicare_part_b, raw_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_id, pname, c.get("payer_type"), is_active, is_b, json.dumps(c)))


def insert_notes(conn: sqlite3.Connection, patient_int_id: int, patient_id: str, notes: list):
    conn.execute("DELETE FROM clinical_notes WHERE patient_int_id = ?", (patient_int_id,))
    for n in notes:
        conn.execute("""
            INSERT INTO clinical_notes (patient_int_id, patient_id, note_text, note_date, note_type, raw_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_int_id, patient_id, n.get("note_text", ""), n.get("effective_date"), n.get("note_type"), json.dumps(n)))


def insert_assessments(conn: sqlite3.Connection, patient_int_id: int, patient_id: str, assessments: list):
    conn.execute("DELETE FROM assessments WHERE patient_int_id = ?", (patient_int_id,))
    for a in assessments:
        conn.execute("""
            INSERT INTO assessments (patient_int_id, patient_id, assessment_date, raw_data)
            VALUES (?, ?, ?, ?)
        """, (patient_int_id, patient_id, a.get("assessment_date"), json.dumps(a)))


def upsert_wound_extraction(conn: sqlite3.Connection, patient_id: str, wound: dict | None):
    conn.execute("DELETE FROM wound_extractions WHERE patient_id = ?", (patient_id,))
    if wound:
        conn.execute("""
            INSERT INTO wound_extractions
            (patient_id, wound_type, wound_stage, location, length_cm, width_cm, depth_cm,
             drainage_amount, source_type, confidence, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_id, wound.get("wound_type"), wound.get("wound_stage"), wound.get("location"),
            wound.get("length_cm"), wound.get("width_cm"), wound.get("depth_cm"),
            wound.get("drainage_amount"), wound.get("source_type"), wound.get("confidence"),
            wound.get("raw_text"),
        ))


def upsert_eligibility(conn: sqlite3.Connection, result: dict):
    """Upsert eligibility result — insert if new patient_id, update if existing."""
    conn.execute("""
        INSERT INTO eligibility_results
        (patient_id, facility_id, first_name, last_name, has_medicare_part_b, has_wound_diagnosis,
         wound_type, wound_stage, location, length_cm, width_cm, depth_cm,
         drainage_amount, routing_decision, reason, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(patient_id) DO UPDATE SET
            has_medicare_part_b = excluded.has_medicare_part_b,
            has_wound_diagnosis = excluded.has_wound_diagnosis,
            wound_type = excluded.wound_type,
            wound_stage = excluded.wound_stage,
            location = excluded.location,
            length_cm = excluded.length_cm,
            width_cm = excluded.width_cm,
            depth_cm = excluded.depth_cm,
            drainage_amount = excluded.drainage_amount,
            routing_decision = excluded.routing_decision,
            reason = excluded.reason,
            updated_at = datetime('now')
    """, (
        result["patient_id"], result.get("facility_id"), result.get("first_name"), result.get("last_name"),
        1 if result.get("has_medicare_part_b") else 0, 1 if result.get("has_wound_diagnosis") else 0,
        result.get("wound_type"), result.get("wound_stage"), result.get("location"),
        result.get("length_cm"), result.get("width_cm"), result.get("depth_cm"),
        result.get("drainage_amount"), result.get("routing_decision"), result.get("reason"),
    ))


def log_failed_api_call(conn: sqlite3.Connection, endpoint: str, url: str, params: dict, patient_id: str, error: str):
    conn.execute("""
        INSERT INTO failed_api_calls (api_url, endpoint, params, patient_id, attempts, last_error, status)
        VALUES (?, ?, ?, ?, 20, ?, 'pending')
    """, (url, endpoint, json.dumps(params or {}), patient_id, error))


def get_table_counts() -> dict:
    conn = get_connection()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    counts = {}
    for (t,) in tables:
        counts[t] = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
    conn.close()
    return counts


def _is_medicare_b(payer_code: str, payer_name: str) -> bool:
    if payer_code.upper() in ("MCB", "MCRB", "MEDICARE_B"):
        return True
    name = payer_name.lower()
    return "part b" in name or ("medicare" in name and "part a" not in name and "advantage" not in name)
