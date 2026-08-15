"""Telegram alerter. Fire-and-forget: any HTTP or config failure is
logged and swallowed — we never let an alert path crash the scheduler.

Message rate is bounded by a 1-second per-message spacing; if bursts
happen (e.g. reconciliation drift after a crash), they queue naturally.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from ..logging_config import get_logger
from ..settings import get_settings

log = get_logger(__name__)

_MAX_LEN = 4000  # Telegram hard cap is 4096; leave room for our prefix


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=8.0)

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        payload = {"chat_id": self._chat_id, "text": text[:_MAX_LEN],
                   "disable_web_page_preview": True}
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        async with self._lock:
            try:
                r = await self._client.post(url, data=payload)
                if r.status_code != 200:
                    log.warning("telegram.non_200", status=r.status_code,
                                body=r.text[:300])
                    return False
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram.error", error=str(exc)[:200])
                return False
            await asyncio.sleep(1.0)   # spacing
        return True

    async def close(self) -> None:
        await self._client.aclose()


_alerter: TelegramAlerter | None = None


def get_alerter() -> TelegramAlerter:
    """Singleton alerter reading TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    from env. Returns a stub if either is missing (send() no-ops)."""
    global _alerter
    if _alerter is None:
        s = get_settings()
        token = s.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat = s.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        _alerter = TelegramAlerter(token=token, chat_id=chat)
        if not _alerter.enabled:
            log.info("telegram.disabled",
                     reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        else:
            log.info("telegram.enabled", chat_id=chat)
    return _alerter


async def notify(event: str, **fields: Any) -> None:
    """Convenience: format `event key=value key=value ...` and send."""
    parts = [event] + [f"{k}={v}" for k, v in fields.items()]
    text = " ".join(parts)
    log.info("alert.emit", alert=event, fields=fields)
    await get_alerter().send(text)
