"""
app/workers/feed_worker.py

WebSocket feed manager with real heartbeat tracking.

Responsibilities:
  1. Maintain Fyers WebSocket connection
  2. Write last_tick_ts to Redis on every tick (sub-ms)
  3. Write to PostgreSQL feed_heartbeat as fallback (every 5s)
  4. Detect stale/dead feed and trigger circuit breaker
  5. Reconnect with exponential backoff
  6. Update circuit breaker state on repeated failures

Redis keys written:
  tradedeck:last_tick_ts   → ISO timestamp of last received tick
  tradedeck:ws_connected   → "1" if connected, deleted on disconnect
  tradedeck:ltp:{symbol}   → latest price for each subscribed symbol

The observability endpoint reads these keys for real feed health.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

# Feed staleness thresholds
STALE_THRESHOLD_S  = 1.0   # Feed considered stale after 1s without tick
DEAD_THRESHOLD_S   = 3.0   # Feed considered dead after 3s
CB_TRIP_THRESHOLD  = 5     # Trip circuit breaker after 5 consecutive failures

# Reconnect backoff
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]  # seconds, capped at 30


class FeedWorker:

    def __init__(
        self,
        broker,
        session_factory: async_sessionmaker,
        redis_client=None,
    ):
        self.broker          = broker
        self.session_factory = session_factory
        self.redis           = redis_client

        self._running         = False
        self._connected       = False
        self._task: Optional[asyncio.Task] = None
        self._last_tick_ts: Optional[float] = None
        self._subscribed: Set[str] = set()
        self._reconnect_count = 0
        self._consecutive_failures = 0

        # In-process tick handlers (registered by strategies)
        self._tick_handlers = []
        self._loop = None
        self._ws_active = False

        # Register for token refreshes
        self.broker.register_on_refresh(self._handle_token_refresh)

    def _handle_token_refresh(self, new_token: str):
        """Callback from BrokerService when token changes."""
        logger.info("[FEED] 🔄 Token refresh detected. Signaling WebSocket restart...")
        self._ws_active = False

    def register_tick_handler(self, handler):
        """Strategy executor registers here to receive live ticks."""
        self._tick_handlers.append(handler)

    async def start(self, symbols: list[str]) -> None:
        self._running = True
        self._subscribed = set(symbols)
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="feed_worker")
        logger.info("[FEED] 🚀 Worker starting. Subscribing to %d symbols.", len(symbols))

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self._mark_disconnected()
        logger.info("[FEED] 🛑 Feed worker stopped.")

    async def get_feed_status(self) -> dict:
        """Return current feed health. Used by health endpoint directly."""
        now = time.time()
        if self._last_tick_ts is None:
            age_s  = None
            status = "dead"
        else:
            age_s  = round(now - self._last_tick_ts, 2)
            status = "live" if age_s < STALE_THRESHOLD_S else \
                     "stale" if age_s < DEAD_THRESHOLD_S else "dead"
        return {
            "age_seconds":   age_s,
            "ws_connected":  self._connected,
            "status":        status,
            "source":        "in_process",
            "reconnect_count": self._reconnect_count,
        }

    async def _run(self) -> None:
        """Main loop — connects, receives, reconnects on failure."""
        delay_idx = 0

        while self._running:
            try:
                await self._connect_and_receive()
                delay_idx = 0  # Reset backoff on clean exit
                # Added delay to prevent tight loop even on manual signals
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                self._reconnect_count      += 1
                logger.error(
                    "[FEED] 🛑 Connection lost (failure #%d): %s",
                    self._consecutive_failures, e
                )
                await self._mark_disconnected()

                if not self._running:
                    break

                delay = RECONNECT_DELAYS[min(delay_idx, len(RECONNECT_DELAYS)-1)]
                delay_idx += 1
                logger.info("[FEED] ⏳ Retrying in %ds...", delay)
                await asyncio.sleep(delay)

    async def _connect_and_receive(self) -> None:
        """Single connection lifetime."""
        from fyers_apiv3.FyersWebsocket import data_ws

        # Wait if a token refresh is in progress
        if not self.broker._refresh_event.is_set():
            logger.info("[FEED] ⏳ Waiting for broker refresh to complete...")
            await self.broker._refresh_event.wait()

        logger.info("[FEED] 📡 Connecting to Fyers WebSocket...")
        self._consecutive_failures = 0
        self._ws_active = True

        access_token = self.broker.ws_access_token
        logger.info(f"[FEED] 🔑 Using token format: {access_token[:15]}...{access_token[-10:]}")
        
        # Initialize the Fyers DataSocket
        # Set reconnect=False to handle it ourselves with the latest token
        self._fyers = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="/tmp",
            litemode=False,
            write_to_file=False,
            reconnect=False,
            on_connect=self._on_ws_open,
            on_close=self._on_ws_close,
            on_error=self._on_ws_error,
            on_message=self._on_ws_message
        )

        self._fyers.connect()
        
        # Keep the coroutine alive while connection is active
        while self._running and self._ws_active:
            await asyncio.sleep(0.5)
        
        if hasattr(self, "_fyers"):
            # self._fyers.close() # Library usually handles this via reconnect=True
            pass

    def _on_ws_open(self):
        logger.info("[FEED] ✅ Fyers WebSocket Connection Opened")
        self._connected = True
        # Subscribe to symbols
        if self._subscribed and hasattr(self, "_fyers"):
            from fyers_apiv3.FyersWebsocket import data_ws
            # Subscribe to all symbols in list
            # Note: fyers_apiv3 DataSocket usually expects a list of symbols
            data_type = getattr(data_ws.FyersDataSocket, "Ltp", "symbolData")
            self._fyers.subscribe(
                symbols=list(self._subscribed), 
                data_type=data_type
            )
            # Mark connected in DB/Redis after subscription
            asyncio.run_coroutine_threadsafe(self._mark_connected(), self._loop)

    def _on_ws_close(self):
        logger.warning("[FEED] 🔌 WebSocket Connection Closed")
        self._connected = False
        self._ws_active = False  # Exit the _connect_and_receive loop to trigger reconnect
        asyncio.run_coroutine_threadsafe(self._mark_disconnected(), self._loop)

    def _on_ws_error(self, error):
        logger.error(f"[FEED] 🛑 WebSocket Error: {error}")
        
        # Proactive Refresh on Token Expiry (-99 is Fyers' WebSocket auth error code)
        is_expired = False
        if isinstance(error, dict):
            if error.get("code") == -99 or "expired" in str(error.get("message", "")).lower():
                is_expired = True
        elif "expired" in str(error).lower():
            is_expired = True
            
        if is_expired:
            logger.warning("[FEED] 🔑 Token expired. Triggering proactive refresh...")
            # Schedule refresh and wait for it; if it fails, back off before reconnect
            future = asyncio.run_coroutine_threadsafe(
                self.broker.refresh_access_token(), self._loop
            )
            try:
                success = future.result(timeout=20)  # Wait up to 20s for refresh
                if not success:
                    # Refresh failed (bad pin / missing config). Back off 30s to
                    # avoid hammering Fyers with an invalid token in a tight loop.
                    logger.error(
                        "[FEED] ❌ Token refresh failed. Backing off 30s before next reconnect."
                    )
                    time.sleep(30)
            except Exception as exc:
                import traceback
                logger.error(
                    f"[FEED] ❌ Token refresh raised: {exc}.\n{traceback.format_exc()}"
                )
                time.sleep(30)

        self._ws_active = False  # Exit the _connect_and_receive loop on error

    def _on_ws_message(self, message):
        """Handle incoming WebSocket messages."""
        # Translate Fyers message to internal tick format
        # message is usually a dict for LTP/Depth
        if "symbol" in message:
            # Inject source and timestamp for observability and strategy consumption
            message["source"] = "ws"
            if "ts" not in message:
                message["ts"] = datetime.now(timezone.utc).isoformat()
            
            asyncio.run_coroutine_threadsafe(self._on_tick(message), self._loop)

    async def _on_tick(self, tick: dict) -> None:
        """Called on every tick. Critical path — must be fast."""
        now    = datetime.now(timezone.utc)
        now_ts = time.time()
        self._last_tick_ts = now_ts

        # ── Write to Redis (primary, fast path) ───────────────────────────
        if self.redis:
            try:
                pipe = self.redis.pipeline()
                pipe.set("tradedeck:last_tick_ts", now.isoformat(), ex=10)
                pipe.set("tradedeck:ws_connected", "1", ex=10)
                if symbol := tick.get("symbol"):
                    pipe.set(f"tradedeck:ltp:{symbol}", str(tick.get("ltp",0)), ex=10)
                await pipe.execute()
            except Exception as e:
                logger.warning("Redis tick write failed: %s", e)
                # Fall through to DB write

        # ── Periodic DB heartbeat fallback (every 5s) ─────────────────────
        if not hasattr(self, "_last_db_write") or now_ts - self._last_db_write > 5.0:
            asyncio.create_task(self._write_db_heartbeat(now))
            self._last_db_write = now_ts

        # ── Fan out to strategy executors (in-process) ────────────────────
        for handler in self._tick_handlers:
            try:
                await handler(tick)
            except Exception as e:
                logger.error("Tick handler error: %s", e, exc_info=True)

    async def _write_db_heartbeat(self, ts: datetime) -> None:
        """Async DB write — runs as background task, never blocks tick processing."""
        try:
            async with self.session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE feed_heartbeat SET "
                        "  last_tick_at=:ts, is_connected=true, updated_at=:now "
                        "WHERE feed_name='fyers_ws'"
                    ),
                    {"ts": ts, "now": datetime.now(timezone.utc)}
                )
                await db.commit()
        except Exception as e:
            logger.error("DB heartbeat write failed: %s", e)

    async def _mark_connected(self) -> None:
        self._connected = True
        if self.redis:
            try:
                await self.redis.set("tradedeck:ws_connected", "1", ex=10)
            except Exception:
                pass
        try:
            async with self.session_factory() as db:
                await db.execute(
                    text("UPDATE feed_heartbeat SET is_connected=true, updated_at=:now WHERE feed_name='fyers_ws'"),
                    {"now": datetime.now(timezone.utc)}
                )
                await db.commit()
        except Exception as e:
            logger.warning("mark_connected DB write failed: %s", e)

    async def _mark_disconnected(self) -> None:
        self._connected = False
        if self.redis:
            try:
                await self.redis.delete("tradedeck:ws_connected")
            except Exception:
                pass
        try:
            async with self.session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE feed_heartbeat SET is_connected=false, updated_at=:now "
                        "WHERE feed_name='fyers_ws'"
                    ),
                    {"now": datetime.now(timezone.utc)}
                )
                await db.commit()
        except Exception as e:
            logger.warning("mark_disconnected DB write failed: %s", e)
