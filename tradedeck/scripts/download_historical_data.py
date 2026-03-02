import asyncio
import os
import pandas as pd
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
from app.core.config import settings

async def download_data():
    app_id = settings.FYERS_APP_ID
    access_token = settings.FYERS_ACCESS_TOKEN
    
    if not app_id or not access_token:
        print("Missing FYERS_APP_ID or FYERS_ACCESS_TOKEN in settings.")
        return

    # Initialize FyersModel
    fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=True, log_path="/tmp")
    
    symbol = "NSE:NIFTY50-INDEX"
    resolutions = ["1", "5", "15"]
    days_to_fetch = 365
    chunk_size = 90  # Staying safe below 100-day limit
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_to_fetch)
    
    # Target directory
    download_dir = r"C:\Users\Vinay\Downloads"
    os.makedirs(download_dir, exist_ok=True)
    
    for res in resolutions:
        print(f"Fetching {res}-minute data for {symbol}...")
        all_candles = []
        current_to = end_date
        
        while current_to > start_date:
            current_from = max(start_date, current_to - timedelta(days=chunk_size))
            
            from_str = current_from.strftime("%Y-%m-%d")
            to_str = current_to.strftime("%Y-%m-%d")
            
            print(f"  Requesting range: {from_str} to {to_str}")
            
            data = {
                "symbol": symbol,
                "resolution": res,
                "date_format": "1",
                "range_from": from_str,
                "range_to": to_str,
                "cont_flag": "1"
            }
            
            try:
                response = await fyers.history(data=data)
                if response.get("s") == "ok":
                    candles = response.get("candles", [])
                    if candles:
                        all_candles.extend(candles)
                    else:
                        print(f"    No candles returned for this chunk.")
                else:
                    print(f"    Error in response: {response}")
            except Exception as e:
                print(f"    Exception during fetch: {e}")
            
            current_to = current_from - timedelta(days=1)
            
        if all_candles:
            # Fyers candle format: [timestamp, open, high, low, close, volume]
            df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
            df['datetime'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('datetime')
            
            # Remove duplicates if any (at chunk boundaries)
            df = df.drop_duplicates(subset=['timestamp'])
            
            filename = f"nifty50_{res}m_365d.csv"
            filepath = os.path.join(download_dir, filename)
            df.to_csv(filepath, index=False)
            print(f"SUCCESS: Saved {len(df)} rows to {filepath}")
        else:
            print(f"FAILED: No data found for {res}-minute resolution.")

if __name__ == "__main__":
    asyncio.run(download_data())
