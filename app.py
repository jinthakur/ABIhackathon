"""
Flask web dashboard for Wound Care Billing Pipeline.
Features:
  - Live results table with filtering
  - SQL query runner
  - MCP/API endpoints (JSON)
  - Run pipeline button (triggers upsert — insert new, update existing)
  - Real-time progress polling
"""
import sys
import os
import sqlite3
import threading
import logging

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "wound_pipeline.db")

_pipeline_thread: threading.Thread | None = None


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    conn = get_db()

    # Stats
    stats = {"auto_accept": 0, "flag_for_review": 0, "reject": 0, "total": 0}
    for r in conn.execute("SELECT routing_decision, COUNT(*) as cnt FROM eligibility_results GROUP BY routing_decision"):
        stats[r["routing_decision"]] = r["cnt"]
        stats["total"] += r["cnt"]

    # All results
    results = conn.execute("""
        SELECT patient_id, facility_id, first_name, last_name, has_medicare_part_b,
               wound_type, wound_stage, location, length_cm, width_cm, depth_cm,
               drainage_amount, routing_decision, reason
        FROM eligibility_results
        ORDER BY routing_decision, facility_id, patient_id
    """).fetchall()

    # Patients with missing wound data fields
    missing_rows = conn.execute("""
        SELECT patient_id, first_name, last_name, facility_id, routing_decision,
               wound_type, wound_stage, location, length_cm, width_cm, depth_cm, drainage_amount
        FROM eligibility_results
        WHERE wound_type IS NULL OR length_cm IS NULL OR width_cm IS NULL
              OR drainage_amount IS NULL OR location IS NULL
        ORDER BY facility_id, patient_id
    """).fetchall()

    # Table counts
    table_counts = {}
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (t,) in tables:
        table_counts[t] = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]

    conn.close()
    return render_template("index.html", stats=stats, results=results, table_counts=table_counts, missing_rows=missing_rows)


@app.route("/patient/<patient_id>")
def patient_detail(patient_id):
    conn = get_db()
    patient = conn.execute(
        "SELECT * FROM eligibility_results WHERE patient_id = ?", (patient_id,)
    ).fetchone()
    if not patient:
        return f"Patient {patient_id} not found", 404

    notes = conn.execute(
        "SELECT note_text, note_date, note_type FROM clinical_notes WHERE patient_id = ? ORDER BY note_date DESC",
        (patient_id,)
    ).fetchall()

    diagnoses = conn.execute(
        "SELECT icd10_code, description, is_wound_related FROM diagnoses WHERE patient_id = ?",
        (patient_id,)
    ).fetchall()

    conn.close()
    return render_template("patient.html", patient=patient, notes=notes, diagnoses=diagnoses)


# ── API Endpoints (MCP-style) ──────────────────────────────────────────────────

@app.route("/api/results")
def api_results():
    """Returns eligibility results as JSON. Supports ?limit, ?decision, ?facility filters."""
    limit = int(request.args.get("limit", 300))
    decision = request.args.get("decision", "")
    facility = request.args.get("facility", "")

    conn = get_db()
    where = []
    params = []
    if decision:
        where.append("routing_decision = ?")
        params.append(decision)
    if facility:
        where.append("facility_id = ?")
        params.append(int(facility))
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        f"SELECT * FROM eligibility_results {clause} ORDER BY routing_decision, patient_id LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()

    return jsonify({
        "count": len(rows),
        "results": [dict(r) for r in rows],
        "sql": f"SELECT * FROM eligibility_results {clause} ORDER BY routing_decision, patient_id LIMIT {limit}",
    })


@app.route("/api/patient/<patient_id>")
def api_patient(patient_id):
    """Returns full data for a single patient."""
    conn = get_db()
    elig = conn.execute("SELECT * FROM eligibility_results WHERE patient_id = ?", (patient_id,)).fetchone()
    notes = conn.execute("SELECT * FROM clinical_notes WHERE patient_id = ?", (patient_id,)).fetchall()
    diag = conn.execute("SELECT * FROM diagnoses WHERE patient_id = ?", (patient_id,)).fetchall()
    cov = conn.execute("SELECT * FROM coverage WHERE patient_id = ?", (patient_id,)).fetchall()
    wound = conn.execute("SELECT * FROM wound_extractions WHERE patient_id = ?", (patient_id,)).fetchone()
    conn.close()

    if not elig:
        return jsonify({"error": f"Patient {patient_id} not found"}), 404

    return jsonify({
        "eligibility": dict(elig),
        "wound_extraction": dict(wound) if wound else None,
        "notes": [dict(n) for n in notes],
        "diagnoses": [dict(d) for d in diag],
        "coverage": [dict(c) for c in cov],
    })


@app.route("/api/tables")
def api_tables():
    """Returns all table names and their row counts."""
    conn = get_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    counts = {}
    for (t,) in tables:
        counts[t] = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
    conn.close()
    return jsonify({
        "database": DB_PATH,
        "tables": counts,
        "sql": "SELECT name FROM sqlite_master WHERE type='table'; SELECT COUNT(*) FROM [table];",
    })


@app.route("/api/query", methods=["POST"])
def api_query():
    """Run a custom SQL SELECT query. Returns rows as JSON."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    allowed_starts = ("select", "with", "pragma", "explain")
    if not any(sql.lower().startswith(s) for s in allowed_starts):
        return jsonify({"error": "Only SELECT queries allowed"}), 403

    conn = get_db()
    try:
        rows = conn.execute(sql).fetchall()
        columns = [d[0] for d in conn.execute(sql).description] if rows else []
        result_rows = [dict(zip(columns, row)) for row in rows]
        return jsonify({
            "sql": sql,
            "row_count": len(result_rows),
            "columns": columns,
            "rows": result_rows[:500],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    """Trigger the pipeline. Uses upsert — insert new patients, update existing."""
    global _pipeline_thread
    if _pipeline_thread and _pipeline_thread.is_alive():
        return jsonify({"status": "already_running", "message": "Pipeline is already running"})

    def run():
        try:
            from src.pipeline import run_pipeline
            run_pipeline()
        except Exception as e:
            logger.error(f"Pipeline error: {e}")

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    return jsonify({"status": "started", "message": "Pipeline started in background"})


@app.route("/api/status")
def api_status():
    """Returns current pipeline progress and overall stats."""
    conn = get_db()
    stats = {"auto_accept": 0, "flag_for_review": 0, "reject": 0, "total": 0}
    for r in conn.execute("SELECT routing_decision, COUNT(*) as cnt FROM eligibility_results GROUP BY routing_decision"):
        stats[r["routing_decision"]] = r["cnt"]
        stats["total"] += r["cnt"]
    conn.close()

    try:
        from src.pipeline import get_progress
        progress = get_progress()
    except Exception:
        progress = {"running": False, "done": 0, "total": stats["total"], "error": 0}

    return jsonify({**stats, **progress})


@app.route("/api/patient/<patient_id>/retry", methods=["POST"])
def api_retry_patient(patient_id):
    """Re-fetch all PCC API data for one patient and reprocess wound extraction + eligibility."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, patient_id, facility_id, first_name, last_name, date_of_birth, raw_data FROM patients WHERE patient_id = ?",
        (patient_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": f"Patient {patient_id} not found in patients table"}), 404

    import json as _json
    raw = _json.loads(row["raw_data"]) if row["raw_data"] else {}
    patient = {**raw, "patient_id": row["patient_id"], "id": row["id"], "_facility_id": row["facility_id"]}

    try:
        from agents.clinical_agent import fetch_clinical_data
        from agents.extraction_agent import extract_wound_data
        from agents.eligibility_agent import determine_eligibility
        import db as _db

        clinical_data = fetch_clinical_data(patient)
        wound = extract_wound_data(clinical_data["notes"], clinical_data["assessments"])
        eligibility = determine_eligibility(clinical_data, wound)

        conn2 = _db.get_connection()
        try:
            _db.upsert_patient(conn2, patient, row["facility_id"])
            _db.insert_diagnoses(conn2, patient_id, clinical_data["diagnoses"])
            _db.insert_coverage(conn2, patient_id, clinical_data["coverage"])
            _db.insert_notes(conn2, row["id"], patient_id, clinical_data["notes"])
            _db.insert_assessments(conn2, row["id"], patient_id, clinical_data["assessments"])
            _db.upsert_wound_extraction(conn2, patient_id, wound)
            _db.upsert_eligibility(conn2, eligibility)
            conn2.commit()
        except Exception as e:
            conn2.rollback()
            raise
        finally:
            conn2.close()

        return jsonify({
            "status": "ok",
            "patient_id": patient_id,
            "routing_decision": eligibility["routing_decision"],
            "wound_type": eligibility.get("wound_type"),
            "location": eligibility.get("location"),
            "length_cm": eligibility.get("length_cm"),
            "width_cm": eligibility.get("width_cm"),
            "drainage_amount": eligibility.get("drainage_amount"),
            "reason": eligibility.get("reason"),
            "notes_fetched": len(clinical_data["notes"]),
            "assessments_fetched": len(clinical_data["assessments"]),
        })

    except Exception as e:
        logger.error(f"Retry failed for {patient_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/patient/<patient_id>/followup", methods=["POST"])
def api_followup(patient_id):
    """Generate AI follow-up suggestions for a patient using OpenAI."""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    conn = get_db()
    elig = conn.execute("SELECT * FROM eligibility_results WHERE patient_id = ?", (patient_id,)).fetchone()
    notes = conn.execute(
        "SELECT note_text, note_date, note_type FROM clinical_notes WHERE patient_id = ? ORDER BY note_date DESC LIMIT 3",
        (patient_id,)
    ).fetchall()
    diagnoses = conn.execute(
        "SELECT icd10_code, description FROM diagnoses WHERE patient_id = ?", (patient_id,)
    ).fetchall()
    conn.close()

    if not elig:
        return jsonify({"error": f"Patient {patient_id} not found"}), 404

    e = dict(elig)
    wound_summary = (
        f"Wound Type: {e.get('wound_type') or 'unknown'}, "
        f"Stage: {e.get('wound_stage') or 'unknown'}, "
        f"Location: {e.get('location') or 'unknown'}, "
        f"Size: {e.get('length_cm') or '?'}x{e.get('width_cm') or '?'}"
        + (f"x{e['depth_cm']}" if e.get('depth_cm') else "") + " cm, "
        f"Drainage: {e.get('drainage_amount') or 'unknown'}"
    )
    diag_text = "; ".join(f"{d['icd10_code']} ({d['description']})" for d in diagnoses) or "None"
    notes_text = "\n".join(
        f"[{n['note_date']} {n['note_type']}]: {n['note_text'][:400]}" for n in notes
    ) or "No notes available."

    prompt = f"""You are a wound care clinical advisor. Based on the following patient data, provide concise follow-up recommendations.

Patient: {e.get('first_name')} {e.get('last_name')} (ID: {patient_id})
Medicare Part B: {'Yes' if e.get('has_medicare_part_b') else 'No'}
Billing Decision: {e.get('routing_decision', '').replace('_', ' ').upper()}
Reason: {e.get('reason') or 'N/A'}

Wound Assessment:
{wound_summary}

ICD-10 Diagnoses: {diag_text}

Recent Clinical Notes:
{notes_text}

Provide 3-5 specific, actionable follow-up recommendations covering: wound care treatment, reassessment timing, documentation gaps (if any), and billing next steps. Be concise and clinical."""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        suggestions = response.choices[0].message.content.strip()
        return jsonify({"patient_id": patient_id, "suggestions": suggestions})
    except Exception as e:
        logger.error(f"OpenAI error for {patient_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/failed-calls")
def api_failed_calls():
    """Returns all failed API calls from the retry bucket."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM failed_api_calls ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({
        "count": len(rows),
        "failed_calls": [dict(r) for r in rows],
        "retry_command": "python retry_bucket.py retry",
    })


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        print("Run main.py first to populate data.")
        sys.exit(1)
    print(f"\n  Wound Billing Dashboard running at http://127.0.0.1:5000\n")
    app.run(debug=True, port=5000, use_reloader=False)
