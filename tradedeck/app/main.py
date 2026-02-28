import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqlalchemy import text

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
from app.core.config import settings

# Configure structured logging
configure_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure tables exist (especially important for local SQLite on Render)
    if "sqlite" in engine.url.drivername:
        logger.info("Lifespan: Ensuring SQLite tables exist...")
        async with engine.begin() as conn:
            import sqlalchemy.exc
            try:
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Lifespan: SQLite tables verified/created.")
            except sqlalchemy.exc.OperationalError as e:
                if "already exists" in str(e):
                    logger.debug("Lifespan: Tables already exist, moving on.")
                elif "database is locked" in str(e):
                    logger.warning("Lifespan: Database locked during create_all; ignoring as another worker likely finished.")
                else:
                    logger.error(f"Lifespan: Unexpected SQLite error: {e}")
                    raise

    # 2. Record system startup in audit logs
    try:
        import uuid
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO audit_logs (id, event_type, created_at) VALUES (:i, :e, :ts)"),
                {"i": str(uuid.uuid4()), "e": "SYSTEM_STARTUP", "ts": datetime.now(timezone.utc)}
            )
        logger.info("Lifespan: Startup audit log recorded.")
    except Exception as e:
        # Don't let audit log failure crash the whole app
        logger.warning(f"Lifespan: Could not record startup audit log: {e}")
    
    # Initialize services
    broker = BrokerService()
    risk = RiskEngine(broker)
    notifier = NotificationService(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
    mongo = MongoDBService(settings.MONGO_URI)
    reporting = StrategyReportingService(async_session, mongo_service=mongo)
    
    # Initialize Workers
    reconciler = ReconciliationWorker(broker, risk, async_session)
    executor = StrategyExecutor(async_session, broker, risk, notifier=notifier)
    feed = FeedWorker(broker, async_session)
    tg_worker = TelegramWorker(notifier, reporting)

    # Register Strategies
    # Note: Use specific instance-bound methods for signals
    executor.register("FAILED_AUCTION_B1", "NSE:NIFTY50-INDEX", get_failed_auction())
    executor.register("STAT_SNIPER_01", "NSE:NIFTY50-INDEX", get_stat_sniper())

    # Wire Feed to Executor
    feed.register_tick_handler(executor.on_tick)
    
    # Start Background Workers
    await reconciler.start()
    await executor.start()
    await feed.start(symbols=["NSE:NIFTY50-INDEX"])
    await tg_worker.start()
    await mongo.connect()
    
    # Send Startup Message
    await notifier.send_message("🚀 *TradeDeck v2 Production* components initialized and background tasks started.")
    
    app.state.executor = executor
    app.state.feed = feed
    app.state.broker = broker
    
    logger.info("Bot components initialized and background tasks started")
    
    yield
    
    # Cleanup
    await tg_worker.stop()
    await mongo.close()
    await feed.stop()
    await executor.stop()
    await reconciler.stop()
    logger.info("Bot components shut down")

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

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
from fastapi.staticfiles import StaticFiles

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
