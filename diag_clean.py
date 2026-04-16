
import asyncio
import json
import os
import sys

# Add tradedeck to python path
sys.path.append(os.path.join(os.getcwd(), "tradedeck"))

from app.services.broker_service import BrokerService
from app.services.mongodb_service import MongoDBService
from app.core.database import async_session
from sqlalchemy import text

async def diag():
    mongo = MongoDBService("mongodb+srv://venketeshdn511_db_user:VQ4NneK2loxp2uWz@zenalgo.2ihw5hx.mongodb.net/zen_algorithm?appName=ZenAlgo")
    await mongo.connect()
    broker = BrokerService(mongo)
    await broker.initialize()
    
    print("\n--- Fyers Raw Funds ---")
    try:
        if broker.client:
            funds = await broker.client.funds()
            print(json.dumps(funds, indent=2))
        else:
            print("Broker client not initialized")
    except Exception as e:
        print(f"Funds error: {e}")

    print("\n--- Feed Heartbeat Table ---")
    try:
        async with async_session() as db:
            r = await db.execute(text("SELECT * FROM feed_heartbeat"))
            print(r.fetchall())
    except Exception as e:
        print(f"DB error: {e}")

if __name__ == "__main__":
    asyncio.run(diag())
