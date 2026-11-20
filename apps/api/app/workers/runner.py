"""Worker process: provisioning jobs + scheduled maintenance.

  python -m app.workers.runner

Runs N job threads (each claims jobs from PostgreSQL; Redis BLPOP is only a wake-up signal)
and one scheduler thread for periodic tasks: email delivery, metrics, reconciliation,
rollups/retention, API counter flush, host inventory, backup scheduling/expiry/verification
and heartbeats.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable

import redis

from app.core.config import get_settings
from app.core.db import system_session
from app.core.logging import configure_logging, log_event
from app.core.redis import JOB_WAKEUP_KEY, get_redis, wake_workers
from app.models import WorkerHeartbeat, utcnow
from app.monitoring import metrics
from app.provisioning.hosts import ProvisionerPool
from app.services import backup_service, idempotency
from app.services import email as email_service
from app.workers import jobs
from app.workers.reconcile import reconcile

log = logging.getLogger("dmx.worker")


class Worker:
    def __init__(self, concurrency: int = 4):
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency
        self.stop = threading.Event()
        self.started = utcnow()
        # One client per host (bootstrap host from PROVISIONER_URL, others from the registry).
        self.prov = ProvisionerPool()
        self.processed = 0

    def job_loop(self, idx: int) -> None:
        while not self.stop.is_set():
            try:
                job_id = jobs.claim_next(self.id)
            except Exception as e:
                log_event(log, "claim failed", level=logging.WARNING, error=type(e).__name__)
                self.stop.wait(2)
                continue
            if job_id is None:
                try:
                    get_redis().blpop([JOB_WAKEUP_KEY], timeout=2)
                except redis.RedisError:
                    self.stop.wait(2)
                continue
            try:
                jobs.execute(job_id, self.id, self.prov)
                self.processed += 1
            except Exception:
                log.exception("job loop error")

    def every(self, name: str, seconds: float, fn: Callable[[], object], last: dict[str, float]) -> None:
        now = time.monotonic()
        if now - last.get(name, 0) < seconds:
            return
        last[name] = now
        try:
            result = fn()
            if result:
                log_event(log, f"{name} done", result=result)
        except Exception as e:
            log_event(log, f"{name} failed", level=logging.WARNING, error=type(e).__name__)

    def fast_loop(self) -> None:
        """Latency-sensitive work (verification/reset emails, heartbeat) runs on its own thread
        so slow maintenance (metrics over many instances, backups scheduling) never delays it."""
        last: dict[str, float] = {}
        while not self.stop.is_set():
            self.every("heartbeat", 15, self.heartbeat, last)
            self.every("email", 2, self._emails, last)
            self.stop.wait(0.5)

    def scheduler_loop(self) -> None:
        s = get_settings()
        last: dict[str, float] = {}
        while not self.stop.is_set():
            self.every("api_counters", 60, self._counters, last)
            self.every("inventory", 60, self._inventory, last)
            self.every("metrics", s.metrics_interval_seconds, self._metrics, last)
            self.every("reconcile", s.reconcile_interval_seconds, self._reconcile, last)
            self.every("rollup", 3600, self._rollup, last)
            self.every("billing_enforcement", 3600, self._billing, last)
            self.every("billing_reconcile", 6 * 3600, self._billing_reconcile, last)
            if s.backups_enabled:
                self.every("backup_schedule", s.backup_schedule_interval_seconds, self._backup_schedule, last)
                self.every("backup_expiry", 900, self._backup_expiry, last)
            if s.backup_verify_enabled:
                self.every("backup_verify", 3600, self._backup_verify, last)
            self.stop.wait(1)

    def _emails(self) -> int:
        with system_session() as db:
            return email_service.deliver_pending(db)

    def _counters(self) -> int:
        with system_session() as db:
            return metrics.flush_api_counters(db)

    def _inventory(self) -> None:
        with system_session() as db:
            metrics.update_host_inventory(db, self.prov)

    def _metrics(self) -> int:
        with system_session() as db:
            return metrics.collect_all(db, self.prov)

    def _reconcile(self) -> dict[str, int]:
        with system_session() as db:
            out = reconcile(db, self.prov)
        return {k: v for k, v in out.items() if v}

    def _rollup(self) -> dict[str, int]:
        with system_session() as db:
            out = metrics.rollup_and_prune(db)
            out["idempotency_pruned"] = idempotency.prune(db)
        return out

    def _billing(self) -> int:
        from app.billing.enforcement import enforce

        with system_session() as db:
            return enforce(db)

    def _billing_reconcile(self) -> int:
        from app.billing.paddle import reconcile_subscriptions

        with system_session() as db:
            return reconcile_subscriptions(db)

    def _backup_schedule(self) -> int:
        with system_session() as db:
            n = backup_service.schedule_daily_backups(db)
        if n:
            wake_workers()
        return n

    def _backup_expiry(self) -> dict[str, int]:
        with system_session() as db:
            out = backup_service.expire_backups(db, self.prov)
        return {k: v for k, v in out.items() if v}

    def _backup_verify(self) -> int:
        with system_session() as db:
            n = backup_service.schedule_verifications(db, limit=get_settings().backup_verify_per_hour)
        if n:
            wake_workers()
        return n

    def heartbeat(self) -> None:
        with system_session() as db:
            hb = db.get(WorkerHeartbeat, self.id)
            if hb is None:
                hb = WorkerHeartbeat(worker_id=self.id, kind="worker", started_at=self.started, last_seen_at=utcnow())
                db.add(hb)
            hb.last_seen_at = utcnow()
            hb.info = {"concurrency": self.concurrency, "processed": self.processed}

    def run(self) -> None:
        log_event(log, "worker starting", worker_id=self.id, concurrency=self.concurrency)
        threads = [
            threading.Thread(target=self.job_loop, args=(i,), daemon=True, name=f"job-{i}")
            for i in range(self.concurrency)
        ]
        threads.append(threading.Thread(target=self.fast_loop, daemon=True, name="fast"))
        threads.append(threading.Thread(target=self.scheduler_loop, daemon=True, name="scheduler"))
        for t in threads:
            t.start()
        self.stop.wait()
        log_event(log, "worker stopping", worker_id=self.id)
        for t in threads:
            t.join(timeout=30)
        jobs.release_stale_leases_for(self.id)


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    w = Worker(concurrency=int(os.environ.get("DMX_WORKER_CONCURRENCY", "4")))
    signal.signal(signal.SIGTERM, lambda *_: w.stop.set())
    signal.signal(signal.SIGINT, lambda *_: w.stop.set())
    w.run()


if __name__ == "__main__":
    main()
