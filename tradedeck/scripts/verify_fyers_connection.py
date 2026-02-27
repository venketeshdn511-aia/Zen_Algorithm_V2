import asyncio
import os
import logging
from dotenv import load_dotenv
from app.services.broker_service import BrokerService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_connection():
    load_dotenv()
    
    # Print partial App ID to verify context
    app_id = os.getenv("FYERS_APP_ID")
    logger.info(f"Verifying connection for App ID: {app_id[:5]}...")

    broker = BrokerService()
    try:
        funds = await broker.get_funds()
        logger.info(f"SUCCESS! Connection verified. Funds: {funds}")
    except Exception as e:
        logger.error(f"Verification failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify_connection())
