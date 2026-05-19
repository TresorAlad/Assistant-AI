"""
conversation_state.py - Etat par conversation WhatsApp (modes, proprio actif, etc.)
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Literal

OWNER_NAME = os.getenv("OWNER_NAME", "Trésor")

ChatMode = Literal["new", "awaiting_choice", "active", "waiting", "owner_took_over"]

WAIT_PATTERNS = re.compile(
    r"\b(attendre|attendrais|son retour|sa reponse|pas besoin|non merci|"
    r"je prefere attendre|laisse|laisser|plus tard|quand il sera|"
    r"quand elle sera|patienter|patience)\b",
    re.I,
)

ASSIST_PATTERNS = re.compile(
    r"\b(oui|ouais|ok|okay|vas[- ]?y|aide|assister|assist|maintenant|"
    r"je veux|stp|s'il te plait|d'accord|dac|go)\b",
    re.I,
)


@dataclass
class ChatState:
    mode: ChatMode = "new"
    contact_name: str = ""
    message_count: int = 0
    owner_took_over_at: float = 0.0
    last_user_text: str = ""


_chats: dict[str, ChatState] = {}


def get_state(chat_id: str) -> ChatState:
    if chat_id not in _chats:
        _chats[chat_id] = ChatState()
    return _chats[chat_id]


def set_contact_name(chat_id: str, name: str) -> None:
    name = (name or "").strip()
    if name and name != chat_id.split("@")[0]:
        get_state(chat_id).contact_name = name.split()[0] if name else ""


def display_name(chat_id: str) -> str:
    state = get_state(chat_id)
    if state.contact_name:
        return state.contact_name
    phone = chat_id.replace("@s.whatsapp.net", "").replace("@c.us", "")
    return phone[-4:] if len(phone) > 4 else "there"


def mark_owner_took_over(chat_id: str) -> None:
    state = get_state(chat_id)
    state.mode = "owner_took_over"
    state.owner_took_over_at = time.time()


def clear_owner_took_over(chat_id: str) -> None:
    state = get_state(chat_id)
    if state.mode == "owner_took_over":
        state.mode = "awaiting_choice" if state.message_count > 0 else "new"


def is_owner_active(chat_id: str) -> bool:
    return get_state(chat_id).mode == "owner_took_over"


def record_incoming(chat_id: str, text: str) -> None:
    state = get_state(chat_id)
    state.message_count += 1
    state.last_user_text = text


def wants_to_wait(text: str) -> bool:
    return bool(WAIT_PATTERNS.search(text))


def wants_assistance(text: str) -> bool:
    return bool(ASSIST_PATTERNS.search(text))


def first_greeting(chat_id: str) -> str:
    name = display_name(chat_id)
    if name.isdigit() or len(name) <= 3:
        return (
            f"Bonjour, {OWNER_NAME} n'est pas disponible pour le moment. "
            f"Je suis son assistant. Vous souhaitez que je vous aide maintenant "
            f"ou vous preferez attendre son retour ?"
        )
    return (
        f"Bonjour {name}, {OWNER_NAME} n'est pas disponible pour le moment. "
        f"Je suis son assistant. Vous souhaitez que je vous aide maintenant "
        f"ou vous preferez attendre son retour ?"
    )


def waiting_ack() -> str:
    return f"D'accord, je lui transmettrai votre message des qu'il sera disponible."


def quota_fallback(chat_id: str) -> str:
    name = display_name(chat_id)
    prefix = f"{name}, " if not name.isdigit() else ""
    return (
        f"{prefix}{OWNER_NAME} n'est pas dispo la. "
        f"Je note votre message et je lui transmets des qu'il peut. Merci."
    )
