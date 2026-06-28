"""
Database layer — uses SQLite locally (always works).
Port 5432 to Supabase is often blocked on restricted networks.
Run upload_to_supabase.py after getting your Supabase service role key.
"""
import sqlite3
import json
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "wound_pipeline.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema():
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY,
                patient_id TEXT UNIQUE NOT NULL,
                facility_id INTEGER,
                first_name TEXT,
                last_name TEXT,
                date_of_birth TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS diagnoses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                icd10_code TEXT,
                description TEXT,
                is_wound_related INTEGER DEFAULT 0,
                raw_data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                payer_name TEXT,
                plan_type TEXT,
                is_active INTEGER DEFAULT 1,
                is_medicare_part_b INTEGER DEFAULT 0,
                raw_data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS clinical_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_int_id INTEGER,
                patient_id TEXT,
                note_text TEXT,
                note_date TEXT,
                note_type TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_int_id INTEGER,
                patient_id TEXT,
                assessment_date TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wound_extractions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                wound_type TEXT,
                wound_stage TEXT,
                location TEXT,
                length_cm REAL,
                width_cm REAL,
                depth_cm REAL,
                drainage_amount TEXT,
                source_type TEXT,
                confidence TEXT,
                raw_text TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS eligibility_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT UNIQUE NOT NULL,
                facility_id INTEGER,
                first_name TEXT,
                last_name TEXT,
                has_medicare_part_b INTEGER DEFAULT 0,
                has_wound_diagnosis INTEGER DEFAULT 0,
                wound_type TEXT,
                wound_stage TEXT,
                location TEXT,
                length_cm REAL,
                width_cm REAL,
                depth_cm REAL,
                drainage_amount TEXT,
                routing_decision TEXT,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS failed_api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_url TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                params TEXT,
                patient_id TEXT,
                attempts INTEGER DEFAULT 20,
                last_error TEXT,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                last_retried_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_failed_status ON failed_api_calls(status);
            CREATE INDEX IF NOT EXISTS idx_failed_endpoint ON failed_api_calls(endpoint);
            CREATE INDEX IF NOT EXISTS idx_eligibility_routing ON eligibility_results(routing_decision);
            CREATE INDEX IF NOT EXISTS idx_eligibility_facility ON eligibility_results(facility_id);
            CREATE INDEX IF NOT EXISTS idx_diagnoses_patient ON diagnoses(patient_id);
            CREATE INDEX IF NOT EXISTS idx_coverage_patient ON coverage(patient_id);
            CREATE INDEX IF NOT EXISTS idx_notes_patient ON clinical_notes(patient_id);
            CREATE INDEX IF NOT EXISTS idx_assessments_patient ON assessments(patient_id);
        """)
        conn.commit()
        logger.info(f"Schema ready at {DB_PATH}")
    finally:
        conn.close()


def upsert_patient(conn, patient: dict, facility_id: int):
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


def insert_diagnoses(conn, patient_id: str, diagnoses: list):
    from agents.extraction_agent import is_wound_icd10
    conn.execute("DELETE FROM diagnoses WHERE patient_id = ?", (patient_id,))
    for diag in diagnoses:
        code = diag.get("icd10_code", "")
        conn.execute("""
            INSERT INTO diagnoses (patient_id, icd10_code, description, is_wound_related, raw_data)
            VALUES (?, ?, ?, ?, ?)
        """, (
            patient_id,
            code,
            diag.get("icd10_description"),
            1 if is_wound_icd10(code) else 0,
            json.dumps(diag),
        ))


def insert_coverage(conn, patient_id: str, coverage_list: list):
    conn.execute("DELETE FROM coverage WHERE patient_id = ?", (patient_id,))
    for cov in coverage_list:
        payer_code = cov.get("payer_code", "")
        payer_name = cov.get("payer_name", "")
        is_b = 1 if _is_medicare_part_b(payer_code, payer_name) else 0
        is_active = 1 if cov.get("effective_to") is None else 0
        conn.execute("""
            INSERT INTO coverage (patient_id, payer_name, plan_type, is_active, is_medicare_part_b, raw_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            patient_id,
            payer_name,
            cov.get("payer_type"),
            is_active,
            is_b,
            json.dumps(cov),
        ))


def insert_notes(conn, patient_int_id: int, patient_id: str, notes: list):
    conn.execute("DELETE FROM clinical_notes WHERE patient_int_id = ?", (patient_int_id,))
    for note in notes:
        conn.execute("""
            INSERT INTO clinical_notes (patient_int_id, patient_id, note_text, note_date, note_type, raw_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            patient_int_id,
            patient_id,
            note.get("note_text", ""),
            note.get("effective_date"),
            note.get("note_type"),
            json.dumps(note),
        ))


def insert_assessments(conn, patient_int_id: int, patient_id: str, assessments: list):
    conn.execute("DELETE FROM assessments WHERE patient_int_id = ?", (patient_int_id,))
    for a in assessments:
        conn.execute("""
            INSERT INTO assessments (patient_int_id, patient_id, assessment_date, raw_data)
            VALUES (?, ?, ?, ?)
        """, (
            patient_int_id,
            patient_id,
            a.get("assessment_date"),
            json.dumps(a),
        ))


def upsert_wound_extraction(conn, patient_id: str, wound: dict | None):
    conn.execute("DELETE FROM wound_extractions WHERE patient_id = ?", (patient_id,))
    if wound:
        conn.execute("""
            INSERT INTO wound_extractions
            (patient_id, wound_type, wound_stage, location, length_cm, width_cm, depth_cm,
             drainage_amount, source_type, confidence, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_id,
            wound.get("wound_type"),
            wound.get("wound_stage"),
            wound.get("location"),
            wound.get("length_cm"),
            wound.get("width_cm"),
            wound.get("depth_cm"),
            wound.get("drainage_amount"),
            wound.get("source_type"),
            wound.get("confidence"),
            wound.get("raw_text"),
        ))


def upsert_eligibility(conn, result: dict):
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
        result["patient_id"],
        result.get("facility_id"),
        result.get("first_name"),
        result.get("last_name"),
        1 if result.get("has_medicare_part_b") else 0,
        1 if result.get("has_wound_diagnosis") else 0,
        result.get("wound_type"),
        result.get("wound_stage"),
        result.get("location"),
        result.get("length_cm"),
        result.get("width_cm"),
        result.get("depth_cm"),
        result.get("drainage_amount"),
        result.get("routing_decision"),
        result.get("reason"),
    ))


def log_failed_api_call(conn, endpoint: str, url: str, params: dict, patient_id: str, error: str):
    import json as _json
    conn.execute("""
        INSERT INTO failed_api_calls (api_url, endpoint, params, patient_id, attempts, last_error, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (
        url,
        endpoint,
        _json.dumps(params or {}),
        patient_id,
        20,
        error,
    ))


def mark_retry_success(conn, call_id: int):
    conn.execute("""
        UPDATE failed_api_calls
        SET status = 'resolved', last_retried_at = datetime('now'), retry_count = retry_count + 1
        WHERE id = ?
    """, (call_id,))


def mark_retry_failed(conn, call_id: int, error: str):
    conn.execute("""
        UPDATE failed_api_calls
        SET retry_count = retry_count + 1, last_error = ?, last_retried_at = datetime('now')
        WHERE id = ?
    """, (error, call_id))


def _is_medicare_part_b(payer_code: str, payer_name: str) -> bool:
    if payer_code.upper() in ("MCB", "MCRB", "MEDICARE_B"):
        return True
    name = payer_name.lower()
    if "part b" in name or "medicare b" in name:
        return True
    if "medicare" in name and "part a" not in name and "advantage" not in name:
        return True
    return False
