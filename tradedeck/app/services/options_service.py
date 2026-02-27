"""
app/services/options_service.py

Dynamic Option Chain and ATM Strike resolution for Fyers NSE Derivative API.
"""
import logging
import asyncio
import pandas as pd
from datetime import datetime, date
import httpx

logger = logging.getLogger(__name__)

class OptionsService:
    def __init__(self):
        self.master_df = None
        self.last_sync = None
        
    async def sync_symbol_master(self):
        """Downloads and caches the Fyers NSE_FO Symbol Master."""
        try:
            url = "https://public.fyers.in/sym_details/NSE_FO.csv"
            logger.info(f"OptionsService: Downloading latest symbol master from {url}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                if response.status_code == 200:
                    import io
                    csv_data = io.StringIO(response.text)
                    df = pd.read_csv(csv_data, names=[
                        "FyToken", "SymbolDetails", "ExchangeInstrumentId", "MinimumLotSize",
                        "TickSize", "ISIN", "TradingSession", "LastUpdateDate", "ExpiryDate",
                        "SymbolTicker", "Exchange", "Segment", "ScripCode", "UnderlyingScripCode",
                        "StrikePrice", "OptionType", "UnderlyingFyToken", "Reserved1", "Reserved2", "Reserved3", "Reserved4"
                    ], usecols=list(range(17)))
                    
                    # Keep only NIFTY Options
                    # Fyers Option types are numeric or string based on segment. We filter by symbol starting with NSE:NIFTY
                    nifty_opts = df[(df["UnderlyingScripCode"] == "NIFTY") & (df["SymbolTicker"].str.contains(r"CE$|PE$"))].copy()
                    
                    # Store
                    self.master_df = nifty_opts
                    self.last_sync = date.today()
                    logger.info(f"OptionsService: Successfully synced {len(self.master_df)} valid Nifty options")
                else:
                    logger.error(f"OptionsService: Failed to download symbol master, status {response.status_code}")
        except Exception as e:
            logger.error(f"OptionsService: Error syncing symbol master: {e}")

    def get_atm_strike(self, spot_price: float, increment: int = 50) -> int:
        """
        Calculates the At-The-Money strike.
        Rounds to the nearest multiple of `increment` (Default 50 for Nifty).
        e.g. 22866 -> 22850
             22880 -> 22900
        """
        return int(round(spot_price / increment) * increment)

    async def get_atm_option_symbol(self, spot_price: float, option_type: str) -> str:
        """
        Identifies the exact Fyers symbol for the nearest ATM weekly expiry.
        :param spot_price: Live price of the underlying index.
        :param option_type: "CE" or "PE"
        :return: Exact trading symbol string (e.g., 'NSE:NIFTY2630222850CE')
        """
        if self.master_df is None or self.last_sync != date.today():
            await self.sync_symbol_master()
            
        if self.master_df is None or self.master_df.empty:
            logger.error("OptionsService: Cannot derive symbol, master list is empty!")
            return None
            
        atm_strike = self.get_atm_strike(spot_price)
        
        # Filter by requested Type (CE/PE) using the end of the symbol ticker
        df_filtered = self.master_df[self.master_df["SymbolTicker"].str.endswith(option_type)]
        
        if df_filtered.empty:
            logger.error(f"OptionsService: No {option_type} options found in master list!")
            return None
            
        # Get all future expiries
        # ExpiryDate comes from Fyers as Unix epoch in seconds
        current_time = datetime.now().timestamp()
        valid_expiries = df_filtered[df_filtered["ExpiryDate"] > current_time]["ExpiryDate"].unique()
        
        if len(valid_expiries) == 0:
            logger.error("OptionsService: No upcoming expiries found!")
            return None
            
        # Sort and pick the nearest expiry date
        nearest_expiry = sorted(valid_expiries)[0]
        
        # Filter for the target Expiry and Strike
        target_options = df_filtered[(df_filtered["ExpiryDate"] == nearest_expiry) & (df_filtered["StrikePrice"] == atm_strike)]
        
        if target_options.empty:
            logger.error(f"OptionsService: No exact match found for Expiry {nearest_expiry}, Strike {atm_strike} {option_type}")
            # Fallback: Find closest strike available
            nearest_options = df_filtered[df_filtered["ExpiryDate"] == nearest_expiry].copy()
            nearest_options['StrikeDiff'] = abs(nearest_options['StrikePrice'] - atm_strike)
            if not nearest_options.empty:
                closest = nearest_options.sort_values('StrikeDiff').iloc[0]
                logger.warning(f"OptionsService: Falling back to closest strike {closest['StrikePrice']} instead of target {atm_strike}")
                return closest["SymbolTicker"]
            return None
            
        return target_options.iloc[0]["SymbolTicker"]

# Global Instance
options_service = OptionsService()
