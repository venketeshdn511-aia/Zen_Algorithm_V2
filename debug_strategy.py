import asyncio
from tradedeck.app.core.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        print("--- Last 5 Control Log Entries ---")
        r = await db.execute(text("SELECT strategy_name, action, actor, acked_at, created_at FROM strategy_control_log ORDER BY created_at DESC LIMIT 5"))
        for row in r.fetchall():
            print(row)
            
        print("\n--- Strategy States ---")
        r = await db.execute(text("SELECT strategy_name, status, control_intent, intent_set_at, intent_acked_at FROM strategy_states"))
        for row in r.fetchall():
            print(row)

if __name__ == "__main__":
    asyncio.run(check())
