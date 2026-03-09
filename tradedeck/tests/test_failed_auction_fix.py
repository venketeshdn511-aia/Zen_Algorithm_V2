import asyncio
import pandas as pd
from datetime import datetime, timedelta, timezone
from app.strategies.failed_auction import FailedAuctionB1
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_fix():
    strategy = FailedAuctionB1()
    
    # 1. Simulate historical data (Naive datetimes from the past)
    # Note: In the real app, these are fetched and appended.
    # We'll just manually populate for testing the indicator calculation logic.
    base_time = datetime(2026, 3, 9, 9, 0)
    for i in range(100):
        strategy.candles_15m.append({
            "time":   base_time + timedelta(minutes=15 * i),
            "open":   23000 + i,
            "high":   23010 + i,
            "low":    22990 + i,
            "close":  23005 + i,
            "volume": 1000
        })
    
    # 2. Simulate a live tick (UTC aware)
    tick = {
        "symbol": "NSE:NIFTY50-INDEX",
        "ltp": 23100,
        "ts": datetime.now(timezone.utc).timestamp(),
        "vol": 10
    }
    
    logger.info("Running strategy with mixed datetimes...")
    # This should NOT crash now
    result = await strategy(tick, None, None, None, None)
    logger.info(f"Strategy call 1 result: {result['signal']}")
    
    # 3. Verify caching
    first_df_id = id(strategy.indicators_df)
    logger.info(f"First indicators_df ID: {first_df_id}")
    
    # Another tick in the same candle
    tick_same = tick.copy()
    tick_same["ltp"] = 23105
    await strategy(tick_same, None, None, None, None)
    
    second_df_id = id(strategy.indicators_df)
    logger.info(f"Second indicators_df ID: {second_df_id}")
    
    if first_df_id == second_df_id:
        logger.info("SUCCESS: Indicators cache hit (IDs match)")
    else:
        logger.error("FAILURE: Indicators recalculated on same candle")
        
    # 4. Trigger a new candle
    tick_new = tick.copy()
    tick_new["ts"] = (datetime.now(timezone.utc) + timedelta(minutes=16)).timestamp()
    await strategy(tick_new, None, None, None, None)
    
    third_df_id = id(strategy.indicators_df)
    logger.info(f"Third indicators_df ID: {third_df_id}")
    
    if third_df_id != second_df_id:
        logger.info("SUCCESS: Indicators recalculated on new candle")
    else:
        logger.error("FAILURE: Indicators NOT recalculated on new candle")

if __name__ == "__main__":
    asyncio.run(test_fix())
