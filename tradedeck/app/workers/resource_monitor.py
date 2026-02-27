"""
app/workers/resource_monitor.py

Logs RSS, CPU, pool stats, and open FDs to resource_metrics every 60 seconds.
Detects slow leaks using a sliding window of samples.
Fires resource_alerts when thresholds breach.

Leak detection logic:
  A single high RSS reading means nothing — could be a burst.
  A leak is when RSS grows monotonically across N consecutive samples
  without ever decreasing. We use a simple linear regression slope
  on the last LEAK_WINDOW samples. If slope > LEAK_SLOPE_THRESHOLD
  MB/min for LEAK_CONFIRM_SAMPLES consecutive windows → alert.

This catches the real killers:
  - Forgotten append() without maxlen → steady 0.5MB/min growth
  - Unclosed DB sessions → pool saturation + RSS growth together
  - Growing asyncio task count → task leak
"""
import asyncio
import collections
import logging
import os
import statistics
import time
from datetime import datetime, timezone
from typing import Optional

import psutil
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
SAMPLE_INTERVAL_S     = 60       # Log every 60 seconds
RETENTION_DAYS        = 7        # Delete samples older than this

RSS_WARN_MB           = 350      # Warn at 350MB (68% of 512MB limit)
RSS_CRITICAL_MB       = 430      # Critical at 430MB (84%)
CPU_WARN_PCT          = 70       # Warn at 70% process CPU
POOL_WARN_PCT         = 80       # Warn at 80% pool utilisation
FD_WARN               = 400      # Warn at 400 open file descriptors
TASK_WARN             = 100      # Warn at 100 asyncio tasks

# Leak detection
LEAK_WINDOW           = 10       # Number of samples to regress over (10 min window)
LEAK_SLOPE_THRESHOLD  = 0.5      # MB/min sustained growth = probable leak
LEAK_CONFIRM_SAMPLES  = 3        # Consecutive windows above slope = confirmed leak


class ResourceMonitor:

    def __init__(
        self,
        session_factory: async_sessionmaker,
        engine=None,
    ):
        self.session_factory = session_factory
        self.engine          = engine          # For pool stats
        self._running        = False
        self._task: Optional[asyncio.Task] = None
        self._proc           = psutil.Process(os.getpid())

        # Sliding window for leak detection
        self._rss_history:  collections.deque = collections.deque(maxlen=LEAK_WINDOW)
        self._fd_history:   collections.deque = collections.deque(maxlen=LEAK_WINDOW)
        self._task_history: collections.deque = collections.deque(maxlen=LEAK_WINDOW)

        # Consecutive leak confirmations
        self._rss_leak_streak  = 0
        self._fd_leak_streak   = 0
        self._active_alerts: dict[str, int] = {}  # alert_type → alert row id

        # Tick rate tracking (fed by executor)
        self._tick_count    = 0
        self._last_tick_ts  = time.time()

        # Previous RSS for delta
        self._prev_rss_mb: Optional[float] = None

    def record_tick(self) -> None:
        """Called by FeedWorker on every tick. Thread-safe increment."""
        self._tick_count += 1

    async def start(self) -> None:
        self._running = True
        self._task    = asyncio.create_task(self._loop(), name="resource_monitor")
        logger.info("Resource monitor started (interval=%ds).", SAMPLE_INTERVAL_S)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("Resource monitor stopped.")

    # ─────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ─────────────────────────────────────────────────────────────────────
    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(SAMPLE_INTERVAL_S)
                await self._sample()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Monitor must never crash — log and continue
                logger.error("Resource monitor sample error: %s", e, exc_info=True)

    async def _sample(self) -> None:
        now = datetime.now(timezone.utc)

        # ── Collect metrics ────────────────────────────────────────────────
        mem   = self._proc.memory_info()
        rss   = round(mem.rss / 1024**2, 2)
        vms   = round(mem.vms / 1024**2, 2)
        delta = round(rss - self._prev_rss_mb, 2) if self._prev_rss_mb else None
        self._prev_rss_mb = rss

        cpu_proc  = self._proc.cpu_percent(interval=None)
        cpu_sys   = psutil.cpu_percent(interval=None)

        try:
            open_fds = self._proc.num_fds()
        except AttributeError:
            open_fds = None  # Windows doesn't support this

        active_tasks = len(asyncio.all_tasks())

        # Pool stats
        pool_out  = pool_size = pool_of = None
        if self.engine:
            try:
                pool      = self.engine.pool
                pool_out  = pool.checkedout()
                pool_size = pool.size()
                pool_of   = pool.overflow()
            except Exception:
                pass

        # Tick rate
        now_ts    = time.time()
        elapsed   = now_ts - self._last_tick_ts
        tick_rate = round(self._tick_count / elapsed, 2) if elapsed > 0 else 0
        self._tick_count   = 0
        self._last_tick_ts = now_ts

        # Running strategies count
        running_count = None
        try:
            async with self.session_factory() as db:
                r = await db.execute(
                    text("SELECT COUNT(*) FROM strategy_states WHERE status='running'")
                )
                running_count = r.scalar()
        except Exception:
            pass

        # ── Leak detection ─────────────────────────────────────────────────
        self._rss_history.append(rss)
        if open_fds is not None:
            self._fd_history.append(open_fds)
        self._task_history.append(active_tasks)

        rss_leak  = self._detect_leak(list(self._rss_history),  LEAK_SLOPE_THRESHOLD)
        fd_leak   = self._detect_leak(list(self._fd_history),   2.0)     # 2 FD/min slope
        task_leak = self._detect_leak(list(self._task_history), 1.0)     # 1 task/min slope

        if rss_leak:
            self._rss_leak_streak += 1
        else:
            self._rss_leak_streak = 0

        if fd_leak:
            self._fd_leak_streak += 1
        else:
            self._fd_leak_streak = 0

        rss_leak_confirmed  = self._rss_leak_streak >= LEAK_CONFIRM_SAMPLES
        fd_leak_confirmed   = self._fd_leak_streak  >= LEAK_CONFIRM_SAMPLES

        # ── Write sample to DB ─────────────────────────────────────────────
        async with self.session_factory() as db:
            await db.execute(
                text(
                    "INSERT INTO resource_metrics "
                    "(recorded_at, rss_mb, vms_mb, rss_delta_mb, cpu_pct, cpu_sys_pct, "
                    " pool_checked_out, pool_size, pool_overflow, open_fds, active_tasks, "
                    " rss_leak_flag, fd_leak_flag, running_strategies, tick_rate_hz) "
                    "VALUES "
                    "(:ts, :rss, :vms, :delta, :cpu, :cpu_s, "
                    " :pool_out, :pool_sz, :pool_of, :fds, :tasks, "
                    " :rss_lk, :fd_lk, :run_s, :ticks)"
                ),
                {
                    "ts":       now,
                    "rss":      rss,   "vms":     vms,    "delta":   delta,
                    "cpu":      cpu_proc, "cpu_s": cpu_sys,
                    "pool_out": pool_out, "pool_sz": pool_size, "pool_of": pool_of,
                    "fds":      open_fds, "tasks":   active_tasks,
                    "rss_lk":   rss_leak_confirmed,
                    "fd_lk":    fd_leak_confirmed,
                    "run_s":    running_count,
                    "ticks":    tick_rate,
                }
            )

            # ── Fire alerts ────────────────────────────────────────────────
            await self._check_thresholds(db, now, rss, cpu_proc, open_fds,
                                          active_tasks, pool_out, pool_size,
                                          rss_leak_confirmed, fd_leak_confirmed)

            # ── Prune old samples ──────────────────────────────────────────
            await db.execute(
                text(
                    "DELETE FROM resource_metrics "
                    "WHERE recorded_at < NOW() - INTERVAL ':days days'"
                    .replace(":days days", f"{RETENTION_DAYS} days")
                )
            )

            await db.commit()

        # ── Structured log every sample ────────────────────────────────────
        logger.info(
            "rss=%.1fMB delta=%s cpu=%.1f%% fds=%s tasks=%d pool=%s/%s ticks/s=%.1f "
            "running=%s%s%s",
            rss,
            f"{delta:+.1f}MB" if delta is not None else "?",
            cpu_proc,
            open_fds or "?",
            active_tasks,
            pool_out or "?",
            pool_size or "?",
            tick_rate,
            running_count or "?",
            " ⚠ RSS_LEAK"   if rss_leak_confirmed else "",
            " ⚠ FD_LEAK"    if fd_leak_confirmed  else "",
        )

    # ─────────────────────────────────────────────────────────────────────
    # LEAK DETECTION
    # ─────────────────────────────────────────────────────────────────────
    def _detect_leak(self, samples: list, slope_threshold: float) -> bool:
        """
        Returns True if the linear regression slope of samples
        exceeds slope_threshold (per sample interval).

        Uses least-squares regression — same as numpy.polyfit(deg=1).
        No numpy dependency. Pure stdlib.

        A random spike doesn't trip this — only sustained monotonic growth.
        """
        n = len(samples)
        if n < 4:
            return False

        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(samples) / n

        numerator   = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, samples))
        denominator = sum((x - x_mean) ** 2 for x in xs)

        if denominator == 0:
            return False

        slope = numerator / denominator
        return slope > slope_threshold

    # ─────────────────────────────────────────────────────────────────────
    # THRESHOLD ALERTS
    # ─────────────────────────────────────────────────────────────────────
    async def _check_thresholds(
        self, db, now, rss, cpu, fds, tasks, pool_out, pool_size,
        rss_leak, fd_leak
    ) -> None:
        checks = [
            ("RSS_HIGH",       rss,   RSS_CRITICAL_MB,  f"RSS {rss:.1f}MB >= {RSS_CRITICAL_MB}MB limit"),
            ("RSS_WARN",       rss,   RSS_WARN_MB,      f"RSS {rss:.1f}MB >= {RSS_WARN_MB}MB warning threshold"),
            ("CPU_SPIKE",      cpu,   CPU_WARN_PCT,     f"Process CPU {cpu:.1f}% >= {CPU_WARN_PCT}%"),
        ]
        if fds is not None:
            checks.append(("FD_HIGH", fds, FD_WARN, f"Open FDs {fds} >= {FD_WARN}"))
        if tasks is not None:
            checks.append(("TASK_HIGH", tasks, TASK_WARN, f"Asyncio tasks {tasks} >= {TASK_WARN}"))
        if pool_out is not None and pool_size:
            pool_pct = (pool_out / pool_size) * 100
            if pool_pct >= POOL_WARN_PCT:
                checks.append(("POOL_WARN", pool_pct, POOL_WARN_PCT,
                                f"DB pool {pool_out}/{pool_size} ({pool_pct:.0f}%)"))
        if rss_leak:
            checks.append(("RSS_LEAK", rss, 0,
                            f"Slow RSS leak detected: {self._rss_leak_streak} consecutive windows above {LEAK_SLOPE_THRESHOLD}MB/min slope"))
        if fd_leak:
            checks.append(("FD_LEAK", fds or 0, 0,
                            f"Slow FD leak detected: {self._fd_leak_streak} consecutive windows growing"))

        for alert_type, current, threshold, message in checks:
            if current >= threshold or alert_type in ("RSS_LEAK", "FD_LEAK"):
                if alert_type not in self._active_alerts:
                    result = await db.execute(
                        text(
                            "INSERT INTO resource_alerts "
                            "(alerted_at, alert_type, metric_name, current_val, threshold, message) "
                            "VALUES (:ts, :type, :metric, :val, :thresh, :msg) "
                            "RETURNING id"
                        ),
                        {
                            "ts": now, "type": alert_type,
                            "metric": alert_type.split("_")[0].lower(),
                            "val": current, "thresh": threshold, "msg": message,
                        }
                    )
                    row = result.fetchone()
                    if row:
                        self._active_alerts[alert_type] = row.id
                    logger.warning("RESOURCE ALERT [%s]: %s", alert_type, message)
            else:
                # Resolve active alert
                if alert_type in self._active_alerts:
                    await db.execute(
                        text("UPDATE resource_alerts SET resolved_at=:ts WHERE id=:id"),
                        {"ts": now, "id": self._active_alerts.pop(alert_type)}
                    )

    # ─────────────────────────────────────────────────────────────────────
    # QUERY API — used by /health/detailed and /api/v1/observe/infra
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_recent(db, minutes: int = 60) -> list:
        """Last N minutes of samples. Used by health endpoint and ops dashboard."""
        rows = (await db.execute(
            text(
                "SELECT recorded_at, rss_mb, cpu_pct, open_fds, active_tasks, "
                "pool_checked_out, pool_size, tick_rate_hz, rss_leak_flag, rss_delta_mb "
                "FROM resource_metrics "
                "WHERE recorded_at > NOW() - INTERVAL ':m minutes' "
                "ORDER BY recorded_at DESC LIMIT 120"
                .replace(":m minutes", f"{minutes} minutes")
            )
        )).fetchall()
        return [
            {
                "ts":       r.recorded_at.isoformat(),
                "rss_mb":   r.rss_mb,
                "cpu_pct":  r.cpu_pct,
                "fds":      r.open_fds,
                "tasks":    r.active_tasks,
                "pool":     f"{r.pool_checked_out}/{r.pool_size}" if r.pool_checked_out else None,
                "ticks_hz": r.tick_rate_hz,
                "leak":     r.rss_leak_flag,
                "delta_mb": r.rss_delta_mb,
            }
            for r in rows
        ]

    @staticmethod
    async def get_open_alerts(db) -> list:
        """Active (unresolved) resource alerts. For health ribbon."""
        rows = (await db.execute(
            text(
                "SELECT alert_type, metric_name, current_val, threshold, message, alerted_at "
                "FROM resource_alerts WHERE resolved_at IS NULL "
                "ORDER BY alerted_at DESC"
            )
        )).fetchall()
        return [
            {
                "type":    r.alert_type,
                "metric":  r.metric_name,
                "current": r.current_val,
                "limit":   r.threshold,
                "message": r.message,
                "since":   r.alerted_at.isoformat(),
            }
            for r in rows
        ]
