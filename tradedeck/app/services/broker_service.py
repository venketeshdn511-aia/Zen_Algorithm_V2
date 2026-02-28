import logging
import httpx
import os
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

class BrokerService:
    def __init__(self):
        self.app_id = os.getenv("FYERS_APP_ID")
        self.secret_id = os.getenv("FYERS_SECRET_ID")
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
        self.refresh_token = os.getenv("FYERS_REFRESH_TOKEN")
        self.pin = os.getenv("FYERS_PIN")
        
        self.base_url = "https://api.fyers.in/api/v2"
        self._update_headers()

    def _update_headers(self):
        self.headers = {
            "Authorization": f"{self.app_id}:{self.access_token}",
            "Content-Type": "application/json"
        }

    async def _refresh_access_token(self) -> bool:
        """Automated token refresh using refresh_token (Fyers v3)."""
        if not all([self.app_id, self.secret_id, self.refresh_token, self.pin]):
            logger.error("Cannot refresh token: Missing credentials in .env")
            return False

        logger.info("Fyers: Attempting automated access token refresh...")
        try:
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
                res = await client.post(url, json=payload, timeout=10.0)
                data = res.json()
                
                if data.get("s") == "ok":
                    new_token = data.get("access_token")
                    self.access_token = new_token
                    self._update_headers()
                    
                    # Persist to .env locally
                    self._update_env_file(new_token)
                    logger.info("Fyers: Access token refreshed and persisted successfully.")
                    return True
                else:
                    logger.error(f"Fyers: Token refresh failed: {data}")
                    return False
        except Exception as e:
            logger.error(f"Fyers: Critical error during token refresh: {e}")
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

    async def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Resilient request wrapper with auto-retry on 401."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient() as client:
            # First attempt
            try:
                res = await client.request(method, url, headers=self.headers, **kwargs)
                data = res.json()
                
                # Check for token expiry
                # Fyers often returns 200 with error code in body, or 401
                is_expired = (res.status_code == 401) or \
                             (data.get("s") == "error" and data.get("code") in (-300, -400, -502))
                
                if is_expired:
                    logger.warn("Fyers: Access token detected as expired. Triggering refresh...")
                    if await self._refresh_access_token():
                        # Retry once with new headers
                        res = await client.request(method, url, headers=self.headers, **kwargs)
                        data = res.json()
                
                if res.status_code != 200 or data.get("s") != "ok":
                    raise BrokerError(data.get("code", "ERROR"), data.get("message", "API Request Failed"))
                
                return data
            except BrokerError:
                raise
            except Exception as e:
                logger.error(f"Fyers Request Error ({method} {path}): {e}")
                raise

    async def get_funds(self) -> Dict[str, Any]:
        """Fetch margin/funds from Fyers."""
        data = await self._request("GET", "/funds", timeout=5.0)
        fund_limit = data.get("fund_limit", [])
        equity_data = next((item for item in fund_limit if item.get("title") == "Equity"), {})
        return {
            "equity": {
                "available_margin": equity_data.get("equityAmount", 0),
                "used_margin": equity_data.get("utilizedAmount", 0)
            }
        }

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch LTP/Quote for a symbol."""
        data = await self._request("GET", f"/quotes?symbols={symbol}", timeout=3.0)
        quote_data = data.get("d", [{}])[0].get("v", {})
        return {
            "ltp": quote_data.get("lp", 0),
            "symbol": symbol
        }

    async def get_positions(self) -> list:
        """Fetch active positions."""
        try:
            data = await self._request("GET", "/positions", timeout=5.0)
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
        """Fetch orders for the day."""
        try:
            data = await self._request("GET", "/orders", timeout=5.0)
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
        """Place an order with Fyers."""
        return await self._request("POST", "/orders", json=order_data, timeout=5.0)
