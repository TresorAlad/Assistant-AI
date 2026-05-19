"""
quota_manager.py - Budget journalier, cooldown global et limite par conversation
"""

from __future__ import annotations

import os
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

DAILY_BUDGET = int(os.getenv("LLM_DAILY_BUDGET", "100"))
MIN_INTERVAL_SEC = int(os.getenv("LLM_MIN_INTERVAL_SECONDS", "10"))
GLOBAL_COOLDOWN_SEC = int(os.getenv("LLM_GLOBAL_COOLDOWN_SECONDS", "120"))

_daily_date = date.today()
_daily_count = 0
_chat_last_call: dict[str, float] = {}
_global_cooldown_until = 0.0


def _reset_daily_if_needed() -> None:
    global _daily_date, _daily_count
    today = date.today()
    if today != _daily_date:
        _daily_date = today
        _daily_count = 0


def record_llm_call(chat_id: str = "") -> None:
    global _daily_count
    _reset_daily_if_needed()
    _daily_count += 1
    if chat_id:
        _chat_last_call[chat_id] = time.time()


def set_global_cooldown(seconds: int) -> None:
    global _global_cooldown_until
    _global_cooldown_until = time.time() + max(seconds, GLOBAL_COOLDOWN_SEC)


def get_stats() -> dict:
    _reset_daily_if_needed()
    remaining = max(0, DAILY_BUDGET - _daily_count)
    cooldown_left = max(0, int(_global_cooldown_until - time.time()))
    return {
        "daily_used": _daily_count,
        "daily_budget": DAILY_BUDGET,
        "daily_remaining": remaining,
        "cooldown_seconds": cooldown_left,
    }


def can_call_llm(chat_id: str) -> tuple[bool, str]:
    """Retourne (autorise, raison si refuse)."""
    _reset_daily_if_needed()
    now = time.time()

    if now < _global_cooldown_until:
        left = int(_global_cooldown_until - now)
        return False, f"cooldown_global:{left}"

    if _daily_count >= DAILY_BUDGET:
        return False, "daily_budget_exceeded"

    last = _chat_last_call.get(chat_id, 0)
    if chat_id and now - last < MIN_INTERVAL_SEC:
        return False, f"chat_throttle:{int(MIN_INTERVAL_SEC - (now - last))}"

    return True, "ok"
