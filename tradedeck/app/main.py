import logging
import uuid
import redis.asyncio as redis
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqlalchemy import text, select

from app.api.routes import health
from app.models.db import Base
from app.core.database import async_session, engine
from app.core.observability import configure_logging, get_metrics_output
from app.services.broker_service import BrokerService
from app.services.risk_engine import RiskEngine
from app.workers.reconciliation import ReconciliationWorker
from app.workers.strategy_executor import StrategyExecutor
from app.workers.feed_worker import FeedWorker
from app.services.notification_service import NotificationService
from app.services.reporting_service import StrategyReportingService
from app.services.mongodb_service import MongoDBService
from app.workers.telegram_worker import TelegramWorker
from app.strategies.failed_auction import get_strategy as get_failed_auction
from app.strategies.statistical_sniper import get_strategy as get_stat_sniper
from app.strategies.ib_production_bridge import get_strategy as get_ib_breakout
from app.strategies.initial_balance_breakout_strategy import get_strategy as get_ib_scanner
from app.strategies.bb_squeeze_strategy import get_strategy as get_bb_squeeze
from app.core.config import settings

# Configure structured logging
configure_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure tables exist (especially important for local SQLite on Render)
    if "sqlite" in engine.url.drivername:
        logger.info("[SYSTEM] 🛠️ Ensuring SQLite tables exist...")
        async with engine.begin() as conn:
            import sqlalchemy.exc
            try:
                # Explicit table creation for audit_logs to fix missing table error on some SQLite versions
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id VARCHAR(60) PRIMARY KEY,
                        session_id VARCHAR(60),
                        event_type VARCHAR(60) NOT NULL,
                        entity_type VARCHAR(30),
                        entity_id VARCHAR(100),
                        actor VARCHAR(100),
                        ip_address VARCHAR(45),
                        payload JSON,
                        created_at DATETIME NOT NULL
                    )
                """))
                await conn.run_sync(Base.metadata.create_all)
                logger.info("[SYSTEM] ✅ SQLite tables verified/created.")
            except sqlalchemy.exc.OperationalError as e:
                if "already exists" in str(e):
                    logger.debug("[SYSTEM] ℹ️ Tables already exist.")
                elif "database is locked" in str(e):
                    logger.warning("[SYSTEM] ⚠️ Database locked during create_all.")
                else:
                    logger.error(f"[SYSTEM] 🛑 Unexpected SQLite error: {e}")
                    raise

    # 2. Ensure TradingSession for today exists
    from datetime import date
    from app.models.db import TradingSession
    try:
        today = date.today().isoformat()
        async with async_session() as db:
            result = await db.execute(select(TradingSession).where(TradingSession.date == today))
            session_obj = result.scalar_one_or_none()
            if not session_obj:
                logger.info(f"[SYSTEM] 📅 Creating new TradingSession for {today}")
                new_session = TradingSession(
                    date=today,
                    id=str(uuid.uuid4()),
                    max_daily_loss=10000.0,
                    max_position_size=100,
                    max_open_orders=20,
                    max_margin_usage_pct=80.0
                )
                db.add(new_session)
                await db.commit()
            else:
                logger.info(f"[SYSTEM] 📅 Active TradingSession found for {today}")
    except Exception as e:
        logger.error(f"[SYSTEM] 🛑 Failed to ensure TradingSession: {e}")

    # 3. Record system startup in audit logs
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO audit_logs (id, event_type, created_at) VALUES (:i, :e, :ts)"),
                {"i": str(uuid.uuid4()), "e": "SYSTEM_STARTUP", "ts": datetime.now(timezone.utc).replace(tzinfo=None)}
            )
        logger.info("[SYSTEM] 📝 Startup audit log recorded.")
    except Exception as e:
        logger.warning(f"[SYSTEM] ⚠️ Could not record startup audit log: {e}")
    
    # 4. Ensure Feed Heartbeat row exists
    try:
        async with async_session() as db:
            from app.models.db import FeedHeartbeat
            result = await db.execute(select(FeedHeartbeat).where(FeedHeartbeat.feed_name == "fyers_ws"))
            if not result.scalar_one_or_none():
                logger.info("[SYSTEM] 📡 Seeding initial heartbeat row for 'fyers_ws'")
                db.add(FeedHeartbeat(
                    feed_name="fyers_ws",
                    last_tick_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    is_connected=False
                ))
                await db.commit()
    except Exception as e:
        logger.warning(f"[SYSTEM] ⚠️ Could not initialize feed heartbeat row: {e}")
    
    # 3.5 Initialize Redis for global coordination (April 2026 fix)
    try:
        if settings.REDIS_URL:
            redis_client = redis.from_url(
                settings.REDIS_URL,
                password=settings.REDIS_PASSWORD,
                encoding="utf-8",
                decode_responses=True
            )
            await redis_client.ping()
            app.state.redis = redis_client
            logger.info("[SYSTEM] 🟢 Redis connected and registered to app.state")
        else:
            app.state.redis = None
            logger.warning("[SYSTEM] ⚠️ REDIS_URL not found; skipping Redis initialization")
    except Exception as e:
        logger.warning(f"[SYSTEM] ⚠️ Could not connect to Redis: {e}")
        app.state.redis = None

    # Initialize services
    notifier = NotificationService(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
    mongo = MongoDBService(settings.MONGO_URI)
    await mongo.connect() # Connect MongoDB early as BrokerService might need it
    
    # Initialize broker with mongo and redis, then call .initialize() to sync tokens
    broker = BrokerService(mongo_service=mongo, redis_client=getattr(app.state, "redis", None))
    await broker.initialize()

    risk = RiskEngine(broker)
    reporting = StrategyReportingService(async_session, mongo_service=mongo)
    
    # Initialize Workers
    reconciler = ReconciliationWorker(broker, risk, async_session)
    executor = StrategyExecutor(async_session, broker, risk, notifier=notifier)
    feed = FeedWorker(broker, async_session, redis_client=getattr(app.state, "redis", None))
    tg_worker = TelegramWorker(notifier, reporting)

    # Register Strategies
    # Note: Use specific instance-bound methods for signals
    executor.register("STAT_SNIPER_01", "NSE:NIFTY50-INDEX", get_stat_sniper())
    executor.register("BB_SQUEEZE_01", "NSE:NIFTY50-INDEX", get_bb_squeeze())

    # Wire Feed to Executor
    feed.register_tick_handler(executor.on_tick)
    
    # Start Background Workers
    await reconciler.start()
    await executor.start()
    await feed.start(symbols=["NSE:NIFTY50-INDEX"])
    await tg_worker.start()
    # NOTE: mongo.connect() was already called above (line ~102) — do NOT call again here
    
    # Send Startup Message
    await notifier.send_message("🚀 *TradeDeck v2 Production* components initialized and background tasks started.")
    
    app.state.executor = executor
    app.state.feed = feed
    app.state.broker = broker
    
    logger.info("[SYSTEM] ⚙️ Bot components initialized and background tasks started.")
    
    yield
    
    # Cleanup
    await tg_worker.stop()
    await mongo.close()
    if getattr(app.state, "redis", None):
        await app.state.redis.close()
    await feed.stop()
    await executor.stop()
    await reconciler.stop()
    logger.info("[SYSTEM] 🛑 Bot components shut down.")

app = FastAPI(
    title="TradeDeck v2 Bot",
    description="Automated Trading Bot with Risk Controls and Reconciliation",
    version="2.0.0",
    lifespan=lifespan
)

from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import health, observability

# Include Routers
app.include_router(health.router)
app.include_router(observability.router)

# Enable CORS — allow all origins in production (AWS/Render/Docker)
# The frontend is served from the same origin via Nginx reverse proxy,
# so wildcard is safe here. Restrict further if embedding in a 3rd-party site.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # Must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
import traceback
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Global exception handler — surfaces actual errors instead of blank 500s ──
@app.exception_handler(Exception)
async def global_exception_handler(request: FastAPIRequest, exc: Exception):
    tb = traceback.format_exc()
    logger.error(f"[UNHANDLED] {request.method} {request.url} → {exc}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc),
            "type": type(exc).__name__,
            "path": str(request.url.path),
        }
    )

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics_output(), media_type="text/plain")

# Serve UI if built folder exists from Docker
if os.path.exists("frontend_dist"):
    app.mount("/", StaticFiles(directory="frontend_dist", html=True), name="ui")
elif os.path.exists("dist"):
    app.mount("/", StaticFiles(directory="dist", html=True), name="ui")
else:
    @app.get("/")
    async def root():
        return {
            "service": "tradedeck-bot",
            "status": "online",
            "ui_status": "missing_frontend_dist_folder",
            "docs": "/docs"
        }

if __name__ == "__main__":
    import uvicorn
    # In local mode, we bind to localhost for security
    uvicorn.run(app, host="127.0.0.1", port=8000)
