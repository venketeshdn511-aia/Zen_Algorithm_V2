import asyncio
import logging
from app.core.database import engine
from app.models.db import Base

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

async def create_tables():
    logger.info("🚀 Starting database migration...")
    try:
        async with engine.begin() as conn:
            # This creates all tables defined in models/db.py if they don't exist
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created successfully.")
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(create_tables())
