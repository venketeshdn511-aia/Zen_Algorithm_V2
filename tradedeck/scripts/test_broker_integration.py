import asyncio
import os
from dotenv import load_dotenv
from tradedeck.app.services.broker_service import BrokerService

async def test_auto_refresh():
    # 1. Load current .env
    load_dotenv()
    original_token = os.getenv("FYERS_ACCESS_TOKEN")
    print(f"Original Token starts with: {original_token[:15]}...")

    # 2. Corrupt the token in memory/environment to force a refresh
    os.environ["FYERS_ACCESS_TOKEN"] = "INVALID_TOKEN_FOR_TESTING"
    
    broker = BrokerService()
    # Manually override the instance's token to be sure
    broker.access_token = "INVALID_TOKEN_FOR_TESTING"
    broker._update_headers()
    
    print("Triggering get_funds() with invalid token...")
    try:
        funds = await broker.get_funds()
        print(f"Success! Funds: {funds}")
        
        # 3. Check if .env was updated
        load_dotenv(override=True)
        new_token = os.getenv("FYERS_ACCESS_TOKEN")
        print(f"New Token in .env starts with: {new_token[:15]}...")
        
        if new_token != original_token and new_token != "INVALID_TOKEN_FOR_TESTING":
            print("VERIFICATION PASSED: Token was refreshed and persisted.")
        else:
            print("VERIFICATION FAILED: Token was not updated in .env.")
            
    except Exception as e:
        print(f"VERIFICATION FAILED: Request failed after refresh attempt: {e}")

if __name__ == "__main__":
    asyncio.run(test_auto_refresh())
