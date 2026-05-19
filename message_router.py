"""
message_router.py - Routage intelligent sans gaspiller le quota API
"""

from __future__ import annotations

import logging

from agent import get_agent_chat, handle_message
from conversation_state import (
    clear_owner_took_over,
    display_name,
    first_greeting,
    get_state,
    is_owner_active,
    mark_owner_took_over,
    record_incoming,
    set_contact_name,
    waiting_ack,
    wants_assistance,
    wants_to_wait,
    quota_fallback,
)
from llm_provider import AllProvidersExhausted, AgentQuotaError
from message_filter import should_skip_llm
from quota_manager import can_call_llm

log = logging.getLogger("message_router")


def process_owner_message(chat, chat_id: str, text: str) -> str | None:
    """Canal proprietaire (Message a soi)."""
    if text.startswith("/reprendre "):
        target = text[len("/reprendre ") :].strip()
        if not target.endswith("@s.whatsapp.net"):
            target = f"{target}@s.whatsapp.net"
        clear_owner_took_over(target)
        return f"Assistant reactive pour {target.replace('@s.whatsapp.net', '')}."

    skip, _ = should_skip_llm(text)
    if skip:
        return None

    ok, reason = can_call_llm(chat_id)
    if not ok:
        log.info("[ROUTER] Owner quota skip: %s", reason)
        return "Quota IA atteint pour aujourd'hui. Reessayez plus tard ou configurez Ollama local."

    return handle_message(chat, text, chat_id, is_owner=True)


def process_contact_message(
    chat,
    chat_id: str,
    text: str,
    push_name: str = "",
) -> str | None:
    """
    Retourne le texte a envoyer, ou None pour ne pas repondre.
    """
    set_contact_name(chat_id, push_name)
    record_incoming(chat_id, text)
    state = get_state(chat_id)

    if is_owner_active(chat_id):
        log.info("[ROUTER] Proprio actif sur %s — silence assistant", chat_id)
        return None

    skip, reason = should_skip_llm(text)
    if skip:
        log.info("[ROUTER] Message ignore (%s) : %s", reason, text[:40])
        return None

    # --- Premier message : accueil SANS IA ---
    if state.mode == "new":
        state.mode = "awaiting_choice"
        log.info("[ROUTER] Accueil template (0 token) pour %s", display_name(chat_id))
        return first_greeting(chat_id)

    # --- En attente du choix aide / attendre ---
    if state.mode == "awaiting_choice":
        if wants_to_wait(text):
            state.mode = "waiting"
            return waiting_ack()
        if wants_assistance(text):
            state.mode = "active"
        else:
            state.mode = "active"

    # --- Mode attente : silence sauf nouveau message substantiel ---
    if state.mode == "waiting":
        if wants_to_wait(text) or should_skip_llm(text)[0]:
            return None
        state.mode = "active"

    # --- Appel IA seulement en mode actif ---
    ok, reason = can_call_llm(chat_id)
    if not ok:
        log.warning("[ROUTER] Quota local: %s — reponse template", reason)
        return quota_fallback(chat_id)

    enriched = (
        f"[contact_name={display_name(chat_id)}] [mode={state.mode}]\n{text}"
    )
    try:
        return handle_message(chat, enriched, chat_id, is_owner=False)
    except AllProvidersExhausted:
        return quota_fallback(chat_id)
    except AgentQuotaError:
        return quota_fallback(chat_id)
