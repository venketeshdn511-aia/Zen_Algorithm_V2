import logging
import os
import asyncio
import hashlib
import httpx
import base64
import pyotp
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs
from fyers_apiv3 import fyersModel

from app.core.config import settings

logger = logging.getLogger(__name__)

class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

class BrokerService:
    def __init__(self, mongo_service: Optional["MongoDBService"] = None):
        self.app_id = settings.FYERS_APP_ID
        self.secret_id = settings.FYERS_SECRET_ID
        self.access_token = settings.FYERS_ACCESS_TOKEN
        self.refresh_token = settings.FYERS_REFRESH_TOKEN
        self.pin = settings.FYERS_PIN
        self.redirect_uri = settings.FYERS_REDIRECT_URI
        
        self.mongo = mongo_service
        self._on_refresh_callbacks = []
        self._refresh_lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._refresh_event.set() # Initially idle
        
        # We don't initialize client here because we need to sync from DB first
        self.client = None

    def _initialize_client(self):
        """Initialize or re-initialize the Fyers V3 client."""
        self.client = fyersModel.FyersModel(
            client_id=self.app_id, 
            token=self.access_token, 
            is_async=True, 
            log_path="/tmp"
        )

    async def initialize(self):
        """Sync tokens from MongoDB and initialize client."""
        if self.mongo:
            db_access = await self.mongo.get_config("fyers_access_token")
            db_refresh = await self.mongo.get_config("fyers_refresh_token")
            
            if db_access:
                logger.info("[BROKER] 📥 Synced access_token from MongoDB.")
                self.access_token = db_access
            if db_refresh:
                logger.info("[BROKER] 📥 Synced refresh_token from MongoDB.")
                self.refresh_token = db_refresh
        
        self._initialize_client()

    def register_on_refresh(self, callback):
        """Register a callback to be called when the access token is refreshed."""
        self._on_refresh_callbacks.append(callback)

    async def _generate_new_access_token(self) -> bool:
        """Automated TOTP-based login to generate a fresh access_token (April 2026 compliant)."""
        if self._refresh_lock.locked():
            logger.info("[BROKER] ⏳ Authentication already in progress. Waiting...")
            await self._refresh_event.wait()
            return True

        async with self._refresh_lock:
            self._refresh_event.clear()
            
            fy_id = settings.FYERS_USERNAME
            pin = settings.FYERS_PIN
            totp_secret = settings.FYERS_TOTP_SECRET
            
            missing = []
            if not fy_id: missing.append("FYERS_USERNAME")
            if not pin: missing.append("FYERS_PIN")
            if not totp_secret: missing.append("FYERS_TOTP_SECRET")
            if not self.app_id: missing.append("FYERS_APP_ID")
            if not self.secret_id: missing.append("FYERS_SECRET_ID")

            if missing:
                logger.error(f"[BROKER] ❌ Cannot authenticate: Missing {', '.join(missing)}")
                self._refresh_event.set()
                return False

            logger.info("[BROKER] 🔐 Starting automated TOTP login flow...")
            try:
                def b64(v: str) -> str:
                    return base64.b64encode(v.encode()).decode()

                # 1. login_otp
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r1 = await client.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", json={
                        "fy_id": b64(fy_id), "app_id": "2"
                    })
                    if r1.status_code != 200: raise Exception(f"Step 1 failed: {r1.text}")
                    req_key = r1.json().get("request_key")

                    # 2. verify_otp
                    totp_val = pyotp.TOTP(totp_secret).now()
                    r2 = await client.post("https://api-t2.fyers.in/vagator/v2/verify_otp", json={
                        "request_key": req_key, "otp": totp_val
                    })
                    if r2.status_code != 200: 
                        raise Exception(f"Step 2 TOTP verify failed: {r2.text}")
                    req_key2 = r2.json().get("request_key")

                    # 3. verify_pin
                    r3 = await client.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2", json={
                        "request_key": req_key2, "identity_type": "pin", "identifier": b64(pin)
                    })
                    if r3.status_code != 200: raise Exception(f"Step 3 PIN verify failed: {r3.text}")
                    session_token = r3.json().get("data", {}).get("access_token")

                    # 4. token redirect
                    if "-" in self.app_id:
                        app_base, app_type = self.app_id.rsplit("-", 1)
                    else:
                        app_base, app_type = self.app_id, "100"

                    client.headers.update({"Authorization": f"Bearer {session_token}"})
                    r4 = await client.post("https://api-t1.fyers.in/api/v3/token", json={
                        "fyers_id": fy_id, "app_id": app_base, "redirect_uri": self.redirect_uri,
                        "appType": app_type, "response_type": "code", "create_cookie": True
                    }, follow_redirects=False)
                    
                    auth_code = None
                    if r4.status_code in (308, 200):
                        url = r4.json().get("Url", "")
                        auth_code = parse_qs(urlparse(url).query).get("auth_code", [""])[0]
                    
                    if not auth_code: raise Exception(f"Step 4 (Auth Code) failed: {r4.text}")

                    # 5. finalize
                    app_hash = hashlib.sha256(f"{self.app_id}:{self.secret_id}".encode()).hexdigest()
                    r5 = await client.post("https://api-t1.fyers.in/api/v3/validate-authcode", json={
                        "grant_type": "authorization_code", "appIdHash": app_hash, "code": auth_code
                    })
                    if r5.status_code != 200 or r5.json().get("s") == "error": 
                        raise Exception(f"Step 5 failed: {r5.text}")

                    new_token = r5.json().get("access_token")
                    self.access_token = new_token
                    self._initialize_client()

                    if self.mongo:
                        await self.mongo.set_config("fyers_access_token", new_token)
                    
                    self._update_env_file(new_token)
                    logger.info("[BROKER] ✅ Fresh Access Token generated via TOTP and persisted.")
                    
                    for cb in self._on_refresh_callbacks:
                        try:
                            if asyncio.iscoroutinefunction(cb): asyncio.create_task(cb(new_token))
                            else: cb(new_token)
                        except Exception as e:
                            logger.error(f"[BROKER] ⚠️ Callback error: {e}")
                    
                    self._refresh_event.set()
                    return True

            except Exception as e:
                logger.error(f"[BROKER] 🛑 TOTP Login failed: {repr(e)}")
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
                # Before refreshing, try to sync from DB in case another worker already did it
                if self.mongo:
                    db_access = await self.mongo.get_config("fyers_access_token")
                    if db_access and db_access != self.access_token:
                        logger.info("[BROKER] 🔄 Detected fresh token in MongoDB. Syncing...")
                        self.access_token = db_access
                        self._initialize_client()
                        response = await func(*args, **kwargs)
                        if response.get("s") == "ok":
                            return response

                logger.warning(f"[BROKER] 🔑 Token expired (code {response.get('code')}). Triggering TOTP login...")
                if await self._generate_new_access_token():
                    # Retry once
                    response = await func(*args, **kwargs)
                else:
                    raise BrokerError("AUTH_FAILED", "TOTP Login failed")
            
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
