"""
ABI Hackathon — Wound Care Billing Pipeline
Multi-agent platform: fetches PCC API data, extracts wound info, writes to SQLite.
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("Wound Care Billing Pipeline starting...")

    import db
    db.create_schema()
    logger.info("Database ready")

    from agents.orchestrator import run_pipeline
    stats = run_pipeline()

    if stats:
        print()
        print("=" * 55)
        print("  WOUND BILLING PIPELINE — FINAL RESULTS")
        print("=" * 55)
        print(f"  Auto Accept     : {stats.get('auto_accept', 0)}")
        print(f"  Flag for Review : {stats.get('flag_for_review', 0)}")
        print(f"  Reject          : {stats.get('reject', 0)}")
        print(f"  Errors          : {stats.get('error', 0)}")
        total = sum(stats.values())
        print(f"  Total processed : {total}")
        print("=" * 55)
        print(f"  Results saved to: wound_pipeline.db")
        print(f"  Query: SELECT * FROM eligibility_results ORDER BY routing_decision;")


if __name__ == "__main__":
    main()
