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
                    nifty_opts = df[(df["SymbolTicker"].str.startswith("NSE:NIFTY")) & (df["SymbolTicker"].str.contains(r"CE$|PE$"))].copy()
                    
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
        expiry_dt = datetime.fromtimestamp(nearest_expiry)
        
        # Filter for the target Expiry
        expiry_df = df_filtered[df_filtered["ExpiryDate"] == nearest_expiry].copy()
        
        # Robust strike matching: Extract strike price directly from the SymbolTicker 
        # SymbolTicker format: NSE:NIFTY<YYMDD><STRIKE><CE/PE>
        # Example: NSE:NIFTY2631020400CE -> 20400
        # We can extract it by removing the prefix 'NSE:NIFTY', removing 'CE'/'PE', 
        # and taking the last 5 characters (since Nifty strikes are 5 digits).
        # To be safe, we just regex extract digits before CE/PE
        import re
        def extract_strike(ticker):
            match = re.search(r'(\d+)(CE|PE)$', ticker)
            if match:
                # The matched digits might be e.g. '2631020400'. The strike is usually the last 5 digits.
                # However, Fyers format is NIFTY YY M DD STRIKE CE. e.g. 26 3 10 20400 CE. 
                # If strike is 20400, it's 5 chars. If it is 9000, it's 4 chars.
                # Actually, pulling it from the right side before CE/PE:
                num_str = match.group(1)
                return int(num_str[-5:]) if len(num_str) >= 5 else int(num_str)
            return 0
            
        expiry_df["ParsedStrike"] = expiry_df["SymbolTicker"].apply(extract_strike)
        
        target_options = expiry_df[expiry_df["ParsedStrike"] == atm_strike]
        
        if target_options.empty:
            found_strikes = sorted(expiry_df["ParsedStrike"].unique())
            logger.error(
                f"OptionsService: No exact match for Expiry {expiry_dt} ({nearest_expiry}), Strike {atm_strike} {option_type}. "
                f"Found {len(found_strikes)} strikes in this expiry. Range: {min(found_strikes) if found_strikes else 'N/A'} - {max(found_strikes) if found_strikes else 'N/A'}"
            )
            # Fallback: Find closest strike available
            expiry_df['StrikeDiff'] = abs(expiry_df['ParsedStrike'] - atm_strike)
            if not expiry_df.empty:
                closest = expiry_df.sort_values('StrikeDiff').iloc[0]
                logger.warning(f"OptionsService: Falling back to closest strike {closest['ParsedStrike']} instead of target {atm_strike}")
                return closest["SymbolTicker"]
            return None
            
        return target_options.iloc[0]["SymbolTicker"]

# Global Instance
options_service = OptionsService()
