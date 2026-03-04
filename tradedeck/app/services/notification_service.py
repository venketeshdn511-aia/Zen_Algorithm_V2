"""
app/services/notification_service.py

Asynchronous Telegram notification service.
"""
import logging
import httpx
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Handles outgoing Telegram notifications (messages, documents, alerts).
    """
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)
        
        if not self.enabled:
            logger.warning("Telegram Bot Token or Chat ID missing. Notifications disabled.")

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a text message to Telegram."""
        if not self.enabled:
            return False
            
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    return True
                else:
                    # Retry once without parse_mode if it failed (often due to formatting)
                    if response.status_code == 400:
                        payload.pop("parse_mode", None)
                        resp2 = await client.post(url, json=payload)
                        if resp2.status_code == 200:
                            logger.warning("Telegram Markdown parse failed, but fallback to plain text succeeded.")
                            return True
                        else:
                            logger.error(f"Telegram send_message fallback failed: {resp2.status_code} - {resp2.text}")
                            return False
                            
                    logger.error(f"Telegram send_message failed: {response.status_code} - {response.text}")
                    return False
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    async def send_document(self, file_path: str, caption: Optional[str] = None) -> bool:
        """Send a document (PDF, etc.) to Telegram."""
        if not self.enabled:
            return False
            
        url = f"{self.base_url}/sendDocument"
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption
            
        try:
            with open(file_path, "rb") as f:
                files = {"document": f}
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(url, data=data, files=files)
                    if response.status_code == 200:
                        return True
                    else:
                        logger.error(f"Telegram send_document failed: {response.status_code} - {response.text}")
                        return False
        except Exception as e:
            logger.error(f"Error sending Telegram document: {e}")
            return False

    async def alert_entry(self, strategy: str, symbol: str, side: str, price: float, qty: int):
        """Standardized entry alert."""
        emoji = "🚀" if side.upper() == "BUY" else "📉"
        msg = (
            f"{emoji} *ENTRY: {strategy}*\n"
            f"Symbol: `{symbol}`\n"
            f"Side: *{side.upper()}*\n"
            f"Price: ₹{price:,.2f}\n"
            f"Qty: {qty}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        return await self.send_message(msg)

    async def alert_exit(self, strategy: str, symbol: str, side: str, price: float, pnl: float, reason: str):
        """Standardized exit alert."""
        emoji = "💰" if pnl > 0 else "🔴"
        msg = (
            f"{emoji} *EXIT: {strategy}*\n"
            f"Symbol: `{symbol}`\n"
            f"Side: *{side.upper()}*\n"
            f"Exit Price: ₹{price:,.2f}\n"
            f"PnL: ₹{pnl:+.2f}\n"
            f"Reason: {reason}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        return await self.send_message(msg)

    async def alert_error(self, strategy: str, error: str):
        """System error alert."""
        msg = (
            f"⚠️ *ERROR: {strategy}*\n"
            f"Message: `{error}`\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        return await self.send_message(msg)
