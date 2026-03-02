import logging
import os
import asyncio
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
        """Automated token refresh using Fyers V3 SessionModel."""
        missing = []
        if not self.app_id: missing.append("FYERS_APP_ID")
        if not self.secret_id: missing.append("FYERS_SECRET_ID")
        if not self.refresh_token: missing.append("FYERS_REFRESH_TOKEN")
        if not self.pin: missing.append("FYERS_PIN")

        if missing:
            logger.error(f"Cannot refresh token: Missing credentials: {', '.join(missing)}")
            return False

        logger.info("Fyers: Attempting automated access token refresh via SessionModel...")
        try:
            session = fyersModel.SessionModel(
                client_id=self.app_id,
                secret_key=self.secret_id,
                redirect_uri=self.redirect_uri,
                response_type="code",
                grant_type="refresh_token"
            )
            
            # Use the official library refresh mechanism
            response = session.refresh_token(self.refresh_token, self.pin)
            
            if response.get("s") == "ok":
                new_token = response.get("access_token")
                self.access_token = new_token
                
                # Re-initialize the internal client with new token
                self._initialize_client()
                
                # Persist to .env locally
                self._update_env_file(new_token)
                logger.info("Fyers: Access token refreshed and persisted successfully.")
                
                # Trigger callbacks (e.g., FeedWorker restart)
                for cb in self._on_refresh_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb(new_token))
                        else:
                            cb(new_token)
                    except Exception as e:
                        logger.error(f"Error in token refresh callback: {e}")
                            
                return True
            else:
                logger.error(f"Fyers: Token refresh failed: {response}")
                return False
        except Exception as e:
            logger.error(f"Fyers: Critical error during token refresh: {repr(e)}")
            return False

    def _update_env_file(self, new_token: str):
        """Update .env file with new access token."""
        try:
            # Try to find .env in common locations
            possible_paths = [
                os.path.join(os.getcwd(), ".env"),
                os.path.join(os.path.dirname(os.getcwd()), ".env"),
                # Absolute path for this specific user setup as fallback
                r"c:\Users\Vinay\OneDrive\Desktop\Algo Trading\tradedeck-v2-production\tradedeck\.env"
            ]
            
            env_path = None
            for p in possible_paths:
                if os.path.exists(p):
                    env_path = p
                    break
            
            if not env_path:
                logger.warning("Fyers: .env not found, skipping persistence.")
                return

            logger.info(f"Fyers: Updating .env at {env_path}")
            with open(env_path, "r") as f:
                lines = f.readlines()

            with open(env_path, "w") as f:
                for line in lines:
                    if line.startswith("FYERS_ACCESS_TOKEN="):
                        f.write(f"FYERS_ACCESS_TOKEN={new_token}\n")
                    else:
                        f.write(line)
        except Exception as e:
            logger.error(f"Failed to update .env: {e}")

    async def _api_call(self, func, *args, **kwargs) -> Dict[str, Any]:
        """Wrapper for Fyers library calls with auto-refresh on 401."""
        try:
            response = await func(*args, **kwargs)
            
            # Token expiry detection (Fyers V3 uses -99 or -300 codes usually)
            if response.get("s") == "error" and response.get("code") in (-99, -300, -400):
                logger.warning(f"Fyers: Token expiry/error detected ({response.get('code')}). Refreshing...")
                if await self._refresh_access_token():
                    # Retry once
                    response = await func(*args, **kwargs)
                else:
                    raise BrokerError("AUTH_FAILED", "Token refresh failed")
            
            if response.get("s") != "ok":
                raise BrokerError(str(response.get("code", "ERROR")), response.get("message", "API Request Failed"))
            
            return response
        except BrokerError:
            raise
        except Exception as e:
            logger.error(f"Fyers API Exception: {repr(e)}")
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
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }
        return await self._api_call(self.client.history, data=payload)
