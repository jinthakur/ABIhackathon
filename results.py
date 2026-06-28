"""
Results viewer — query the wound billing pipeline output from SQLite.
Run after main.py completes.
"""
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "wound_pipeline.db")


def get_db():
    if not os.path.exists(DB_PATH):
        print("Database not found. Run main.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def print_summary():
    conn = get_db()
    cur = conn.cursor()

    print("\n" + "=" * 70)
    print("  WOUND BILLING PIPELINE — RESULTS SUMMARY")
    print("=" * 70)

    # Overall counts
    cur.execute("""
        SELECT routing_decision, COUNT(*) as cnt
        FROM eligibility_results
        GROUP BY routing_decision
        ORDER BY routing_decision
    """)
    rows = cur.fetchall()
    total = sum(r["cnt"] for r in rows)
    print(f"\n  Total patients processed: {total}\n")
    for row in rows:
        decision = row["routing_decision"] or "pending"
        cnt = row["cnt"]
        pct = 100 * cnt / total if total else 0
        bar = "#" * int(pct / 3)
        print(f"  {decision:<20} {cnt:>4}  ({pct:5.1f}%)  {bar}")

    # Per facility breakdown
    print("\n" + "-" * 70)
    print("  BREAKDOWN BY FACILITY")
    print("-" * 70)
    cur.execute("""
        SELECT facility_id, routing_decision, COUNT(*) as cnt
        FROM eligibility_results
        GROUP BY facility_id, routing_decision
        ORDER BY facility_id, routing_decision
    """)
    rows = cur.fetchall()
    current_fac = None
    for row in rows:
        if row["facility_id"] != current_fac:
            current_fac = row["facility_id"]
            print(f"\n  Facility {current_fac}:")
        print(f"    {row['routing_decision']:<20} {row['cnt']}")

    # Auto-accept patients (safe to bill)
    print("\n" + "-" * 70)
    print("  AUTO-ACCEPT PATIENTS (Ready for Billing)")
    print("-" * 70)
    cur.execute("""
        SELECT patient_id, first_name, last_name, facility_id,
               wound_type, wound_stage, location,
               length_cm, width_cm, depth_cm, drainage_amount
        FROM eligibility_results
        WHERE routing_decision = 'auto_accept'
        ORDER BY facility_id, patient_id
        LIMIT 30
    """)
    rows = cur.fetchall()
    print(f"\n  {'Patient':<10} {'Name':<20} {'Wound Type':<20} {'Stage':<12} {'Location':<15} {'Measurements':<15} {'Drainage'}")
    print(f"  {'-'*10} {'-'*20} {'-'*20} {'-'*12} {'-'*15} {'-'*15} {'-'*10}")
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
        meas = ""
        if r["length_cm"] and r["width_cm"]:
            meas = f"{r['length_cm']}x{r['width_cm']}"
            if r["depth_cm"]:
                meas += f"x{r['depth_cm']}"
            meas += " cm"
        print(f"  {r['patient_id']:<10} {name:<20} {(r['wound_type'] or ''):<20} "
              f"{(r['wound_stage'] or ''):<12} {(r['location'] or ''):<15} "
              f"{meas:<15} {r['drainage_amount'] or ''}")

    # Flag for review
    print("\n" + "-" * 70)
    print("  FLAG FOR REVIEW (Top 10 — Manual Review Required)")
    print("-" * 70)
    cur.execute("""
        SELECT patient_id, first_name, last_name, facility_id, reason
        FROM eligibility_results
        WHERE routing_decision = 'flag_for_review'
        ORDER BY facility_id, patient_id
        LIMIT 10
    """)
    rows = cur.fetchall()
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
        print(f"  {r['patient_id']:<10} {name:<22} {r['reason']}")

    # Medicare coverage stats
    print("\n" + "-" * 70)
    print("  MEDICARE PART B COVERAGE STATS")
    print("-" * 70)
    cur.execute("""
        SELECT
            SUM(has_medicare_part_b) as with_b,
            COUNT(*) - SUM(has_medicare_part_b) as without_b,
            COUNT(*) as total
        FROM eligibility_results
    """)
    r = cur.fetchone()
    print(f"\n  With Medicare Part B   : {r['with_b']} ({100*r['with_b']//r['total']}%)")
    print(f"  Without Medicare Part B: {r['without_b']} ({100*r['without_b']//r['total']}%)")

    print("\n" + "=" * 70)
    print(f"  Database: {DB_PATH}")
    print("  Query directly: sqlite3 wound_pipeline.db")
    print("  Example: SELECT * FROM eligibility_results WHERE routing_decision='auto_accept';")
    print("=" * 70 + "\n")

    conn.close()


def print_patient(patient_id: str):
    """Show all data for a specific patient."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM eligibility_results WHERE patient_id = ?", (patient_id,))
    elig = cur.fetchone()
    if not elig:
        print(f"Patient {patient_id} not found")
        conn.close()
        return

    print(f"\n{'='*60}")
    print(f"  Patient: {patient_id} — {elig['first_name']} {elig['last_name']}")
    print(f"{'='*60}")
    print(f"  Facility      : {elig['facility_id']}")
    print(f"  Medicare Part B: {'Yes' if elig['has_medicare_part_b'] else 'No'}")
    print(f"  Wound Diagnosis: {'Yes' if elig['has_wound_diagnosis'] else 'No'}")
    print(f"\n  Wound Type    : {elig['wound_type'] or 'N/A'}")
    print(f"  Stage         : {elig['wound_stage'] or 'N/A'}")
    print(f"  Location      : {elig['location'] or 'N/A'}")
    meas = "N/A"
    if elig["length_cm"] and elig["width_cm"]:
        meas = f"{elig['length_cm']}x{elig['width_cm']}"
        if elig["depth_cm"]:
            meas += f"x{elig['depth_cm']}"
        meas += " cm"
    print(f"  Measurements  : {meas}")
    print(f"  Drainage      : {elig['drainage_amount'] or 'N/A'}")
    print(f"\n  ROUTING: {(elig['routing_decision'] or '').upper()}")
    print(f"  Reason : {elig['reason']}")

    cur.execute("SELECT note_text FROM clinical_notes WHERE patient_id = ?", (patient_id,))
    notes = cur.fetchall()
    if notes:
        print(f"\n  Clinical Notes ({len(notes)}):")
        for n in notes:
            print(f"    {n['note_text'][:200]}...")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print_patient(sys.argv[1])
    else:
        print_summary()
