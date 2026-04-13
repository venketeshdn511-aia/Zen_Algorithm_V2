
import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timezone
from sqlalchemy import text
from app.core.database import async_session
from app.services.broker_service import BrokerService
from app.services.mongodb_service import MongoDBService
import redis.asyncio as redis

async def diag():
    print("--- 🔍 TradeDeck Diagnostic ---")
    
    # 1. Check DB Heartbeat
    print("\n1. Feed Heartbeat Table:")
    async with async_session() as db:
        res = await db.execute(text("SELECT * FROM feed_heartbeat"))
        rows = res.fetchall()
        for r in rows:
            print(f"  {r}")

    # 2. Check Redis
    print("\n2. Redis Connectivity:")
    try:
        r = redis.from_url("redis://redis:6379/0")
        ping = await r.ping()
        print(f"  Redis Ping: {ping}")
        keys = await r.keys("tradedeck:*")
        print(f"  Current Keys: {keys}")
        for k in keys:
            val = await r.get(k)
            print(f"    {k.decode()}: {val.decode() if val else 'None'}")
    except Exception as e:
        print(f"  Redis Error: {e}")

    # 3. Check Funds Response
    print("\n3. Fyers Raw Funds:")
    try:
        mongo = MongoDBService("mongodb://mongodb:27017")
        await mongo.connect()
        broker = BrokerService(mongo)
        await broker.initialize()
        funds_raw = await broker.client.funds()
        print(json.dumps(funds_raw, indent=2))
    except Exception as e:
        print(f"  Fyers Error: {e}")

if __name__ == "__main__":
    asyncio.run(diag())
