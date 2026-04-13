"""
app/workers/strategy_executor.py  — v2

Two fixes applied vs v1:

FIX 1 — Symbol-filtered dispatch
  Before: 8 ticks/sec × 28 strategies = 224 evaluations/sec
  After:  2 ticks/sec × 7 strategies  =  14 evaluations/sec (16× reduction)
  Implementation: _symbol_map populated at register(), on_tick() only dispatches
  to strategies subscribed to the incoming symbol.

FIX 2 — Control loop independent of tick flow
  Before: intents processed only when ticks arrive — feed stall = control stall
  After:  _control_loop() runs as separate asyncio task at 200ms interval
  Feed can be completely dead. Operator presses pause. It works.
"""
import asyncio
import collections
import logging
import traceback
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import text, select, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.services.strategy_control import StrategyControlService
from app.models.db import (
    StrategyState, TradingSession, Order, OrderStatus, 
    ProductType, OrderType, OrderSide
)
from app.services.options_service import options_service

logger = logging.getLogger(__name__)
_ctrl  = StrategyControlService()

CONTROL_POLL_INTERVAL_S = 0.2   # 200ms — independent of tick rate


class StrategyExecutor:

    def __init__(self, session_factory: async_sessionmaker, broker, risk, notifier=None):
        self.session_factory = session_factory
        self.broker          = broker
        self.risk            = risk
        self.notifier        = notifier
        self._running        = False
        self._tick_task:    Optional[asyncio.Task] = None
        self._control_task: Optional[asyncio.Task] = None

        # FIX 1: symbol → [strategy_name, ...]  for O(strategies_per_symbol) dispatch
        self._symbol_map:   Dict[str, List[str]] = {}   # symbol → list of strategy names
        self._name_to_symbol: Dict[str, str]      = {}   # name → symbol
        self._registry:     Dict[str, Callable]  = {}   # name → async fn
        self._status_cache: Dict[str, str]        = {}   # name → current status

        # Bounded tick buffer per symbol — deque enforces maxlen, never grows unbounded
        self._tick_buffers: Dict[str, collections.deque] = {}

        # Signal tracking for notifications
        self._prev_signals: Dict[str, str] = {}

        # Metrics for resource monitor
        self.tick_count: int = 0

    def register(self, name: str, symbol: str, fn: Callable) -> None:
        """
        Register a strategy.

        Args:
            name:   Strategy identifier, e.g. "NIFTY_CE_BUY_01"
            symbol: Fyers symbol this strategy listens to, e.g. "NSE:NIFTY50-INDEX"
            fn:     Async callable: async def strategy(tick, candle_buf, db) -> dict
        """
        self._registry[name]  = fn
        self._name_to_symbol[name] = symbol
        self._status_cache[name] = "stopped"

        # Build reverse index: symbol → list of strategy names
        if symbol not in self._symbol_map:
            self._symbol_map[symbol] = []
            self._tick_buffers[symbol] = collections.deque(maxlen=500)  # bounded
        self._symbol_map[symbol].append(name)

        logger.info("Registered: %s → symbol=%s", name, symbol)

    async def start(self) -> None:
        async with self.session_factory() as db:
            await self._ensure_strategy_rows(db)
            await db.commit()

        self._running = True

        # FIX 2: control loop as independent task — not tied to tick flow
        self._control_task = asyncio.create_task(
            self._control_loop(), name="strategy_control_loop"
        )

        logger.info(
            "Executor started: %d strategies across %d symbols.",
            len(self._registry), len(self._symbol_map)
        )

    async def stop(self) -> None:
        self._running = False
        for task in [self._control_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        logger.info("Executor stopped.")

    # ─────────────────────────────────────────────────────────────────────
    # FIX 2: CONTROL LOOP — independent of tick flow
    # ─────────────────────────────────────────────────────────────────────
    async def _control_loop(self) -> None:
        """
        Polls for pending intents every 200ms.
        Runs independently of FeedWorker — feed can be dead, control still works.

        This is the reliability boundary: operator presses Pause in the UI,
        intent is written to DB, this loop sees it within 200ms, applies it,
        acks it. No tick required.
        """
        while self._running:
            try:
                await asyncio.sleep(CONTROL_POLL_INTERVAL_S)
                async with self.session_factory() as db:
                    intents = await _ctrl.get_pending_intents(db)
                    if intents:
                        logger.info("Control loop found %d pending intents", len(intents))
                        for intent_row in intents:
                            await self._apply_and_ack(db, intent_row)
                        await db.commit()
                    else:
                        # logger.debug("Control loop: no intents")
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Control loop error: %s", e, exc_info=True)

    async def _apply_and_ack(self, db: AsyncSession, intent_row) -> None:
        """
        Apply transition and acknowledge intent.
        
        CRITICAL: We must use the ORM object 'strategy' that was fetched in this session
        instead of raw SQL to avoid flushing/stale data issues when mixing patterns.
        """
        name   = intent_row.strategy_name
        intent = intent_row.control_intent
        status_map = {"pause": "paused", "resume": "running",
                      "stop": "stopped", "start": "running"}
        new_status = status_map.get(intent, "stopped")

        try:
            # Use ORM for cross-dialect compatibility
            result = await db.execute(
                select(StrategyState).where(StrategyState.strategy_name == name)
            )
            strategy = result.scalar_one_or_none()
            if not strategy:
                logger.error("Strategy '%s' not found during intent apply", name)
                return

            # Apply business logic based on intent
            if intent == "stop":
                strategy.auto_restart = False
            elif intent in ("resume", "start"):
                strategy.started_at = datetime.now(timezone.utc)
                strategy.error_message = None
            
            # Update the ORM record
            strategy.status = new_status
            strategy.control_intent = None # Clear the intent
            strategy.intent_acked_at = datetime.now(timezone.utc)
            strategy.updated_at = datetime.now(timezone.utc)
            
            # Update in-memory cache
            self._status_cache[name] = new_status

            logger.info(f"Executor acked intent '{intent}' for '{name}' -> {new_status}")
            
            # Flush to ensure changes are visible to the DB
            await db.flush()
            
        except Exception as e:
            logger.error(f"Failed to apply/ack intent {intent} for {name}: {e}", exc_info=True)

    # ─────────────────────────────────────────────────────────────────────
    # FIX 1: SYMBOL-FILTERED TICK DISPATCH
    # ─────────────────────────────────────────────────────────────────────
    async def on_tick(self, tick: dict) -> None:
        """
        Called by FeedWorker on every tick.

        Dispatches ONLY to strategies subscribed to tick['symbol'].
        O(strategies_per_symbol), not O(all_strategies).

        With 28 strategies across 4 symbols → ~7 strategies per tick
        vs 28 previously. 16× CPU reduction on the hot path.
        """
        self.tick_count += 1
        symbol = tick.get("symbol")
        if not symbol:
            return

        # Update bounded tick buffer for this symbol
        if symbol in self._tick_buffers:
            self._tick_buffers[symbol].append(tick)
        else:
            return  # No strategies care about this symbol

        # Only strategies subscribed to this symbol
        subscribed = self._symbol_map.get(symbol, [])
        if not subscribed:
            return

        tasks = []
        for name in subscribed:
            if self._status_cache.get(name) == "running":
                fn  = self._registry[name]
                buf = self._tick_buffers[symbol]
                tasks.append(self._run_safe(name, fn, tick, buf))

        if tasks:
            # gather — all strategies for this symbol run concurrently
            # return_exceptions=True — one strategy crash doesn't block others
            results = await asyncio.gather(*tasks, return_exceptions=True)
            async with self.session_factory() as db:
                for name, result in zip(
                    [n for n in subscribed if self._status_cache.get(n) == "running"],
                    results
                ):
                    if isinstance(result, Exception):
                        await self._handle_error(db, name, result)
                    elif isinstance(result, dict):
                        await self._update_metrics(db, name, result)
                await db.commit()

    async def _run_safe(
        self, name: str, fn: Callable, tick: dict, buf: collections.deque
    ) -> Optional[dict]:
        """
        Run one strategy. Returns metrics dict or raises.
        Strategy signature: async def fn(tick, tick_buffer, db) -> dict
        tick_buffer is the bounded deque for this symbol — strategies
        use it for candle construction without needing a DB round-trip.
        """
        async with self.session_factory() as db:
            return await fn(tick, buf, db, self.broker, self.risk)

    async def _handle_error(self, db: AsyncSession, name: str, exc: Exception) -> None:
        tb = traceback.format_exc() if isinstance(exc, Exception) else str(exc)
        logger.error("Strategy '%s' error: %s", name, exc)
        
        result = await db.execute(
            select(StrategyState).where(StrategyState.strategy_name == name)
        )
        strategy = result.scalar_one_or_none()
        if not strategy: return

        strategy.status = "error"
        strategy.error_message = str(exc)[:500]
        strategy.error_trace = tb[:4000]
        strategy.error_count += 1
        from sqlalchemy.sql import func
        strategy.last_error_at = func.now()
        
        self._status_cache[name] = "error"
        if strategy.auto_restart and strategy.restart_count < 5:
            asyncio.create_task(self._auto_restart(name, delay_s=30))
        elif strategy.restart_count >= 5:
            strategy.auto_restart = False
            logger.critical("Strategy '%s' auto-restart disabled after 5 failures.", name)

    async def _auto_restart(self, name: str, delay_s: int) -> None:
        await asyncio.sleep(delay_s)
        async with self.session_factory() as db:
            from datetime import datetime, timezone
            result = await db.execute(
                text(
                    "UPDATE strategy_states SET status='running', error_message=NULL, "
                    "error_trace=NULL, restart_count=restart_count+1, "
                    "started_at=:start_time, updated_at=:now "
                    "WHERE strategy_name=:n AND status='error' RETURNING restart_count"
                ),
                {"n": name, "start_time": datetime.now(timezone.utc).isoformat(), "now": datetime.now(timezone.utc)}
            )
            row = result.fetchone()
            if row:
                self._status_cache[name] = "running"
                await db.commit()
                logger.info("Auto-restarted '%s' (attempt %d).", name, row.restart_count)

    async def _update_metrics(self, db: AsyncSession, name: str, m: dict) -> None:
        result = await db.execute(
            select(StrategyState).where(StrategyState.strategy_name == name)
        )
        strategy = result.scalar_one_or_none()
        if not strategy: return

        # INTERCEPT AND RESOLVE OPTIONS LEGS
        target_instrument = m.get("target_instrument")
        if target_instrument and (m.get("signal") in ["BUY", "SELL", "SHORT"] or m.get("signal", "").startswith("EXIT_")):
            if target_instrument.get("type") == "OPTION":
                spot_price = m.get("ltp")
                leg_type = target_instrument.get("leg") # CE or PE
                if spot_price and leg_type:
                    opt_symbol = await options_service.get_atm_option_symbol(spot_price, leg_type)
                    if opt_symbol:
                        m["target_symbol"] = opt_symbol
                        logger.info(f"Strategy {name} dynamically targeting option {opt_symbol} at spot {spot_price}")

        strategy.pnl = m.get("pnl", 0)
        strategy.open_qty = m.get("open_qty", 0)
        strategy.avg_entry = m.get("avg_entry")
        strategy.ltp = m.get("ltp")
        strategy.net_delta = m.get("delta", 0)
        strategy.drawdown_pct = m.get("drawdown_pct", 0)
        strategy.risk_pct = m.get("risk_pct", 0)
        strategy.direction_bias = m.get("direction", "NEUTRAL")
        strategy.current_signal = m.get("signal", "FLAT")
        strategy.win_rate = m.get("win_rate", 0)
        strategy.total_trades = m.get("trades", 0)
        strategy.thought_process = m.get("thought_process")
        strategy.stop_loss = m.get("stop_loss")
        strategy.target_price = m.get("target_price")

        # Telegram Notifications on Signal Change AND Live Order Execution
        new_sig = m.get("signal", "FLAT")
        old_sig = self._prev_signals.get(name)
        
        if self.notifier and old_sig and new_sig != old_sig:
            final_target_symbol = m.get("target_symbol") or strategy.symbol or "NIFTY"
            
            # Entry Alert and LIVE ORDER PLACEMENT
            if new_sig in ("BUY", "SELL"):
                # 1. Determine execution parameters
                # Use qty from strategy signal; fallback is current NIFTY lot size (65)
                qty = m.get("open_qty", 65)
                # Fyers logic: 1 = BUY, -1 = SELL
                broker_side = 1 if new_sig == "BUY" else -1
                
                from datetime import date
                import uuid
                today = date.today().isoformat()
                ts_query = await db.execute(select(TradingSession).where(TradingSession.date == today))
                session_obj = ts_query.scalar_one_or_none()
                
                if not session_obj:
                    logger.warning(f"No Active TradingSession found for {today}. Auto-creating one.")
                    session_obj = TradingSession(
                        id=str(uuid.uuid4()),
                        date=today,
                        max_daily_loss=10000.0,
                        max_position_size=100,
                        max_open_orders=10,
                        max_margin_usage_pct=80.0,
                        max_lot_size=5
                    )
                    db.add(session_obj)
                    await db.flush()
                
                if False: # Dummy to maintain indentation if needed, but not actually needed because we now always have a session block. Better to just proceed.
                    pass
                else:
                    idempotency_key = f"{name}_{new_sig}_{datetime.now(timezone.utc).strftime('%H%M%S')}_{str(uuid.uuid4())[:8]}"
                    # 3. Call RiskEngine
                    db_side = OrderSide.BUY if new_sig == "BUY" else OrderSide.SELL
                    risk_result = await self.risk.validate_order(
                        db=db,
                        session=session_obj,
                        symbol=final_target_symbol,
                        side=db_side.value,
                        quantity=qty,
                        order_type="MARKET",
                        price=m.get("ltp"),
                        product_type="INTRADAY",
                        idempotency_key=idempotency_key
                    )
                    
                    # RISK BLOCK REMOVED per user request - always proceed
                    if not risk_result.approved:
                        logger.warning(f"RISK VIOLATION IGNORED for {name}: {risk_result.message}")
                    
                    if True: # Risk bypass
                        # 4. Write Order to DB
                        new_order = Order(
                            id=str(uuid.uuid4()),
                            session_id=session_obj.id,
                            idempotency_key=idempotency_key,
                            symbol=final_target_symbol,
                            display_symbol=final_target_symbol,
                            side=db_side,
                            order_type=OrderType.MARKET,
                            product_type=ProductType.INTRADAY,
                            quantity=qty,
                            price=m.get("ltp"),
                            status=OrderStatus.PENDING,
                            risk_snapshot=risk_result.snapshot
                        )
                        db.add(new_order)
                        await db.flush()
                        
                        # 5. Dispatch to Broker
                        order_data = {
                            "symbol": final_target_symbol,
                            "qty": qty,
                            "type": 2, # MARKET
                            "side": broker_side,
                            "productType": "INTRADAY",
                            "limitPrice": 0,
                            "stopPrice": 0,
                            "validity": "DAY",
                            "disclosedQty": 0,
                            "offlineOrder": False
                        }
                        
                        try:
                            # Actually place the live order
                            broker_resp = await self.broker.submit_order(order_data)
                            logger.info(f"Live Broker Response for {name}: {broker_resp}")
                            
                            # Success response format check
                            if broker_resp.get("s") == "ok":
                                order_id_fyers = broker_resp.get("id")
                                new_order.broker_order_id = order_id_fyers
                                new_order.status = OrderStatus.ACKNOWLEDGED
                                new_order.sent_at = datetime.now(timezone.utc)
                                new_order.acked_at = datetime.now(timezone.utc)
                                new_order.status_history = [{"status": "ACKNOWLEDGED", "time": datetime.now(timezone.utc).isoformat(), "actor": "SYSTEM", "reason": "Fyers API accept"}]
                                
                                # Send Success Telegram Alert
                                risk_tag = " (⚠️ OVERRIDE)" if not risk_result.approved else ""
                                asyncio.create_task(self.notifier.send_message(
                                    f"✅ *ENTRY EXECUTED*{risk_tag}: `{name}`\n"
                                    f"• Action: {new_sig} {qty}x {final_target_symbol}\n"
                                    f"• Spot LTP: {m.get('ltp', 0)}\n"
                                    f"• Broker ID: `{order_id_fyers}`"
                                ))
                            else:
                                new_order.status = OrderStatus.REJECTED
                                new_order.reject_reason = broker_resp.get("message", "Broker rejection")
                                new_order.status_history = [{"status": "REJECTED", "time": datetime.now(timezone.utc).isoformat(), "actor": "BROKER", "reason": new_order.reject_reason}]
                                asyncio.create_task(self.notifier.send_message(
                                    f"❌ *BROKER REJECTED*: `{name}`\n"
                                    f"• Action: {new_sig} {qty}x {final_target_symbol}\n"
                                    f"• Reason: {new_order.reject_reason}"
                                ))
                        except Exception as e:
                            logger.error(f"Error submitting live order to broker: {e}")
                            new_order.status = OrderStatus.REJECTED
                            new_order.reject_reason = f"Exception during submission: {str(e)}"
                            asyncio.create_task(self.notifier.send_message(
                                f"❌ *SYSTEM ERROR*: Failed to route `{name}` order to broker.\n"
                                f"• Exception: {str(e)}"
                            ))

            # Exit Alert and LIVE ORDER PLACEMENT
            elif new_sig.startswith("EXIT_"):
                # 1. Determine execution parameters
                # Use qty from strategy signal; fallback is current NIFTY lot size (65)
                qty = m.get("open_qty", 65)
                # Opposite side to exit: If we were Long (BUY), we now SELL to exit (-1). If Short, BUY to exit (1).
                direction = m.get("direction", "NEUTRAL") 
                if direction in ("LONG", "BUY"):
                    broker_side = -1 # SELL
                    db_side = OrderSide.SELL
                elif direction in ("SHORT", "SELL"):
                    broker_side = 1  # BUY
                    db_side = OrderSide.BUY
                else: 
                     # Fallback if direction wasn't explicitly passed, we guess based on exit logic or skip.
                    logger.warning(f"Could not conclusively determine exit side for {name}. Defaulting to SELL to close.")
                    broker_side = -1 
                    db_side = OrderSide.SELL
                    
                from datetime import date
                import uuid
                today = date.today().isoformat()
                ts_query = await db.execute(select(TradingSession).where(TradingSession.date == today))
                session_obj = ts_query.scalar_one_or_none()
                
                if not session_obj:
                    logger.warning(f"No Active TradingSession found for {today}. Auto-creating one.")
                    session_obj = TradingSession(
                        id=str(uuid.uuid4()),
                        date=today,
                        max_daily_loss=10000.0,
                        max_position_size=100,
                        max_open_orders=10,
                        max_margin_usage_pct=80.0,
                        max_lot_size=5
                    )
                    db.add(session_obj)
                    await db.flush()
                
                if False:
                    pass
                else:
                    idempotency_key = f"{name}_{new_sig}_{datetime.now(timezone.utc).strftime('%H%M%S')}_{str(uuid.uuid4())[:8]}"
                    # 3. Call RiskEngine
                    risk_result = await self.risk.validate_order(
                        db=db,
                        session=session_obj,
                        symbol=final_target_symbol,
                        side=db_side.value,
                        quantity=qty,
                        order_type="MARKET",
                        price=m.get("ltp"),
                        product_type="INTRADAY",
                        idempotency_key=idempotency_key
                    )
                    
                    # RISK BLOCK REMOVED per user request - always proceed
                    if not risk_result.approved:
                        logger.warning(f"RISK VIOLATION IGNORED (EXIT) for {name}: {risk_result.message}")
                    
                    if True: # Risk bypass
                        # 4. Write Order to DB
                        new_order = Order(
                            id=str(uuid.uuid4()),
                            session_id=session_obj.id,
                            idempotency_key=idempotency_key,
                            symbol=final_target_symbol,
                            display_symbol=final_target_symbol,
                            side=db_side,
                            order_type=OrderType.MARKET,
                            product_type=ProductType.INTRADAY,
                            quantity=qty,
                            price=m.get("ltp"),
                            status=OrderStatus.PENDING,
                            risk_snapshot=risk_result.snapshot
                        )
                        db.add(new_order)
                        await db.flush()
                        
                        # 5. Dispatch to Broker
                        order_data = {
                            "symbol": final_target_symbol,
                            "qty": qty,
                            "type": 2, # MARKET
                            "side": broker_side,
                            "productType": "INTRADAY",
                            "limitPrice": 0,
                            "stopPrice": 0,
                            "validity": "DAY",
                            "disclosedQty": 0,
                            "offlineOrder": False
                        }
                        
                        try:
                            # Actually place the live order
                            broker_resp = await self.broker.submit_order(order_data)
                            logger.info(f"Live Broker RESPONSE (EXIT) for {name}: {broker_resp}")
                            
                            if broker_resp.get("s") == "ok":
                                order_id_fyers = broker_resp.get("id")
                                new_order.broker_order_id = order_id_fyers
                                new_order.status = OrderStatus.ACKNOWLEDGED
                                new_order.sent_at = datetime.now(timezone.utc)
                                new_order.acked_at = datetime.now(timezone.utc)
                                new_order.status_history = [{"status": "ACKNOWLEDGED", "time": datetime.now(timezone.utc).isoformat(), "actor": "SYSTEM", "reason": "Fyers API accept"}]
                                
                                # Send Success Telegram Alert
                                risk_tag = " (⚠️ OVERRIDE)" if not risk_result.approved else ""
                                asyncio.create_task(self.notifier.alert_exit(
                                    strategy=name,
                                    symbol=final_target_symbol,
                                    side=m.get("direction", "NEUTRAL"),
                                    price=m.get("ltp", 0),
                                    pnl=m.get("pnl", 0),
                                    reason=f"{new_sig} Executed{risk_tag} (Broker ID: {order_id_fyers})"
                                ))
                            else:
                                new_order.status = OrderStatus.REJECTED
                                new_order.reject_reason = broker_resp.get("message", "Broker rejection")
                                new_order.status_history = [{"status": "REJECTED", "time": datetime.now(timezone.utc).isoformat(), "actor": "BROKER", "reason": new_order.reject_reason}]
                                asyncio.create_task(self.notifier.send_message(
                                    f"❌ *BROKER REJECTED EXIT*: `{name}`\n"
                                    f"• Action: {new_sig} {qty}x {final_target_symbol}\n"
                                    f"• Reason: {new_order.reject_reason}"
                                ))
                        except Exception as e:
                            logger.error(f"Error submitting live EXIT order to broker: {e}")
                            new_order.status = OrderStatus.REJECTED
                            new_order.reject_reason = f"Exception during submission: {str(e)}"
                            asyncio.create_task(self.notifier.send_message(
                                f"❌ *SYSTEM ERROR*: Failed to route `{name}` EXIT to broker.\n"
                                f"• Exception: {str(e)}"
                            ))
        
        self._prev_signals[name] = new_sig
        if m.get("last_trade_at"):
            strategy.last_trade_at = m.get("last_trade_at")
        
        from sqlalchemy.sql import func
        strategy.updated_at = func.now()

    async def _ensure_strategy_rows(self, db: AsyncSession) -> None:
        """Ensure each registered strategy has a row in the DB."""
        for name in self._registry:
            # Cross-dialect check and insert using ORM model for automatic defaults
            result = await db.execute(
                select(StrategyState).where(StrategyState.strategy_name == name)
            )
            if not result.fetchone():
                db.add(StrategyState(
                    strategy_name=name, 
                    symbol=self._name_to_symbol.get(name),
                    status="stopped"
                ))
                import sqlalchemy.exc
                try:
                    await db.flush()
                except sqlalchemy.exc.IntegrityError:
                     # Gunicorn worker race condition: another worker inserted this exact row
                     # Just rollback the internal transaction state so we can continue
                    await db.rollback()
            
            # Refresh cache
            result = await db.execute(
                select(StrategyState.status).where(StrategyState.strategy_name == name)
            )
            row = result.fetchone()
            if row:
                self._status_cache[name] = row[0]
