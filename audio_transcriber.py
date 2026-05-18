"""
audio_transcriber.py - Telechargement et transcription de vocaux WhatsApp (francais)
"""

import logging
import os
import re

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

log = logging.getLogger("audio_transcriber")

GREEN_API_URL = os.getenv("GREEN_API_URL", "https://api.green-api.com").rstrip("/")
ID_INSTANCE = os.getenv("ID_INSTANCE", "")
API_TOKEN_INSTANCE = os.getenv("API_TOKEN_INSTANCE", "")

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


def _fetch_bytes_from_url(url: str, timeout: int = 60) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("Fichier audio vide.")
    return response.content


def _download_via_green_api(chat_id: str, id_message: str) -> str:
    if not ID_INSTANCE or not API_TOKEN_INSTANCE:
        raise ValueError("ID_INSTANCE ou API_TOKEN_INSTANCE manquant.")

    url = f"{GREEN_API_URL}/waInstance{ID_INSTANCE}/downloadFile/{API_TOKEN_INSTANCE}"
    response = requests.post(
        url,
        json={"chatId": chat_id, "idMessage": id_message},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    download_url = payload.get("downloadUrl", "")
    if not download_url:
        raise ValueError("Green API n'a pas retourne de downloadUrl.")
    return download_url


def download_voice_file(
    chat_id: str,
    id_message: str,
    file_data: dict,
) -> tuple[bytes, str]:
    """
    Telecharge un vocal WhatsApp via l'URL du webhook ou l'API downloadFile.
    Retourne (bytes audio, mime_type normalise).
    """
    mime_type = normalize_mime_type(file_data.get("mimeType", "audio/ogg"))
    download_url = (file_data.get("downloadUrl") or "").strip()

    if download_url:
        try:
            log.info("[AUDIO] Telechargement direct : %s", download_url[:80])
            return _fetch_bytes_from_url(download_url), mime_type
        except Exception as exc:
            log.warning("[AUDIO] Echec telechargement direct : %s", exc)

    if chat_id and id_message:
        log.info("[AUDIO] Fallback downloadFile pour idMessage=%s", id_message[:16])
        resolved_url = _download_via_green_api(chat_id, id_message)
        return _fetch_bytes_from_url(resolved_url), mime_type

    raise ValueError("Impossible de telecharger le fichier audio (URL et idMessage absents).")


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
