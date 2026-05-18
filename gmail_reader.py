"""
gmail_reader.py — Module de lecture des emails Gmail

Ce module gère :
- L'authentification OAuth2 avec Gmail
- La récupération des derniers emails non lus
- L'extraction des données (expéditeur, sujet, snippet, date)
- La gestion des doublons via un fichier de suivi
"""

import os
import json
import base64
from datetime import datetime
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Portée d'accès : lecture et envoi d'emails
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send"
]

# Fichier de suivi des emails déjà traités
PROCESSED_IDS_FILE = os.path.join(os.path.dirname(__file__), "processed_emails.json")


def _load_processed_ids() -> set:
    """Charge les IDs des emails déjà traités pour éviter les doublons."""
    if os.path.exists(PROCESSED_IDS_FILE):
        try:
            with open(PROCESSED_IDS_FILE, "r") as f:
                data = json.load(f)
                return set(data.get("ids", []))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()


def _save_processed_ids(ids: set) -> None:
    """Sauvegarde les IDs des emails traités."""
    # Garder seulement les 500 derniers IDs pour ne pas surcharger
    ids_list = list(ids)[-500:]
    with open(PROCESSED_IDS_FILE, "w") as f:
        json.dump({"ids": ids_list, "updated_at": datetime.now().isoformat()}, f, indent=2)


def authenticate_gmail():
    """
    Authentifie l'utilisateur avec Gmail via OAuth2.

    Utilise credentials.json pour la première connexion,
    puis sauvegarde le token dans token.json pour les connexions suivantes.

    Returns:
        service: Objet Gmail API prêt à l'emploi
    """
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), "token.json")
    credentials_path = os.path.join(os.path.dirname(__file__), "credentials.json")

    # Vérifier si un token existe déjà
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Si pas de credentials valides, en obtenir de nouveaux
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Rafraîchissement du token Gmail...")
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    "❌ Fichier 'credentials.json' introuvable !\n"
                    "   Téléchargez-le depuis la Google Cloud Console :\n"
                    "   https://console.cloud.google.com/apis/credentials"
                )
            print("🔐 Première connexion — ouverture du navigateur pour autorisation...")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Sauvegarder le token pour les prochaines utilisations
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())
            print("✅ Token Gmail sauvegardé.")

    service = build("gmail", "v1", credentials=creds)
    return service


def _extract_header(headers: list, name: str) -> str:
    """Extrait la valeur d'un header spécifique d'un email."""
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def _parse_email_date(date_str: str) -> str:
    """Parse la date d'un email et la formate de manière lisible."""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        return date_str


def _get_email_body(payload: dict) -> str:
    """
    Extrait le corps textuel d'un email.
    Gère les emails simples et multipart.
    """
    body = ""

    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part.get("body", {}):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break
            elif part["mimeType"] == "text/html" and "data" in part.get("body", {}):
                # Fallback sur HTML si pas de texte brut
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            elif "parts" in part:
                # Gestion récursive des multipart imbriqués
                body = _get_email_body(part)
                if body:
                    break
    elif "body" in payload and "data" in payload["body"]:
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return body.strip()


def fetch_emails(max_results: int = 10, labels: list = None) -> list:
    """
    Récupère les derniers emails non lus depuis Gmail.

    Args:
        max_results: Nombre maximum d'emails à récupérer
        labels: Labels Gmail à surveiller (par défaut: INBOX)

    Returns:
        Liste de dictionnaires contenant les données des emails
    """
    if labels is None:
        labels = ["INBOX"]

    service = authenticate_gmail()
    processed_ids = _load_processed_ids()

    print(f"\n📬 Récupération des emails (max: {max_results})...")

    try:
        # Récupérer la liste des messages non lus
        results = service.users().messages().list(
            userId="me",
            labelIds=labels,
            q="is:unread",
            maxResults=max_results
        ).execute()

        messages = results.get("messages", [])

        if not messages:
            print("📭 Aucun nouvel email non lu.")
            return []

        emails = []
        new_ids = set()

        for msg_info in messages:
            msg_id = msg_info["id"]

            # Éviter les doublons
            if msg_id in processed_ids:
                continue

            # Récupérer les détails du message
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full"
            ).execute()

            headers = msg.get("payload", {}).get("headers", [])
            snippet = msg.get("snippet", "")

            # Extraire le corps complet (limité à 1000 caractères pour l'IA)
            body = _get_email_body(msg.get("payload", {}))
            if len(body) > 1000:
                body = body[:1000] + "..."

            email_data = {
                "id": msg_id,
                "from": _extract_header(headers, "From"),
                "subject": _extract_header(headers, "Subject"),
                "date": _parse_email_date(_extract_header(headers, "Date")),
                "snippet": snippet,
                "body": body if body else snippet,
                "labels": msg.get("labelIds", []),
            }

            emails.append(email_data)
            new_ids.add(msg_id)

        # Sauvegarder les IDs traités
        processed_ids.update(new_ids)
        _save_processed_ids(processed_ids)

        print(f"✅ {len(emails)} nouveau(x) email(s) récupéré(s).")
        return emails

    except Exception as e:
        print(f"❌ Erreur lors de la récupération des emails : {e}")
        raise


def mark_as_read(msg_id: str) -> None:
    """
    Marque un email comme lu (optionnel — pour usage futur).

    Args:
        msg_id: ID du message Gmail
    """
    service = authenticate_gmail()
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def send_email(to: str, subject: str, body: str) -> bool:
    """
    Envoie un email via l'API Gmail.

    Args:
        to: Adresse email du destinataire.
        subject: Sujet de l'email.
        body: Contenu de l'email.

    Returns:
        True si l'envoi a réussi, False sinon.
    """
    from email.message import EmailMessage

    service = authenticate_gmail()
    
    message = EmailMessage()
    message.set_content(body)
    message["To"] = to
    message["Subject"] = subject

    # Encodage en base64 pour Gmail API
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    create_message = {"raw": encoded_message}

    try:
        print(f"\n📧 Envoi d'email à {to}...")
        service.users().messages().send(userId="me", body=create_message).execute()
        print(f"✅ Email envoyé avec succès à {to}.")
        return True
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi de l'email : {e}")
        return False


# ============================================
# Test autonome du module
# ============================================
if __name__ == "__main__":
    print("=" * 50)
    print("🧪 Test du module Gmail Reader")
    print("=" * 50)

    try:
        emails = fetch_emails(max_results=5)
        for i, email in enumerate(emails, 1):
            print(f"\n--- Email {i} ---")
            print(f"  📤 De      : {email['from']}")
            print(f"  📋 Sujet   : {email['subject']}")
            print(f"  📅 Date    : {email['date']}")
            print(f"  📝 Aperçu  : {email['snippet'][:100]}...")
    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"❌ Erreur : {e}")
