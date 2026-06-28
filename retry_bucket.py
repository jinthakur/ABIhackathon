"""
Retry Bucket — reprocesses API calls that failed all 20 retries.
Reads from failed_api_calls table, retries each, marks success or failure.

Run: python retry_bucket.py
"""
import sqlite3
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = "wound_pipeline.db"


def show_failed_calls():
    """Show all pending failed API calls."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, endpoint, api_url, patient_id, attempts, retry_count,
               last_error, status, created_at
        FROM failed_api_calls
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("\nNo failed API calls in the retry bucket.")
        return

    print(f"\n{'='*80}")
    print(f"  RETRY BUCKET — {len(rows)} failed API calls")
    print(f"{'='*80}")
    print(f"  {'ID':<5} {'Status':<10} {'Endpoint':<25} {'Patient':<12} "
          f"{'Attempts':<9} {'Retries':<8} {'Error'}")
    print(f"  {'-'*5} {'-'*10} {'-'*25} {'-'*12} {'-'*9} {'-'*8} {'-'*30}")
    for r in rows:
        print(f"  {r['id']:<5} {r['status']:<10} {r['endpoint']:<25} "
              f"{(r['patient_id'] or ''):<12} {r['attempts']:<9} "
              f"{r['retry_count']:<8} {(r['last_error'] or '')[:40]}")

    pending = sum(1 for r in rows if r["status"] == "pending")
    resolved = sum(1 for r in rows if r["status"] == "resolved")
    print(f"\n  Pending: {pending} | Resolved: {resolved}")
    print(f"  API URL format: {rows[0]['api_url'] if rows else 'N/A'}")
    print(f"{'='*80}\n")


def retry_failed_calls(max_additional_retries: int = 20):
    """Retry all pending failed API calls up to max_additional_retries times."""
    from api_client import curl_get, BASE_URL, MAX_RETRIES, RETRY_DELAY
    import time

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, endpoint, api_url, params, patient_id
        FROM failed_api_calls
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()

    if not rows:
        print("No pending failed calls to retry.")
        conn.close()
        return

    print(f"\nRetrying {len(rows)} failed API calls (up to {max_additional_retries} retries each)...")
    print(f"API Base URL: {BASE_URL}\n")

    success_count = 0
    still_failed = 0

    for row in rows:
        call_id = row["id"]
        endpoint = row["endpoint"]
        params = json.loads(row["params"] or "{}")
        patient_id = row["patient_id"]
        api_url = row["api_url"]

        logger.info(f"Retrying: {endpoint} | patient={patient_id} | url={api_url}")

        data = None
        for attempt in range(1, max_additional_retries + 1):
            result = curl_get(endpoint, params=params)
            if result is not None:
                data = result
                break
            logger.warning(f"  Retry {attempt}/{max_additional_retries} failed for {endpoint}")

        if data is not None:
            cur.execute("""
                UPDATE failed_api_calls
                SET status = 'resolved', last_retried_at = datetime('now'),
                    retry_count = retry_count + 1
                WHERE id = ?
            """, (call_id,))
            conn.commit()
            logger.info(f"  SUCCESS: {endpoint} resolved")
            success_count += 1
        else:
            cur.execute("""
                UPDATE failed_api_calls
                SET retry_count = retry_count + 1, last_retried_at = datetime('now'),
                    last_error = 'Retry bucket: still failing after additional retries'
                WHERE id = ?
            """, (call_id,))
            conn.commit()
            logger.error(f"  STILL FAILING: {endpoint} | patient={patient_id}")
            still_failed += 1

    conn.close()
    print(f"\nRetry bucket results:")
    print(f"  Resolved  : {success_count}")
    print(f"  Still fail: {still_failed}")


if __name__ == "__main__":
    import os
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        print("Run main.py first to populate data.")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "retry":
        retry_failed_calls()
    else:
        show_failed_calls()
        if len(sys.argv) == 1:
            print("Run with 'retry' argument to retry pending calls:")
            print("  python retry_bucket.py retry")
