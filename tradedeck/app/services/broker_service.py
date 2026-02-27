"""
BrokerService — Fyers API integration for order placement and market data.
"""
import logging
import httpx
import os
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
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN")
        self.base_url = "https://api.fyers.in/api/v2"
        self.headers = {
            "Authorization": f"{self.app_id}:{self.access_token}",
            "Content-Type": "application/json"
        }

    async def get_funds(self) -> Dict[str, Any]:
        """Fetch margin/funds from Fyers."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/funds",
                    headers=self.headers,
                    timeout=5.0
                )
                data = response.json()
                if response.status_code != 200 or data.get("s") != "ok":
                    raise BrokerError("FUNDS_FETCH_FAILED", data.get("message", "Unknown error"))
                
                # Normalize response format for RiskEngine
                fund_limit = data.get("fund_limit", [])
                equity_data = next((item for item in fund_limit if item.get("title") == "Equity"), {})
                
                return {
                    "equity": {
                        "available_margin": equity_data.get("equityAmount", 0),
                        "used_margin": equity_data.get("utilizedAmount", 0)
                    }
                }
            except Exception as e:
                logger.error(f"Error fetching funds: {e}")
                raise

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch LTP/Quote for a symbol."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/quotes?symbols={symbol}",
                    headers=self.headers,
                    timeout=3.0
                )
                data = response.json()
                if response.status_code != 200 or data.get("s") != "ok":
                    raise BrokerError("QUOTE_FETCH_FAILED", data.get("message", "Unknown error"))
                
                quote_data = data.get("d", [{}])[0].get("v", {})
                return {
                    "ltp": quote_data.get("lp", 0),
                    "symbol": symbol
                }
            except Exception as e:
                logger.error(f"Error fetching quote for {symbol}: {e}")
                raise

    async def get_positions(self) -> list:
        """Fetch active positions."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/positions",
                    headers=self.headers,
                    timeout=5.0
                )
                data = response.json()
                if response.status_code != 200 or data.get("s") != "ok":
                    return []
                
                positions = data.get("netPositions", [])
                return [
                    {
                        "symbol": p["symbol"],
                        "net_qty": p["netQty"],
                        "ltp": p["ltp"],
                        "pnl": p["unrealizedProfit"]
                    } for p in positions
                ]
            except Exception as e:
                logger.error(f"Error fetching positions: {e}")
                return []

    async def get_orders(self) -> list:
        """Fetch orders for the day."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/orders",
                    headers=self.headers,
                    timeout=5.0
                )
                data = response.json()
                if response.status_code != 200 or data.get("s") != "ok":
                    return []
                
                orders = data.get("orderBook", [])
                # Map Fyers status to internal Status
                status_map = {
                    1: "CANCELLED",
                    2: "FILLED",
                    4: "TRANSIT",
                    5: "REJECTED",
                    6: "PENDING"
                }
                return [
                    {
                        "broker_order_id": o["id"],
                        "status": status_map.get(o["status"], "UNKNOWN"),
                        "filled_qty": o["filledQty"],
                        "avg_price": o["tradedPrice"]
                    } for o in orders
                ]
            except Exception as e:
                logger.error(f"Error fetching orders: {e}")
                return []

    async def submit_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Place an order with Fyers."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/orders",
                    headers=self.headers,
                    json=order_data,
                    timeout=5.0
                )
                data = response.json()
                if response.status_code != 200 or data.get("s") != "ok":
                    raise BrokerError("ORDER_PLACEMENT_FAILED", data.get("message", "Unknown error"))
                
                return data
            except Exception as e:
                logger.error(f"Error placing order: {e}")
                raise
