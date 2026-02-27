
import asyncio
from app.services.strategy_control import StrategyControlService
from app.core.database import async_session

async def start_all():
    ctrl = StrategyControlService()
    async with async_session() as db:
        strategies = ["FAILED_AUCTION_B1", "STAT_SNIPER_01"]
        for name in strategies:
            print(f"Sending START intent for {name}...")
            await ctrl.send_intent(db, name, "start", "VERIFICATION_SCRIPT")
        await db.commit()
    print("Intents sent. Wait a few seconds for executor to pick up.")

if __name__ == "__main__":
    asyncio.run(start_all())
