"""
agent.py - Moteur d'assistant IA personnel (WhatsApp / Gmail)

Architecture :
- Le modele produit une decision structuree (reply | escalation | action)
- Le backend execute les actions via les APIs reelles
- Jamais de confirmation d'action sans resultat API
"""

import json
import os
import re
import contextvars
from typing import Any, Literal, Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from gmail_reader import send_email, fetch_emails
from whatsapp_sender import send_message

load_dotenv()

active_client_chat_id = contextvars.ContextVar("active_client_chat_id", default="")

gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    print("ATTENTION: GEMINI_API_KEY non trouvee dans .env")
    gemini_api_key = "DUMMY_KEY"

client = genai.Client(api_key=gemini_api_key)

MODEL_NAME = "gemini-2.5-flash"

SENSITIVE_KEYWORDS = (
    "mot de passe",
    "password",
    "code secret",
    "rib",
    "iban",
    "carte bancaire",
    "cvv",
    "pin",
    "compte bancaire",
    "virement",
    "credentials",
    "token",
    "api key",
)

CONTACTS_DB = {
    "koffi": {"email": "koffi@example.com", "phone": "+22800000001"},
    "jean": {"email": "jean@example.com", "phone": "+22800000002"},
    "esgis ia": {"email": "esgis.ia@example.com", "phone": "123456789@g.us"},
}


class AgentDecision(BaseModel):
    type: Literal["reply", "escalation", "action"]
    message: str = ""
    service: Optional[Literal["whatsapp", "gmail", "other"]] = None
    task: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


SYSTEM_INSTRUCTION_CORE = """
Tu es un assistant personnel integre a WhatsApp et aux emails.
Tu reponds TOUJOURS en francais, de maniere concise et professionnelle.

Regles :
1. Lis le message entrant (texte ou note vocale transcrite, prefixee [Message vocal]).
2. Identifie l'intention : question, demande, tache, urgence.
3. Reponds automatiquement (type "reply") uniquement si ta confiance est elevee.
4. Escalade (type "escalation") si :
   - donnees sensibles (banque, mots de passe, comptes personnels)
   - confiance faible ou ambiguite
   - action impossible sans validation humaine
5. Propose une action (type "action") pour les taches executables via API.
6. Ne pretends JAMAIS qu'une action est faite : le systeme l'executera et confirmera.

Format de sortie JSON strict (un seul objet) :
- "reply" : reponse directe au correspondant (champ "message" obligatoire)
- "escalation" : resume clair de la situation + ce dont le proprietaire a besoin ("message")
- "action" : champs "service" (whatsapp|gmail|other), "task", "data", et "message" (accuse reception court)

Taches action supportees :
- gmail / send_email : data {to, subject, body}
- gmail / read_emails : data {max_results} (optionnel, defaut 5)
- whatsapp / send_message : data {message, phone} (phone optionnel)
- other / search_contact : data {name}
"""

SYSTEM_INSTRUCTION_OWNER = (
    SYSTEM_INSTRUCTION_CORE
    + """
Contexte : canal prive avec ton proprietaire.
Tu peux utiliser les actions gmail et whatsapp pour executer ses demandes.
Pour les sujets sensibles, prefere "escalation" avec un resume demandant confirmation explicite.
"""
)

SYSTEM_INSTRUCTION_CLIENT = (
    SYSTEM_INSTRUCTION_CORE
    + """
Contexte : client ou contact externe du proprietaire.
Tu n'as PAS acces aux emails ni aux outils personnels du proprietaire.
- Questions simples : "reply"
- Informations privees, demandes incertaines ou sensibles : "escalation"
- N'utilise JAMAIS "action" avec service gmail.
"""
)


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
    instruction = SYSTEM_INSTRUCTION_OWNER if is_owner else SYSTEM_INSTRUCTION_CLIENT
    config = types.GenerateContentConfig(
        system_instruction=instruction,
        response_mime_type="application/json",
        response_schema=AgentDecision,
    )
    return client.chats.create(model=MODEL_NAME, config=config)


def _get_decision(chat, user_text: str) -> AgentDecision:
    response = chat.send_message(user_text)
    if response.parsed:
        return AgentDecision.model_validate(response.parsed)
    if response.text:
        return _parse_decision(response.text)
    return AgentDecision(
        type="reply",
        message="Desole, je n'ai pas pu formuler de reponse.",
    )


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

    client_display = client_chat_id.replace("@c.us", "")
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
            if ok:
                return True, f"Email envoye a {to}."
            return False, f"Echec d'envoi de l'email a {to}."

        if task == "read_emails":
            max_results = int(data.get("max_results", 5))
            emails = fetch_emails(max_results=max_results)
            if not emails:
                return True, "Aucun nouvel email dans la boite de reception."
            lines = []
            for i, email in enumerate(emails, 1):
                lines.append(
                    f"Email {i} :\nDe : {email.get('from')}\n"
                    f"Sujet : {email.get('subject')}\n"
                    f"Extrait : {email.get('snippet')}\n"
                )
            return True, "\n".join(lines)

        return False, f"Tache Gmail inconnue : {task}"

    if service == "whatsapp":
        if task == "send_message":
            message = str(data.get("message", "")).strip()
            if not message:
                return False, "Message WhatsApp vide."
            phone = str(data.get("phone", "")).strip() or None
            ok = send_message(message, phone=phone)
            if ok:
                target = phone or "destinataire principal"
                return True, f"Message WhatsApp envoye ({target})."
            return False, "Echec d'envoi du message WhatsApp."

        return False, f"Tache WhatsApp inconnue : {task}"

    if service == "other" and task == "search_contact":
        name = str(data.get("name", "")).strip()
        if not name:
            return False, "Nom de contact manquant."
        return True, _search_contact(name)

    return False, f"Action non supportee : {service}/{task}"


def route_decision(
    decision: AgentDecision,
    chat_id: str,
    is_owner: bool,
) -> str:
    """Applique la decision et retourne le message a envoyer au correspondant."""
    if decision.type == "reply":
        return decision.message.strip() or "Message recu."

    if decision.type == "escalation":
        summary = decision.message.strip() or "Demande necessitant une intervention humaine."
        if is_owner:
            return f"Validation requise : {summary}"
        notified = _notify_owner_escalation(chat_id, summary)
        if notified:
            return (
                "Merci pour votre message. Je transmets votre demande a mon proprietaire "
                "et je reviens vers vous des que possible."
            )
        return (
            "Votre demande a ete enregistree. Je ne peux pas y repondre immediatement, "
            "mais elle sera traitee prochainement."
        )

    if decision.type == "action":
        ok, result = _execute_action(decision, chat_id, is_owner)
        ack = decision.message.strip()
        if ok:
            if ack:
                return f"{ack}\n\n{result}"
            return result
        if is_owner:
            return f"Je n'ai pas pu executer l'action : {result}"
        return "Desole, je ne peux pas effectuer cette action pour le moment."

    return "Je n'ai pas compris votre demande."


def handle_message(chat, user_text: str, chat_id: str, is_owner: bool) -> str:
    """Analyse le message, route la decision, retourne la reponse utilisateur."""
    token = active_client_chat_id.set(chat_id)
    try:
        decision = _get_decision(chat, user_text)
        if not is_owner and decision.type == "action" and decision.service == "gmail":
            decision = AgentDecision(
                type="escalation",
                message=(
                    f"Le client {chat_id.replace('@c.us', '')} demande une action email : "
                    f"{decision.task}. Message : {user_text[:200]}"
                ),
            )
        return route_decision(decision, chat_id, is_owner)
    finally:
        active_client_chat_id.reset(token)


def ask_agent(chat, user_text: str) -> str:
    """Compatibilite : traite comme proprietaire si contexte inconnu."""
    chat_id = active_client_chat_id.get() or os.getenv("OWNER_PHONE", "")
    is_owner = True
    owner_chat = os.getenv("OWNER_PHONE", "").strip().replace("+", "").replace(" ", "")
    if owner_chat and "@c.us" not in owner_chat:
        owner_chat = f"{owner_chat}@c.us"
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
            if user_input.lower() in ["exit", "quit"]:
                break
            if not user_input:
                continue
            print("Assistant en cours...")
            reply = handle_message(chat, user_input, owner_phone, is_owner=True)
            print(f"\nAssistant : {reply}")
        except KeyboardInterrupt:
            print("\nArret.")
            break
        except Exception as e:
            print(f"\nErreur : {e}")


if __name__ == "__main__":
    run_agent_cli()
