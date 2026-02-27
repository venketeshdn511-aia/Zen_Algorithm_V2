"""
app/services/mongodb_service.py

Asynchronous MongoDB service for persistent storage of trades and system events.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

class MongoDBService:
    """
    Handles interactions with MongoDB for persistent audit logs and trade history.
    """
    def __init__(self, mongo_uri: str, db_name: str = "tradedeck"):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.enabled = bool(mongo_uri)
        
        if not self.enabled:
            logger.warning("MONGO_URI missing. MongoDB storage disabled.")

    async def connect(self):
        """Establish connection to MongoDB cluster."""
        if not self.enabled:
            return
            
        try:
            self.client = AsyncIOMotorClient(self.mongo_uri)
            self.db = self.client[self.db_name]
            # Verify connection
            await self.client.admin.command('ping')
            logger.info(f"Successfully connected to MongoDB database: {self.db_name}")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            self.enabled = False

    async def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed.")

    async def log_trade(self, trade_data: Dict[str, Any]):
        """Persist a completed trade record."""
        if not self.enabled:
            return
            
        try:
            trade_data["timestamp"] = trade_data.get("timestamp", datetime.now(timezone.utc))
            await self.db.trades.insert_one(trade_data)
        except Exception as e:
            logger.error(f"Error logging trade to MongoDB: {e}")

    async def log_event(self, event_type: str, details: Dict[str, Any]):
        """Log a system event or audit record."""
        if not self.enabled:
            return
            
        try:
            event = {
                "type": event_type,
                "timestamp": datetime.now(timezone.utc),
                "details": details
            }
            await self.db.events.insert_one(event)
        except Exception as e:
            logger.error(f"Error logging event to MongoDB: {e}")

    async def get_recent_trades(self, strategy_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve recent trades for reporting."""
        if not self.enabled:
            return []
            
        query = {"strategy_name": strategy_name} if strategy_name else {}
        try:
            cursor = self.db.trades.find(query).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Error fetching trades from MongoDB: {e}")
            return []
