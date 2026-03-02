import asyncio
from app.services.broker_service import BrokerService

async def refresh():
    broker = BrokerService()
    print("Attempting to refresh token via BrokerService...")
    success = await broker._refresh_access_token()
    if success:
        print("Token refreshed successfully! Check your .env file.")
    else:
        print("Failed to refresh token. Check your FYERS_REFRESH_TOKEN and FYERS_PIN in .env.")

if __name__ == "__main__":
    asyncio.run(refresh())
