"""
webhook.py - Serveur FastAPI pour recevoir les messages WhatsApp via Evolution API
"""

import os
import logging
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv

from agent import get_agent_chat
from conversation_state import mark_owner_took_over
from llm_provider import AgentQuotaError, can_transcribe_voice
from message_router import process_contact_message, process_owner_message
from audio_transcriber import transcribe_whatsapp_voice
from whatsapp_sender import send_message

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("webhook")

app = FastAPI(title="AI Agent WhatsApp Webhook (Evolution API)")

user_sessions = {}
processing_set = set()
bot_message_ids = set()

OWNER_PHONE = os.getenv("OWNER_PHONE", "")


def get_owner_chat_id() -> str:
    phone = OWNER_PHONE.strip().replace("+", "").replace(" ", "")
    if "@s.whatsapp.net" not in phone:
        phone = f"{phone}@s.whatsapp.net"
    return phone


def _send_if_reply(chat_id: str, reply: str | None) -> None:
    if not reply or not reply.strip():
        log.info(f"[SKIP SEND] Pas de reponse pour {chat_id}")
        return
    msg_id = send_message(reply, phone=chat_id)
    if isinstance(msg_id, str):
        bot_message_ids.add(msg_id)


def _get_or_create_chat(chat_id: str, is_owner: bool):
    if chat_id not in user_sessions:
        log.info(f"[SESSION] Nouvelle session {chat_id} (owner={is_owner})")
        user_sessions[chat_id] = get_agent_chat(is_owner=is_owner)
    return user_sessions[chat_id]


def _run_agent_for_chat(chat_id: str, text: str, push_name: str = ""):
    is_owner = chat_id == get_owner_chat_id()
    chat = _get_or_create_chat(chat_id, is_owner)

    log.info(f"[AGENT] {chat_id} (owner={is_owner}) : {text[:80]}")

    if is_owner:
        reply = process_owner_message(chat, chat_id, text)
    else:
        reply = process_contact_message(chat, chat_id, text, push_name)

    if reply:
        log.info(f"[AGENT] Reponse : {reply[:80]}")
    _send_if_reply(chat_id, reply)


def process_whatsapp_voice(chat_id: str, id_message: str, file_data: dict, push_name: str = ""):
    if chat_id in processing_set:
        return
    processing_set.add(chat_id)

    try:
        if not can_transcribe_voice():
            _send_if_reply(
                chat_id,
                "Je ne peux pas traiter les vocaux pour le moment (quota). "
                "Ecrivez votre message en texte svp.",
            )
            return

        msg_id1 = send_message("Vocal recu, un instant...", phone=chat_id)
        if isinstance(msg_id1, str):
            bot_message_ids.add(msg_id1)

        transcription = transcribe_whatsapp_voice(chat_id, id_message, file_data)
        if not transcription or transcription == "[inaudible]":
            _send_if_reply(
                chat_id,
                "Je n'ai pas bien compris le vocal. Pouvez-vous l'ecrire en texte ?",
            )
            return

        _run_agent_for_chat(chat_id, f"[Message vocal] {transcription}", push_name)
    except AgentQuotaError as e:
        _send_if_reply(chat_id, e.user_message)
    except Exception as e:
        log.error(f"[ERROR] Vocal {chat_id} : {e}", exc_info=True)
        _send_if_reply(chat_id, "Vocal non traite. Envoyez un message texte.")
    finally:
        processing_set.discard(chat_id)


def process_whatsapp_message(chat_id: str, text: str, push_name: str = ""):
    if chat_id in processing_set:
        return
    processing_set.add(chat_id)
    try:
        _run_agent_for_chat(chat_id, text, push_name)
    except AgentQuotaError as e:
        _send_if_reply(chat_id, e.user_message)
    except ValueError as e:
        _send_if_reply(chat_id, str(e))
    except Exception as e:
        log.error(f"[ERROR] {chat_id} : {e}", exc_info=True)
        _send_if_reply(chat_id, "Desole, une erreur interne s'est produite.")
    finally:
        processing_set.discard(chat_id)


@app.post("/webhook")
async def evolution_api_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        event_type = payload.get("event", "")
        log.info(f"[WEBHOOK] event={event_type}")

        if event_type != "messages.upsert":
            return {"status": "ignored", "reason": f"unhandled_event_{event_type}"}

        data = payload.get("data", {})
        if not data:
            return {"status": "ignored", "reason": "empty_data"}

        key = data.get("key", {})
        chat_id = key.get("remoteJid", "")
        message_id = key.get("id", "")
        from_me = key.get("fromMe", False)
        push_name = data.get("pushName", "") or ""

        owner_id = get_owner_chat_id()

        if from_me:
            if message_id in bot_message_ids:
                return {"status": "ignored", "reason": "bot_message"}
            if chat_id != owner_id:
                mark_owner_took_over(chat_id)
                log.info(f"[PROPRIO] Reprise manuelle sur {chat_id} — assistant coupe")
                return {"status": "ignored", "reason": "owner_replied"}

        if "status@broadcast" in chat_id:
            return {"status": "ignored", "reason": "status"}
        if chat_id.endswith("@g.us"):
            return {"status": "ignored", "reason": "group_message"}

        message = data.get("message", {})
        if not message:
            return {"status": "ignored", "reason": "no_message_content"}

        text = ""
        if "conversation" in message:
            text = message.get("conversation", "")
        elif "extendedTextMessage" in message:
            text = message.get("extendedTextMessage", {}).get("text", "")
        elif "audioMessage" in message:
            audio_data = message.get("audioMessage", {})
            file_data = {"mimeType": audio_data.get("mimetype", "audio/ogg")}
            background_tasks.add_task(
                process_whatsapp_voice, chat_id, message_id, file_data, push_name
            )
            return {"status": "processing", "detail": "voice"}

        if not text:
            return {"status": "ignored", "reason": "no_text"}

        if chat_id == owner_id and text.startswith("/repondre "):
            try:
                parts = text[len("/repondre ") :].split(":", 1)
                if len(parts) == 2:
                    target = parts[0].strip()
                    if not target.endswith("@s.whatsapp.net"):
                        target = f"{target}@s.whatsapp.net"
                    msg_id = send_message(parts[1].strip(), phone=target)
                    if isinstance(msg_id, str):
                        bot_message_ids.add(msg_id)
                    confirm = send_message(
                        f"Envoye a {target.replace('@s.whatsapp.net', '')}.",
                        phone=owner_id,
                    )
                    if isinstance(confirm, str):
                        bot_message_ids.add(confirm)
                    return {"status": "success"}
            except Exception as ex:
                _send_if_reply(owner_id, f"Erreur /repondre : {ex}")
                return {"status": "error"}

        log.info(f"[MSG] {chat_id} ({push_name}) -> {text[:100]}")
        background_tasks.add_task(process_whatsapp_message, chat_id, text, push_name)
        return {"status": "processing"}

    except Exception as e:
        log.error(f"[ERROR] Webhook : {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/")
def home():
    from quota_manager import get_stats
    return {
        "status": "running",
        "service": "AI Agent WhatsApp",
        "quota": get_stats(),
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("webhook:app", host="0.0.0.0", port=port, reload=True)
