
import asyncio
import logging
from datetime import datetime, timezone
from app.strategies.ib_production_bridge import IBProductionBridge

# Configure logging to see errors
logging.basicConfig(level=logging.INFO)

async def test_bridge_loading():
    print("--- Testing IB Bridge Loading ---")
    try:
        bridge = IBProductionBridge()
        print("✅ Bridge initialized successfully.")
        
        # Mock objects
        class MockDB: pass
        class MockBroker: pass
        class MockRisk: pass
        
        mock_tick = {
            "symbol": "NSE:NIFTY50-INDEX",
            "ltp": 22505.50,
            "vol": 100,
            "ts": datetime.now(timezone.utc).timestamp()
        }
        
        # Test call
        res = await bridge(mock_tick, [], MockDB(), MockBroker(), MockRisk())
        print(f"✅ Bridge call successful. Signal: {res.get('signal')}")
        print(f"Thought Process: {res.get('thought_process')}")
        
    except Exception as e:
        print(f"❌ Bridge test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_bridge_loading())
