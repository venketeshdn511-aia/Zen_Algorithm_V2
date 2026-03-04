import asyncio
from app.services.broker_service import BrokerService

async def test():
    broker = BrokerService()
    symbol = "NSE:NIFTY50-INDEX"
    res = "1"
    # Fetch just today
    from_date = "2025-02-28"
    to_date = "2025-02-28"
    
    print(f"Testing {symbol} res={res} from {from_date} to {to_date}")
    try:
        data = await broker.get_history(symbol, res, from_date, to_date)
        print(f"Full Response: {data}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
