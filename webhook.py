"""
webhook.py - Serveur FastAPI pour recevoir les messages WhatsApp via Green API
"""

import os
import json
import logging
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv

from agent import get_agent_chat, handle_message
from audio_transcriber import transcribe_whatsapp_voice
from whatsapp_sender import send_message

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("webhook")

app = FastAPI(title="AI Agent WhatsApp Webhook")

# Memoire en RAM : { "chatId": session_gemini }
user_sessions = {}

# Ensemble des chatId deja en cours de traitement (anti boucle infinie)
processing_set = set()

# Le chatId de l'instance elle-meme pour ignorer ses propres messages sortants
OWN_PHONE = os.getenv("PHONE_NUMBER", "")
# Le chatId du proprietaire de l'assistant (qui a les privileges)
OWNER_PHONE = os.getenv("OWNER_PHONE", "")


def get_own_chat_id() -> str:
    """Retourne le chatId de l'instance pour filtrer ses propres messages."""
    phone = OWN_PHONE.strip().replace("+", "").replace(" ", "")
    return f"{phone}@c.us"


def get_owner_chat_id() -> str:
    """Retourne le chatId du proprietaire pour detecter ses messages."""
    phone = OWNER_PHONE.strip().replace("+", "").replace(" ", "")
    if "@c.us" not in phone:
        phone = f"{phone}@c.us"
    return phone


def _run_agent_for_chat(chat_id: str, text: str):
    """Execute l'agent et envoie la reponse (sans gestion du verrou)."""
    is_owner = chat_id == get_owner_chat_id()

    if chat_id not in user_sessions:
        log.info(f"[SESSION] Nouvelle session pour {chat_id} (is_owner={is_owner})")
        user_sessions[chat_id] = get_agent_chat(is_owner=is_owner)

    chat = user_sessions[chat_id]

    log.info(f"[AGENT] Traitement pour {chat_id} (owner={is_owner}) : {text[:80]}")
    reply = handle_message(chat, text, chat_id, is_owner)
    log.info(f"[AGENT] Reponse : {reply[:80]}")

    send_message(reply, phone=chat_id)


def process_whatsapp_voice(chat_id: str, id_message: str, file_data: dict):
    """Telecharge, transcrit un vocal francais, puis traite comme un message texte."""
    if chat_id in processing_set:
        log.warning(f"[SKIP] {chat_id} deja en cours de traitement (vocal).")
        return
    processing_set.add(chat_id)

    try:
        send_message("Message vocal recu, transcription en cours...", phone=chat_id)
        transcription = transcribe_whatsapp_voice(chat_id, id_message, file_data)

        if not transcription or transcription == "[inaudible]":
            send_message(
                "Je n'ai pas reussi a comprendre votre message vocal. "
                "Pouvez-vous le renvoyer ou l'ecrire en texte ?",
                phone=chat_id,
            )
            return

        text = f"[Message vocal] {transcription}"
        log.info(f"[AUDIO] Texte transcrit pour {chat_id} : {transcription[:100]}")
        _run_agent_for_chat(chat_id, text)

    except Exception as e:
        log.error(f"[ERROR] Transcription vocale pour {chat_id} : {e}", exc_info=True)
        try:
            send_message(
                "Desole, je n'ai pas pu transcrire votre vocal. "
                "Reessayez ou envoyez un message texte.",
                phone=chat_id,
            )
        except Exception:
            pass
    finally:
        processing_set.discard(chat_id)


def process_whatsapp_message(chat_id: str, text: str):
    """
    Traite le message avec l'agent Gemini et renvoie la reponse sur WhatsApp.
    Protege contre les boucles infinies via processing_set.
    """
    if chat_id in processing_set:
        log.warning(f"[SKIP] {chat_id} deja en cours de traitement.")
        return
    processing_set.add(chat_id)

    try:
        _run_agent_for_chat(chat_id, text)
    except Exception as e:
        log.error(f"[ERROR] Traitement pour {chat_id} : {e}", exc_info=True)
        try:
            send_message("Desole, une erreur interne s'est produite.", phone=chat_id)
        except Exception:
            pass
    finally:
        processing_set.discard(chat_id)


@app.post("/webhook")
async def green_api_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint appele par Green API a chaque evenement.
    """
    try:
        payload = await request.json()
        webhook_type = payload.get("typeWebhook", "")

        # Logguer tous les types d'evenements pour le debug
        log.info(f"[WEBHOOK] type={webhook_type}")

        # On traite uniquement les messages entrants (de quelqu'un d'autre)
        # et les messages sortants quand l'utilisateur parle au bot depuis son propre appareil
        if webhook_type not in ["incomingMessageReceived", "outgoingMessageReceived"]:
            return {"status": "ignored", "reason": webhook_type}

        sender_data = payload.get("senderData", {})
        chat_id = sender_data.get("chatId", "")
        sender_id = sender_data.get("sender", "")

        # 1. Ignorer les statuts
        if "status@broadcast" in chat_id:
            return {"status": "ignored", "reason": "status"}

        # 2. Ignorer STRICTEMENT les groupes
        if chat_id.endswith("@g.us"):
            log.info(f"[SKIP] Message de groupe ({chat_id}) ignore.")
            return {"status": "ignored", "reason": "group_message"}

        # 3. Anti-boucle : ignorer les messages envoyes par l'instance elle-meme (sauf si c'est la discussion avec soi-meme)
        # Green API met le numero de l'instance dans "sender" avec ":0" parfois
        own_id = get_own_chat_id()
        if chat_id != own_id:
            if sender_id and sender_id.startswith(own_id.replace("@c.us", "")):
                log.info(f"[SKIP] Message envoye par l'instance elle-meme ({sender_id}), ignore.")
                return {"status": "ignored", "reason": "own_message"}

        # Extraire le texte du message
        message_data = payload.get("messageData", {})
        type_message = message_data.get("typeMessage", "")
        text = ""

        if type_message == "textMessage":
            text = message_data.get("textMessageData", {}).get("textMessage", "")
        elif type_message == "extendedTextMessage":
            text = message_data.get("extendedTextMessageData", {}).get("text", "")
        elif type_message == "audioMessage":
            file_data = message_data.get("fileMessageData") or message_data.get(
                "audioMessageData", {}
            )
            id_message = payload.get("idMessage", "")
            caption = (file_data.get("caption") or "").strip()

            if caption:
                text = caption
            elif file_data.get("downloadUrl") or id_message:
                log.info(f"[AUDIO] Vocal recu de {chat_id}, transcription en arriere-plan")
                background_tasks.add_task(
                    process_whatsapp_voice, chat_id, id_message, file_data
                )
                return {"status": "processing", "detail": "voice_transcription"}
            else:
                send_message(
                    "Message vocal recu, mais je ne peux pas le telecharger. Reessayez.",
                    phone=chat_id,
                )
                return {"status": "ignored", "reason": "audio_missing_url"}

        if not text:
            log.info(f"[SKIP] Pas de texte (typeMessage={type_message})")
            return {"status": "ignored", "reason": "no_text"}

        # 4. Traiter les commandes speciales du proprietaire (Owner)
        owner_id = get_owner_chat_id()
        if chat_id == owner_id:
            if text.startswith("/repondre "):
                try:
                    # Syntaxe attendue: /repondre [destinataire] : [message]
                    parts = text[len("/repondre "):].split(":", 1)
                    if len(parts) == 2:
                        target_client = parts[0].strip()
                        reply_content = parts[1].strip()
                        
                        if not target_client.endswith("@c.us"):
                            target_client = f"{target_client}@c.us"
                        
                        log.info(f"[OWNER CMD] Reponse manuelle transmise a {target_client} : {reply_content[:50]}...")
                        
                        # Envoyer la reponse au client
                        send_message(reply_content, phone=target_client)
                        # Confirmer au proprietaire
                        send_message(f"Reponse bien transmise au client {target_client.replace('@c.us', '')} !", phone=owner_id)
                        return {"status": "success", "detail": "reply_sent"}
                except Exception as ex:
                    log.error(f"Erreur lors de l'execution de /repondre : {ex}")
                    send_message(f"Erreur lors de l'envoi de la reponse : {ex}", phone=owner_id)
                    return {"status": "error", "reason": str(ex)}

        log.info(f"[MSG] {chat_id} -> {text[:100]}")

        # Traitement asynchrone en arriere-plan
        background_tasks.add_task(process_whatsapp_message, chat_id, text)
        return {"status": "processing"}

    except Exception as e:
        log.error(f"[ERROR] Webhook : {e}", exc_info=True)
        try:
            raw = await request.body()
            log.error(f"[RAW PAYLOAD] {raw.decode()}")
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


@app.get("/")
def home():
    return {"status": "running", "service": "AI Agent WhatsApp Webhook"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    log.info(f"Lancement du serveur Webhook sur le port {port}...")
    uvicorn.run("webhook:app", host="0.0.0.0", port=port, reload=True)
