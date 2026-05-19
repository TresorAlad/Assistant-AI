"""
audio_transcriber.py - Telechargement et transcription de vocaux WhatsApp (francais) via Evolution API
"""

import logging
import os
import re
import base64
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

log = logging.getLogger("audio_transcriber")

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8085").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
WHATSAPP_INSTANCE = os.getenv("WHATSAPP_INSTANCE", "assistant")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "gemini-2.5-flash")

_client: genai.Client | None = None

MIME_ALIASES = {
    "audio/mpga": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
}

TRANSCRIPTION_PROMPT = """Transcris ce message vocal WhatsApp en francais.

Regles strictes :
- Retourne UNIQUEMENT le texte parle, sans guillemets ni commentaire
- Conserve la ponctuation naturelle
- Si le message est vide, inaudible ou dans une autre langue non comprise, retourne exactement : [inaudible]
"""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY manquante pour la transcription audio.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def normalize_mime_type(mime_type: str) -> str:
    """Normalise le MIME pour Gemini (ex. audio/ogg; codecs=opus -> audio/ogg)."""
    if not mime_type:
        return "audio/ogg"
    base = mime_type.split(";")[0].strip().lower()
    return MIME_ALIASES.get(base, base)


def _download_via_evolution_api(message_id: str) -> bytes:
    if not EVOLUTION_API_KEY or not WHATSAPP_INSTANCE:
        raise ValueError("EVOLUTION_API_KEY ou WHATSAPP_INSTANCE manquant.")

    url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{WHATSAPP_INSTANCE}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "message": {
            "key": {
                "id": message_id
            }
        }
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    base64_data = data.get("base64", "")
    if not base64_data:
        raise ValueError("Evolution API n'a pas retourne de base64 pour ce fichier audio.")
    
    return base64.b64decode(base64_data)


def download_voice_file(
    chat_id: str,
    id_message: str,
    file_data: dict,
) -> tuple[bytes, str]:
    """
    Telecharge un vocal WhatsApp via l'API Evolution.
    Retourne (bytes audio, mime_type normalise).
    """
    mime_type = normalize_mime_type(file_data.get("mimeType", "audio/ogg"))

    if id_message:
        log.info("[AUDIO] Telechargement via Evolution API pour idMessage=%s", id_message)
        audio_bytes = _download_via_evolution_api(id_message)
        return audio_bytes, mime_type

    raise ValueError("Impossible de telecharger le fichier audio (id_message absent).")


def transcribe_french_audio(audio_bytes: bytes, mime_type: str) -> str:
    """Transcrit un vocal en francais via Gemini."""
    if not audio_bytes:
        raise ValueError("Donnees audio vides.")

    client = _get_client()
    mime_type = normalize_mime_type(mime_type)

    response = client.models.generate_content(
        model=TRANSCRIPTION_MODEL,
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            types.Part.from_text(text=TRANSCRIPTION_PROMPT),
        ],
        config=types.GenerateContentConfig(temperature=0.1),
    )

    text = (response.text or "").strip()
    text = re.sub(r"^[\"']|[\"']$", "", text).strip()

    if not text:
        return "[inaudible]"

    return text


def transcribe_whatsapp_voice(
    chat_id: str,
    id_message: str,
    file_data: dict,
) -> str:
    """Telecharge puis transcrit un vocal WhatsApp. Retourne le texte francais."""
    audio_bytes, mime_type = download_voice_file(chat_id, id_message, file_data)
    log.info("[AUDIO] Fichier telecharge (%d octets, %s)", len(audio_bytes), mime_type)
    transcription = transcribe_french_audio(audio_bytes, mime_type)
    log.info("[AUDIO] Transcription : %s", transcription[:120])
    return transcription
