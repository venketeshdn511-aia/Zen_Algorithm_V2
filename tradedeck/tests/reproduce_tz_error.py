import pandas as pd
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reproduce_error():
    # Mix of tz-aware (UTC) and tz-naive datetimes
    candles = [
        {"time": datetime.fromtimestamp(1741498500), "close": 23500}, # Naive (like from historical data)
        {"time": datetime.now(timezone.utc), "close": 23510}        # Aware (like from live tick)
    ]
    
    logger.info("Attempting to create DataFrame from mixed datetimes...")
    df = pd.DataFrame(candles)
    
    try:
        # This is where it fails in FailedAuctionB1._calculate_indicators
        df["time"] = pd.to_datetime(df["time"])
        logger.info("pd.to_datetime(df['time']) succeeded unexpectedly!")
    except Exception as e:
        logger.error(f"Caught expected error: {e}")

    try:
        # Another common failure point
        df["date"] = df["time"].dt.date
        logger.info("df['time'].dt.date succeeded unexpectedly!")
    except Exception as e:
        logger.error(f"Caught expected error in .dt.date: {e}")

if __name__ == "__main__":
    reproduce_error()
