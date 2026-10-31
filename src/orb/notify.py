"""Telegram alerts. Disabled or misconfigured -> silent no-op; a failed send never stops trading."""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, enabled: bool, token: str | None = None, chat_id: str | None = None, prefix: str = "orb"):
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(enabled and self.token and self.chat_id)
        self.prefix = prefix
        if enabled and not self.enabled:
            log.warning("telegram enabled in config but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing")

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": f"[{self.prefix}] {text}"[:4000]},
                timeout=10,
            )
            return r.ok
        except requests.RequestException as e:
            log.warning("telegram send failed: %s", e)
            return False
