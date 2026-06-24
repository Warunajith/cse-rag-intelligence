"""
Redis job queues — TWO queues so on-demand indexing never waits behind a slow
catalog discovery pass:
    cse:jobs:index      high priority — user-triggered index_report/index_company
    cse:jobs:discover   low priority  — full catalog refresh

The worker drains the index queue first, only pulling a discover job when no
index work is waiting. Polling (lpop) avoids blocking-socket timeout issues.
"""
import os
import json
import time
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
Q_INDEX = "cse:jobs:index"
Q_DISCOVER = "cse:jobs:discover"

_pool = None


def client():
    global _pool
    if _pool is None:
        _pool = redis.from_url(REDIS_URL, decode_responses=True,
                               socket_connect_timeout=5, retry_on_timeout=True)
    return _pool


def _queue_for(job: dict) -> str:
    return Q_DISCOVER if job.get("type") == "discover" else Q_INDEX


def enqueue(job: dict) -> int:
    """Push a job onto the appropriate queue by type."""
    return client().rpush(_queue_for(job), json.dumps(job))


def dequeue(timeout: int = 5):
    """Index queue has priority: pull from it first; only take a discover job
    when no index jobs are waiting. Non-blocking + idle sleep."""
    c = client()
    payload = c.lpop(Q_INDEX)
    if payload is None:
        payload = c.lpop(Q_DISCOVER)
    if payload is None:
        time.sleep(1)
        return None
    return json.loads(payload)


def queue_lengths() -> dict:
    c = client()
    return {"index": c.llen(Q_INDEX), "discover": c.llen(Q_DISCOVER)}


def discover_pending() -> int:
    """How many discover jobs are queued (used to guard against duplicates)."""
    return client().llen(Q_DISCOVER)


def list_jobs(queue: str):
    return client().lrange(queue, 0, -1)
