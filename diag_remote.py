
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

# Add tradedeck to python path
sys.path.append(os.path.join(os.getcwd(), "tradedeck"))

from app.services.broker_service import BrokerService
from app.services.mongodb_service import MongoDBService
from app.core.database import async_session
from sqlalchemy import text
from app.core.config import settings

async def diag():
    print("=== TradeDeck AWS Diagnostic (Ubuntu) ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"OS: {sys.platform}")
    print(f"Python: {sys.version}")
    
    # 1. Environment Check
    print("\n--- Environment Check ---")
    print(f"DB_HOST: {settings.DB_HOST}")
    print(f"REDIS_HOST: {os.getenv('REDIS_HOST', 'Not Set')}")
    print(f"FYERS_APP_ID: {settings.FYERS_APP_ID}")
    
    # 2. MongoDB Connectivity
    print("\n--- MongoDB Connectivity ---")
    try:
        mongo = MongoDBService(settings.MONGO_URI)
        await mongo.connect()
        print(" MongoDB Connected")
        
        # Check for tokens in MongoDB
        access_token = await mongo.get_config("fyers_access_token")
        print(f"Token in MongoDB: {'YES' if access_token else 'NO'}")
    except Exception as e:
        print(f"ERROR MongoDB: {e}")
        return

    # 3. Fyers Broker Check
    print("\n--- Fyers Broker Check ---")
    try:
        broker = BrokerService(mongo)
        await broker.initialize()
        
        print("Syncing funds...")
        funds = await broker.client.funds()
        if funds.get("s") == "ok":
            print("OK Fyers API Connected & Authenticated")
            # Look for available balance
            balance = 0
            for item in funds.get("fund_limit", []):
                if item.get("title") in ["Available Balance", "Total Balance"]:
                    balance = item.get("equityAmount", 0)
                    break
            print(f"Available Balance: {balance}")
        else:
            print(f"ERROR Fyers Auth: {funds.get('message', 'Unknown Error')}")
    except Exception as e:
        print(f"ERROR Broker Init: {e}")

    # 4. Database Heartbeat Check
    print("\n--- Database Heartbeat Check ---")
    try:
        async with async_session() as db:
            r = await db.execute(text("SELECT * FROM feed_heartbeat"))
            rows = r.fetchall()
            if rows:
                print(f"OK Database Accessible. Feed Heartbeats found: {len(rows)}")
                for row in rows:
                    print(f"  - {row}")
            else:
                print("WARNING Database Accessible but feed_heartbeat table is empty.")
    except Exception as e:
        print(f"ERROR Database: {e}")

    await mongo.close()
    print("\n=== Diagnostic Complete ===")

if __name__ == "__main__":
    asyncio.run(diag())
