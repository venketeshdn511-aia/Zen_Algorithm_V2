import logging
import os
import asyncio
import hashlib
import httpx
from typing import Dict, Any, Optional
from fyers_apiv3 import fyersModel

from app.core.config import settings

logger = logging.getLogger(__name__)

class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

class BrokerService:
    def __init__(self):
        self.app_id = settings.FYERS_APP_ID
        self.secret_id = settings.FYERS_SECRET_ID
        self.access_token = settings.FYERS_ACCESS_TOKEN
        self.refresh_token = settings.FYERS_REFRESH_TOKEN
        self.pin = settings.FYERS_PIN
        self.redirect_uri = settings.FYERS_REDIRECT_URI
        
        self._on_refresh_callbacks = []
        self._refresh_lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._refresh_event.set() # Initially idle
        self._initialize_client()

    def _initialize_client(self):
        """Initialize or re-initialize the Fyers V3 client."""
        self.client = fyersModel.FyersModel(
            client_id=self.app_id, 
            token=self.access_token, 
            is_async=True, 
            log_path="/tmp"
        )

    def register_on_refresh(self, callback):
        """Register a callback to be called when the access token is refreshed."""
        self._on_refresh_callbacks.append(callback)

    async def _refresh_access_token(self) -> bool:
        """Automated token refresh using manual V3 validation (robust)."""
        if self._refresh_lock.locked():
            logger.info("[BROKER] ⏳ Refresh already in progress. Waiting...")
            await self._refresh_event.wait()
            return True

        async with self._refresh_lock:
            self._refresh_event.clear()
            missing = []
            if not self.app_id: missing.append("FYERS_APP_ID")
            if not self.secret_id: missing.append("FYERS_SECRET_ID")
            if not self.refresh_token: missing.append("FYERS_REFRESH_TOKEN")
            if not self.pin: missing.append("FYERS_PIN")

            if missing:
                logger.error(f"[BROKER] ❌ Cannot refresh: Missing {', '.join(missing)}")
                self._refresh_event.set()
                return False

            logger.info("[BROKER] 🔄 Starting automated token refresh...")
            try:
                # V3 manual refresh logic (most reliable)
                hash_input = f"{self.app_id}:{self.secret_id}"
                app_id_hash = hashlib.sha256(hash_input.encode()).hexdigest()

                payload = {
                    "grant_type": "refresh_token",
                    "appIdHash": app_id_hash,
                    "refresh_token": self.refresh_token,
                    "pin": self.pin
                }
                
                url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
                async with httpx.AsyncClient() as client:
                    res = await client.post(url, json=payload, timeout=15.0)
                    data = res.json()
                
                if data.get("s") == "ok":
                    new_token = data.get("access_token")
                    self.access_token = new_token
                    
                    self._initialize_client()
                    self._update_env_file(new_token)
                    logger.info("[BROKER] ✅ Access token refreshed successfully.")
                    
                    for cb in self._on_refresh_callbacks:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                asyncio.create_task(cb(new_token))
                            else:
                                cb(new_token)
                        except Exception as e:
                            logger.error(f"[BROKER] ⚠️ Callback error: {e}")
                                
                    self._refresh_event.set()
                    return True
                else:
                    logger.error(f"[BROKER] ❌ Refresh failed: {data}")
                    self._refresh_event.set()
                    return False
            except Exception as e:
                logger.error(f"[BROKER] 🛑 Critical refresh error: {repr(e)}")
                self._refresh_event.set()
                return False

    def _update_env_file(self, new_token: str):
        """Update .env file with new access token."""
        try:
            # Try to find .env in common locations
            possible_paths = [
                os.path.join(os.getcwd(), ".env"),
                os.path.join(os.path.dirname(os.getcwd()), ".env"),
                r"c:\Users\Vinay\OneDrive\Desktop\Algo Trading\tradedeck-v2-production\tradedeck\.env"
            ]
            
            env_path = None
            for p in possible_paths:
                if os.path.exists(p):
                    env_path = p
                    break
            
            if not env_path:
                # Noisy on Render, only log once if needed or keep it quiet
                logger.debug("[BROKER] ℹ️ .env not found, skipping persistence (expected on Render).")
                return

            logger.info(f"[BROKER] 💾 Updating .env at {env_path}")
            with open(env_path, "r") as f:
                lines = f.readlines()

            with open(env_path, "w") as f:
                for line in lines:
                    if line.startswith("FYERS_ACCESS_TOKEN="):
                        f.write(f"FYERS_ACCESS_TOKEN={new_token}\n")
                    else:
                        f.write(line)
        except Exception as e:
            logger.error(f"[BROKER] ⚠️ Failed to update .env: {e}")

    async def _api_call(self, func, *args, **kwargs) -> Dict[str, Any]:
        """Wrapper for Fyers library calls with auto-refresh on 401."""
        try:
            response = await func(*args, **kwargs)
            
            # Token expiry detection (Fyers V3 uses -99 or -300 codes usually)
            if response.get("s") == "error" and response.get("code") in (-99, -300, -400):
                logger.warning(f"[BROKER] 🔑 Token expired (code {response.get('code')}). Triggering refresh...")
                if await self._refresh_access_token():
                    # Retry once
                    response = await func(*args, **kwargs)
                else:
                    raise BrokerError("AUTH_FAILED", "Sync refresh failed")
            
            if response.get("s") != "ok":
                raise BrokerError(str(response.get("code", "ERROR")), response.get("message", "API Request Failed"))
            
            return response
        except BrokerError:
            raise
        except Exception as e:
            logger.error(f"[BROKER] 🛑 API Error: {repr(e)}")
            raise

    async def get_funds(self) -> Dict[str, Any]:
        """Fetch margin/funds from Fyers V3."""
        data = await self._api_call(self.client.funds)
        fund_limit = data.get("fund_limit", [])
        equity_data = next((item for item in fund_limit if item.get("title") == "Total Balance"), {})
        return {
            "equity": {
                "available_margin": equity_data.get("equityAmount", 0),
                "used_margin": equity_data.get("utilizedAmount", 0)
            }
        }

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch quotes/LTP for a symbol (V3)."""
        data = await self._api_call(self.client.quotes, data={"symbols": symbol})
        quote_data = data.get("d", [{}])[0].get("v", {})
        return {
            "ltp": quote_data.get("lp", 0),
            "symbol": symbol
        }

    async def get_positions(self) -> list:
        """Fetch active positions (V3)."""
        try:
            data = await self._api_call(self.client.positions)
            positions = data.get("netPositions", [])
            return [
                {
                    "symbol": p["symbol"],
                    "net_qty": p["netQty"],
                    "ltp": p["ltp"],
                    "pnl": p["unrealizedProfit"]
                } for p in positions
            ]
        except Exception:
            return []

    async def get_orders(self) -> list:
        """Fetch daily orderbook (V3)."""
        try:
            data = await self._api_call(self.client.orderbook)
            orders = data.get("orderBook", [])
            status_map = {1: "CANCELLED", 2: "FILLED", 4: "TRANSIT", 5: "REJECTED", 6: "PENDING"}
            return [
                {
                    "broker_order_id": o["id"],
                    "status": status_map.get(o["status"], "UNKNOWN"),
                    "filled_qty": o["filledQty"],
                    "avg_price": o["tradedPrice"]
                } for o in orders
            ]
        except Exception:
            return []

    async def submit_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Place an order via Fyers V3 library."""
        return await self._api_call(self.client.place_order, data=order_data)

    async def get_history(self, symbol: str, resolution: str, range_from: str, range_to: str) -> Dict[str, Any]:
        """Fetch historical candles (V3)."""
        logger.info(f"[BROKER] 📊 Fetching history for {symbol} ({resolution})...")
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }
        return await self._api_call(self.client.history, data=payload)
