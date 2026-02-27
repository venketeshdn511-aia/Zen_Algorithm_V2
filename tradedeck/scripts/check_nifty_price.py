import asyncio
import os
import logging
from dotenv import load_dotenv
from app.services.broker_service import BrokerService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_nifty():
    load_dotenv()
    broker = BrokerService()
    symbol = "NSE:NIFTY50-INDEX"
    try:
        quote = await broker.get_quote(symbol)
        print(f"\n--- Fyers Price Check ---")
        print(f"Symbol: {quote['symbol']}")
        print(f"LTP:    {quote['ltp']}")
        print(f"-------------------------\n")
    except Exception as e:
        logger.error(f"Failed to fetch Nifty price: {e}")

if __name__ == "__main__":
    asyncio.run(check_nifty())
