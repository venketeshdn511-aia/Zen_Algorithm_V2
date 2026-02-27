import sys
import os
import asyncio
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.services.options_service import options_service

logging.basicConfig(level=logging.INFO)

async def run_test():
    spot_price = 22866.45
    print("\n--- Testing Fyers ATM Option Selector ---")
    print(f"Index Live Spot Price: {spot_price}")
    
    print("\n1. Calculating ATM Strike...")
    atm_strike = options_service.get_atm_strike(spot_price, increment=50)
    print(f"Calculated ATM Strike: {atm_strike} (Rounded to nearest 50)")

    print("\n2. Simulating 'Statistical Sniper' BUY Signal -> Translates to CE")
    ce_symbol = await options_service.get_atm_option_symbol(spot_price, "CE")
    print(f"Resulting Trade Target: {ce_symbol}")

    print("\n3. Simulating 'Failed Auction' SHORT Signal -> Translates to PE")
    pe_symbol = await options_service.get_atm_option_symbol(spot_price, "PE")
    print(f"Resulting Trade Target: {pe_symbol}")
    print("-----------------------------------------\n")

if __name__ == "__main__":
    asyncio.run(run_test())
