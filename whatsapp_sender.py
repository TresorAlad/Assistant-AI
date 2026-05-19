"""
whatsapp_sender.py - Module d'envoi WhatsApp via Evolution API
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8085").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
WHATSAPP_INSTANCE = os.getenv("WHATSAPP_INSTANCE", "assistant")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")


def _validate_config():
    missing = []
    for var_name, var_val in [
        ("EVOLUTION_API_URL", EVOLUTION_API_URL),
        ("EVOLUTION_API_KEY", EVOLUTION_API_KEY),
        ("WHATSAPP_INSTANCE", WHATSAPP_INSTANCE),
        ("PHONE_NUMBER", PHONE_NUMBER),
    ]:
        if not var_val:
            missing.append(var_name)
    if missing:
        raise ValueError(f"Config WhatsApp Evolution API incomplete. Manque: {', '.join(missing)}")


def _format_phone(phone: str) -> str:
    """
    Formate un numero de telephone au format attendu par Evolution API.
    L'API Evolution attend le numero brut avec code pays (ex: 22897050981).
    """
    phone = phone.strip()
    # Nettoyer les suffixes et prefixes
    cleaned = phone.replace("@c.us", "").replace("@s.whatsapp.net", "")
    cleaned = cleaned.replace(" ", "").replace("-", "").replace("+", "")
    return cleaned


def check_connection() -> bool:
    _validate_config()
    try:
        url = f"{EVOLUTION_API_URL}/instance/connectionState/{WHATSAPP_INSTANCE}"
        headers = {"apikey": EVOLUTION_API_KEY}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        state = resp.json().get("instance", {}).get("state", "")
        if state in ("open", "connected"):
            print("WhatsApp connecte (Evolution API).")
            return True
        print(f"WhatsApp non connecte. Etat: {state}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Erreur connexion Evolution API : {e}")
        return False


def send_message(message: str, phone: str = None) -> bool:
    _validate_config()
    target = phone or PHONE_NUMBER
    cleaned_phone = _format_phone(target)

    if len(message) > 4000:
        message = message[:3950] + "\n\n[Message tronque]"

    print(f"[SEND] -> phone={cleaned_phone} | msg={message[:60]}...")
    try:
        url = f"{EVOLUTION_API_URL}/message/sendText/{WHATSAPP_INSTANCE}"
        headers = {
            "apikey": EVOLUTION_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {"number": cleaned_phone, "text": message}
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        print(f"[SEND OK]")
        return True
    except requests.exceptions.Timeout:
        print("Erreur: Timeout - Evolution API ne repond pas.")
    except requests.exceptions.ConnectionError:
        print(f"Erreur: Impossible de se connecter a {EVOLUTION_API_URL}")
    except requests.exceptions.HTTPError as e:
        print(f"Erreur HTTP {e.response.status_code}: {e.response.text}")
    except Exception as e:
        print(f"Erreur inattendue send_message: {e}")
    return False


def send_summary(summary: str) -> bool:
    header = "*AI Personal Assistant*\n" + "-" * 25 + "\n\n"
    footer = "\n\n" + "-" * 25 + "\n_Prochain resume bientot_"
    return send_message(header + summary + footer)


if __name__ == "__main__":
    print("Test WhatsApp Sender (Evolution API)")
    try:
        _validate_config()
        if check_connection():
            send_message("Test AI Assistant - Evolution API config OK !")
    except ValueError as e:
        print(e)
