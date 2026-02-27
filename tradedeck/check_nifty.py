import asyncio
import os
import sys

# Add tradedeck to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.broker_service import BrokerService
from app.core.config import settings

async def check_nifty():
    print(f"Using App ID: {settings.FYERS_APP_ID[:6]}... Token: {settings.FYERS_ACCESS_TOKEN[:6]}...")
    broker = BrokerService()
    symbol = "NSE:NIFTY50-INDEX"
    print(f"Fetching quote for {symbol}...")
    try:
        quote = await broker.get_quote(symbol)
        print(f"\n✅ Result: {quote}")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_nifty())
