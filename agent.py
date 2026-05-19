"""
agent.py - Moteur d'assistant IA personnel (WhatsApp / Gmail)
"""

import json
import os
import re
import contextvars
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from gmail_reader import send_email, fetch_emails
from llm_provider import AgentQuotaError, AllProvidersExhausted, create_chat_session
from prompts import ASSISTANT_CLIENT_PROMPT, ASSISTANT_OWNER_PROMPT
from whatsapp_sender import send_message

load_dotenv()

active_client_chat_id = contextvars.ContextVar("active_client_chat_id", default="")

SENSITIVE_KEYWORDS = (
    "mot de passe", "password", "code secret", "rib", "iban",
    "carte bancaire", "cvv", "pin", "compte bancaire", "virement",
    "credentials", "token", "api key",
)

CONTACTS_DB = {
    "koffi": {"email": "koffi@example.com", "phone": "+22800000001"},
    "jean": {"email": "jean@example.com", "phone": "+22800000002"},
}


class AgentDecision(BaseModel):
    type: Literal["reply", "escalation", "action", "skip"]
    message: str = ""
    service: Optional[Literal["whatsapp", "gmail", "other"]] = None
    task: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


def _contains_sensitive_text(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)


def _parse_decision(raw: str) -> AgentDecision:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
        return AgentDecision.model_validate(payload)
    except (json.JSONDecodeError, ValueError):
        return AgentDecision(type="reply", message=text or "Je n'ai pas pu traiter votre message.")


def get_agent_chat(is_owner: bool = False):
    instruction = ASSISTANT_OWNER_PROMPT if is_owner else ASSISTANT_CLIENT_PROMPT
    return create_chat_session(instruction, AgentDecision.model_json_schema())


def _get_decision(chat, user_text: str, chat_id: str) -> AgentDecision:
    raw = chat.send_message(user_text, chat_id=chat_id)
    return _parse_decision(raw)


def _search_contact(name: str) -> str:
    name_lower = name.lower()
    for contact_name, info in CONTACTS_DB.items():
        if contact_name in name_lower or name_lower in contact_name:
            return (
                f"Contact : {contact_name.capitalize()} | "
                f"Email : {info['email']} | Tel : {info['phone']}"
            )
    return f"Aucun contact trouve pour '{name}'."


def _notify_owner_escalation(client_chat_id: str, summary: str) -> bool:
    owner_phone = os.getenv("OWNER_PHONE", "").strip()
    if not owner_phone:
        return False
    client_display = client_chat_id.replace("@c.us", "").replace("@s.whatsapp.net", "")
    owner_message = (
        "--- ESCALADE ASSISTANT ---\n"
        f"Client : {client_display}\n"
        f"Situation : {summary}\n\n"
        f"Pour repondre :\n/repondre {client_display} : [votre reponse]"
    )
    return send_message(owner_message, phone=owner_phone)


def _execute_action(decision: AgentDecision, chat_id: str, is_owner: bool) -> tuple[bool, str]:
    service = (decision.service or "").lower()
    task = (decision.task or "").lower()
    data = decision.data or {}

    combined_text = f"{decision.message} {task} {json.dumps(data, ensure_ascii=False)}"
    if _contains_sensitive_text(combined_text):
        return False, "Action bloquee : sujet sensible. Validation humaine requise."

    if not is_owner and service in ("gmail", "other"):
        return False, "Action non autorisee pour les contacts externes."

    if service == "gmail":
        if task == "send_email":
            to = str(data.get("to", "")).strip()
            subject = str(data.get("subject", "")).strip()
            body = str(data.get("body", "")).strip()
            if not to or not subject:
                return False, "Parametres email incomplets (to, subject requis)."
            ok = send_email(to, subject, body)
            return (True, f"Email envoye a {to}.") if ok else (False, f"Echec d'envoi a {to}.")
        if task == "read_emails":
            max_results = int(data.get("max_results", 5))
            emails = fetch_emails(max_results=max_results)
            if not emails:
                return True, "Aucun nouvel email dans la boite de reception."
            lines = [
                f"Email {i} :\nDe : {e.get('from')}\nSujet : {e.get('subject')}\nExtrait : {e.get('snippet')}\n"
                for i, e in enumerate(emails, 1)
            ]
            return True, "\n".join(lines)
        return False, f"Tache Gmail inconnue : {task}"

    if service == "whatsapp":
        if task == "send_message":
            message = str(data.get("message", "")).strip()
            if not message:
                return False, "Message WhatsApp vide."
            phone = str(data.get("phone", "")).strip() or None
            ok = send_message(message, phone=phone)
            return (True, f"Message WhatsApp envoye.") if ok else (False, "Echec d'envoi WhatsApp.")
        return False, f"Tache WhatsApp inconnue : {task}"

    if service == "other" and task == "search_contact":
        name = str(data.get("name", "")).strip()
        if not name:
            return False, "Nom de contact manquant."
        return True, _search_contact(name)

    return False, f"Action non supportee : {service}/{task}"


def route_decision(decision: AgentDecision, chat_id: str, is_owner: bool) -> str:
    if decision.type == "skip":
        return ""

    if decision.type == "reply":
        return decision.message.strip() or "Message recu."

    if decision.type == "escalation":
        summary = decision.message.strip() or "Demande necessitant une intervention humaine."
        if is_owner:
            return f"Validation requise : {summary}"
        notified = _notify_owner_escalation(chat_id, summary)
        if notified:
            return (
                "Merci, je transmets a mon proprietaire et je reviens vers vous des que possible."
            )
        return "Votre demande est notee, il vous recontactera prochainement."

    if decision.type == "action":
        ok, result = _execute_action(decision, chat_id, is_owner)
        ack = decision.message.strip()
        if ok:
            return f"{ack}\n\n{result}" if ack else result
        if is_owner:
            return f"Je n'ai pas pu executer l'action : {result}"
        return "Desole, je ne peux pas faire ca pour le moment."

    return "Je n'ai pas compris votre demande."


def handle_message(chat, user_text: str, chat_id: str, is_owner: bool) -> str:
    token = active_client_chat_id.set(chat_id)
    try:
        decision = _get_decision(chat, user_text, chat_id)
        if not is_owner and decision.type == "action" and decision.service == "gmail":
            decision = AgentDecision(
                type="escalation",
                message=f"Demande email client : {decision.task}. Msg : {user_text[:200]}",
            )
        return route_decision(decision, chat_id, is_owner)
    finally:
        active_client_chat_id.reset(token)


def ask_agent(chat, user_text: str) -> str:
    chat_id = active_client_chat_id.get() or os.getenv("OWNER_PHONE", "")
    is_owner = True
    owner_chat = os.getenv("OWNER_PHONE", "").strip().replace("+", "").replace(" ", "")
    if owner_chat and "@s.whatsapp.net" not in owner_chat:
        owner_chat = f"{owner_chat}@s.whatsapp.net"
    if chat_id and owner_chat:
        is_owner = chat_id == owner_chat
    return handle_message(chat, user_text, chat_id or owner_chat, is_owner)


def run_agent_cli():
    print("=" * 50)
    print("Assistant Personnel - Mode proprietaire")
    print("Tapez 'exit' pour quitter.")
    print("=" * 50)
    owner_phone = os.getenv("OWNER_PHONE", "owner-cli")
    chat = get_agent_chat(is_owner=True)
    while True:
        try:
            user_input = input("\nProprietaire : ").strip()
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue
            reply = handle_message(chat, user_input, owner_phone, is_owner=True)
            print(f"\nAssistant : {reply}")
        except (AgentQuotaError, AllProvidersExhausted) as e:
            print(f"\n{e}")
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    run_agent_cli()
