"""
API Client — calls the PCC API using Python requests.
Retries up to MAX_RETRIES times with RETRY_DELAY seconds on 429 rate limits.
"""
import requests
import time
import logging
from config import BASE_URL, MAX_RETRIES, RETRY_DELAY

logger = logging.getLogger(__name__)

import threading
from requests.adapters import HTTPAdapter

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})
_adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# Thread-safe list of API calls that failed all retries
_failed_calls: list[dict] = []
_failed_lock = threading.Lock()


def get_failed_calls() -> list[dict]:
    with _failed_lock:
        return list(_failed_calls)


def clear_failed_calls():
    with _failed_lock:
        _failed_calls.clear()


def curl_get(endpoint: str, params: dict = None) -> dict | list | None:
    """
    GET the PCC API endpoint with retry logic.
    Named curl_get to match the multi-agent interface contract.
    30% of calls return 429 — retries up to MAX_RETRIES with RETRY_DELAY sec wait.
    """
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=20)

            if resp.status_code == 200:
                logger.debug(f"OK {endpoint} (attempt {attempt})")
                return resp.json()

            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", RETRY_DELAY))
                wait = max(retry_after, RETRY_DELAY)
                logger.warning(
                    f"429 Rate limited: {endpoint} | attempt {attempt}/{MAX_RETRIES} | waiting {wait}s"
                )
                time.sleep(wait)
                continue

            elif resp.status_code in (500, 502, 503, 504):
                logger.warning(f"Server error {resp.status_code} on {endpoint} attempt {attempt}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                continue

            else:
                logger.error(f"HTTP {resp.status_code} on {endpoint}: {resp.text[:200]}")
                return None

        except requests.Timeout:
            logger.warning(f"Timeout on {endpoint} attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            continue

        except requests.ConnectionError as e:
            logger.error(f"Connection error on {endpoint} attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            continue

        except Exception as e:
            logger.error(f"Unexpected error on {endpoint} attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            continue

    error_msg = f"Exhausted {MAX_RETRIES} retries with no success"
    logger.error(f"FAILED after {MAX_RETRIES} retries: {endpoint} | url={url}")
    with _failed_lock:
        _failed_calls.append({
            "api_url": url,
            "endpoint": endpoint,
            "params": params or {},
            "attempts": MAX_RETRIES,
            "last_error": error_msg,
        })
    return None
