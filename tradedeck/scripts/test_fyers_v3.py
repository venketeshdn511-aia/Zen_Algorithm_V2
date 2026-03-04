import asyncio
from fyers_apiv3 import fyersModel
from app.core.config import settings

async def test():
    app_id = settings.FYERS_APP_ID
    access_token = settings.FYERS_ACCESS_TOKEN
    
    # Check if is_async is accepted
    try:
        fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=True, log_path="/tmp")
        print("FyersModel initialized with is_async=True")
        
        data = {
            "symbol": "NSE:NIFTY50-INDEX",
            "resolution": "1",
            "date_format": "1",
            "range_from": "2025-02-28",
            "range_to": "2025-02-28",
            "cont_flag": "1"
        }
        res = await fyers.history(data=data)
        print(f"Full Response: {res}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
