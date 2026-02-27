"""
app/workers/telegram_worker.py

Background worker for polling Telegram bot commands.
"""
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class TelegramWorker:
    def __init__(self, notifier, reporting_service=None):
        self.notifier = notifier
        self.reporting_service = reporting_service
        self._running = False
        self._offset = 0
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if not self.notifier.enabled:
            logger.warning("TelegramWorker: Notifier disabled, worker will not start.")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll())
        logger.info("TelegramWorker started.")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TelegramWorker stopped.")

    async def _poll(self):
        url = f"{self.notifier.base_url}/getUpdates"
        timeout = 30 # Long polling
        
        while self._running:
            try:
                params = {
                    "offset": self._offset,
                    "timeout": timeout,
                    "allowed_updates": ["message"]
                }
                async with httpx.AsyncClient(timeout=timeout + 5) as client:
                    response = await client.get(url, params=params)
                    if response.status_code == 200:
                        data = response.json()
                        await self._process_updates(data.get("result", []))
                    else:
                        logger.error(f"Telegram poll failed: {response.status_code}")
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TelegramWorker poll error: {e}")
                await asyncio.sleep(5)

    async def _process_updates(self, updates: List[Dict[str, Any]]):
        for update in updates:
            self._offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue
            
            chat_id = str(message["chat"]["id"])
            # Only respond to the configured chat_id for security
            if chat_id != self.notifier.chat_id:
                logger.warning(f"Unauthorized command from chat_id: {chat_id}")
                continue
                
            text = message.get("text", "")
            if text.startswith("/"):
                await self._handle_command(text)

    async def _handle_command(self, text: str):
        parts = text.split()
        cmd = parts[0].lower()
        
        if cmd == "/start":
            await self.notifier.send_message(
                "🤖 *TradeDeck v2 Online*\n"
                "Institutional Trading Infrastructure\n\n"
                "Available commands:\n"
                "• `/strategy` - List registered strategies\n"
                "• `/strategy [name]` - Generate PDF report"
            )
        
        elif cmd == "/strategy":
            if len(parts) == 1:
                # List strategies (I might need to pass the executor or registry here)
                await self.notifier.send_message("Please specify a strategy name, e.g., `/strategy FAILED_AUCTION_B1`")
            else:
                strat_name = parts[1].upper()
                await self.notifier.send_message(f"⏳ Generating institutional report for *{strat_name}*...")
                
                if self.reporting_service:
                    try:
                        report_path = await self.reporting_service.generate_report(strat_name)
                        if report_path:
                            await self.notifier.send_document(report_path, caption=f"Institutional Audit: {strat_name}")
                        else:
                            await self.notifier.send_message(f"❌ Failed to generate report for {strat_name}. Check logs.")
                    except Exception as e:
                        logger.error(f"Reporting error: {e}")
                        await self.notifier.send_message(f"❌ Reporting error: {str(e)}")
                else:
                    await self.notifier.send_message("❌ Reporting service not initialized.")
        
        else:
            # Silently ignore unknown commands or log if needed
            pass
