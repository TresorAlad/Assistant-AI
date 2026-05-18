"""
whatsapp_sender.py - Module d'envoi WhatsApp via Green API
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

GREEN_API_URL = os.getenv("GREEN_API_URL", "https://api.green-api.com").rstrip("/")
ID_INSTANCE = os.getenv("ID_INSTANCE", "")
API_TOKEN_INSTANCE = os.getenv("API_TOKEN_INSTANCE", "")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")


def _validate_config():
    missing = []
    for var_name, var_val in [
        ("ID_INSTANCE", ID_INSTANCE),
        ("API_TOKEN_INSTANCE", API_TOKEN_INSTANCE),
        ("PHONE_NUMBER", PHONE_NUMBER),
    ]:
        if not var_val:
            missing.append(var_name)
    if missing:
        raise ValueError(f"Config WhatsApp incomplete. Manque: {', '.join(missing)}")


def _format_phone(phone: str) -> str:
    """
    Formate un numero de telephone au format chatId de Green API.
    Si le phone contient deja un '@', il est retourne tel quel.
    """
    phone = phone.strip()
    if "@" in phone:
        return phone
    cleaned = phone.replace(" ", "").replace("-", "").replace("+", "")
    return f"{cleaned}@c.us"


def check_connection() -> bool:
    _validate_config()
    try:
        url = f"{GREEN_API_URL}/waInstance{ID_INSTANCE}/getStateInstance/{API_TOKEN_INSTANCE}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        state = resp.json().get("stateInstance", "")
        if state == "authorized":
            print("WhatsApp connecte (Green API).")
            return True
        print(f"WhatsApp non connecte. Etat: {state}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Erreur connexion Green API : {e}")
        return False


def send_message(message: str, phone: str = None) -> bool:
    _validate_config()
    target = phone or PHONE_NUMBER
    chat_id = _format_phone(target)

    if len(message) > 4000:
        message = message[:3950] + "\n\n[Message tronque]"

    print(f"[SEND] -> chatId={chat_id} | msg={message[:60]}...")
    try:
        url = f"{GREEN_API_URL}/waInstance{ID_INSTANCE}/sendMessage/{API_TOKEN_INSTANCE}"
        payload = {"chatId": chat_id, "message": message}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        msg_id = resp.json().get("idMessage", "?")
        print(f"[SEND OK] idMessage={msg_id}")
        return True
    except requests.exceptions.Timeout:
        print("Erreur: Timeout - Green API ne repond pas.")
    except requests.exceptions.ConnectionError:
        print(f"Erreur: Impossible de se connecter a {GREEN_API_URL}")
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
    print("Test WhatsApp Sender")
    try:
        _validate_config()
        if check_connection():
            send_message("Test AI Assistant - config OK !")
    except ValueError as e:
        print(e)
