"""
webhook.py - Serveur FastAPI pour recevoir les messages WhatsApp via Evolution API
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

app = FastAPI(title="AI Agent WhatsApp Webhook (Evolution API)")

# Memoire en RAM : { "chatId": session_gemini }
user_sessions = {}

# Ensemble des chatId deja en cours de traitement (anti boucle infinie)
processing_set = set()

# Le chatId du proprietaire de l'assistant (qui a les privileges)
OWNER_PHONE = os.getenv("OWNER_PHONE", "")


def get_owner_chat_id() -> str:
    """Retourne le chatId du proprietaire pour detecter ses messages."""
    phone = OWNER_PHONE.strip().replace("+", "").replace(" ", "")
    if "@s.whatsapp.net" not in phone:
        phone = f"{phone}@s.whatsapp.net"
    return phone


def _run_agent_for_chat(chat_id: str, text: str):
    """Execute l'agent et envoie la reponse."""
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
async def evolution_api_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint appele par Evolution API a chaque evenement.
    """
    try:
        payload = await request.json()
        event_type = payload.get("event", "")

        # Logguer le type d'evenement pour le debug
        log.info(f"[WEBHOOK] event={event_type}")

        # On traite uniquement les messages.upsert
        if event_type != "messages.upsert":
            return {"status": "ignored", "reason": f"unhandled_event_{event_type}"}

        data = payload.get("data", {})
        if not data:
            return {"status": "ignored", "reason": "empty_data"}

        key = data.get("key", {})
        chat_id = key.get("remoteJid", "")
        message_id = key.get("id", "")
        from_me = key.get("fromMe", False)

        # 1. Ignorer les messages sortants (sauf si c'est une discussion avec soi-meme)
        if from_me:
            # On ignore pour eviter de repondre a nos propres messages
            log.info(f"[SKIP] Message sortant (fromMe=True), ignore.")
            return {"status": "ignored", "reason": "bot_message"}

        # 2. Ignorer les statuts
        if "status@broadcast" in chat_id:
            return {"status": "ignored", "reason": "status"}

        # 3. Ignorer STRICTEMENT les groupes
        if chat_id.endswith("@g.us"):
            log.info(f"[SKIP] Message de groupe ({chat_id}) ignore.")
            return {"status": "ignored", "reason": "group_message"}

        # Extraire le message
        message = data.get("message", {})
        if not message:
            return {"status": "ignored", "reason": "no_message_content"}

        message_type = data.get("messageType", "")
        text = ""

        # Recuperer le texte selon le type de message
        if "conversation" in message:
            text = message.get("conversation", "")
        elif "extendedTextMessage" in message:
            text = message.get("extendedTextMessage", {}).get("text", "")
        elif "audioMessage" in message:
            audio_data = message.get("audioMessage", {})
            file_data = {
                "mimeType": audio_data.get("mimetype", "audio/ogg"),
            }
            log.info(f"[AUDIO] Vocal recu de {chat_id}, transcription en arriere-plan")
            background_tasks.add_task(
                process_whatsapp_voice, chat_id, message_id, file_data
            )
            return {"status": "processing", "detail": "voice_transcription"}

        if not text:
            log.info(f"[SKIP] Pas de texte ou type de message non supporte (messageType={message_type})")
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
                        
                        if not target_client.endswith("@s.whatsapp.net"):
                            target_client = f"{target_client}@s.whatsapp.net"
                        
                        log.info(f"[OWNER CMD] Reponse manuelle transmise a {target_client} : {reply_content[:50]}...")
                        
                        # Envoyer la reponse au client
                        send_message(reply_content, phone=target_client)
                        # Confirmer au proprietaire
                        display_phone = target_client.replace('@s.whatsapp.net', '')
                        send_message(f"Reponse bien transmise au client {display_phone} !", phone=owner_id)
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
        return {"status": "error", "message": str(e)}


@app.get("/")
def home():
    return {"status": "running", "service": "AI Agent WhatsApp Webhook (Evolution API)"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    log.info(f"Lancement du serveur Webhook sur le port {port}...")
    uvicorn.run("webhook:app", host="0.0.0.0", port=port, reload=True)
