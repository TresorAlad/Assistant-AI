"""
main.py — Point d'entrée de l'AI Personal Assistant

Usage :
    python main.py              → Exécute un cycle unique
    python main.py --schedule   → Lance le planificateur automatique
    python main.py --test       → Teste chaque module séparément
    python main.py --status     → Vérifie l'état des connexions
"""

import sys
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Import des modules
from gmail_reader import fetch_emails, authenticate_gmail
from ai_summary import generate_summary, generate_quick_summary
from whatsapp_sender import send_summary, send_message, check_connection
from scheduler import run_cycle, start_scheduler


BANNER = """
╔══════════════════════════════════════════════╗
║       🤖 AI PERSONAL ASSISTANT v1.0         ║
║                                              ║
║  📧 Gmail → 🤖 IA → 📱 WhatsApp            ║
╚══════════════════════════════════════════════╝
"""


def cmd_status():
    """Vérifie l'état de toutes les connexions."""
    print("\n🔍 Vérification des connexions...\n")

    # Gmail
    print("1️⃣  Gmail...")
    try:
        authenticate_gmail()
        print("   ✅ Gmail connecté\n")
    except FileNotFoundError:
        print("   ❌ credentials.json manquant\n")
    except Exception as e:
        print(f"   ❌ Gmail erreur: {e}\n")

    # OpenAI
    print("2️⃣  OpenAI...")
    key = os.getenv("OPENAI_API_KEY", "")
    if key and not key.startswith("sk-xxxx"):
        print(f"   ✅ Clé configurée ({key[:8]}...)\n")
    else:
        print("   ❌ Clé non configurée\n")

    # WhatsApp
    print("3️⃣  WhatsApp (Evolution API)...")
    try:
        check_connection()
    except ValueError as e:
        print(f"   ❌ {e}\n")


def cmd_test():
    """Teste chaque module avec des données factices."""
    print("\n🧪 Mode test\n")

    test_emails = [
        {
            "from": "Test <test@example.com>",
            "subject": "Email de test",
            "date": datetime.now().strftime("%d/%m/%Y à %H:%M"),
            "snippet": "Ceci est un email de test.",
            "body": "Ceci est le contenu complet de l'email de test.",
        }
    ]

    print("--- Résumé rapide (sans IA) ---")
    quick = generate_quick_summary(test_emails)
    print(quick)

    print("\n--- Résumé IA ---")
    try:
        ai = generate_summary(test_emails)
        print(ai)
    except Exception as e:
        print(f"⚠️ IA indisponible: {e}")

    print("\n--- Envoi WhatsApp test ---")
    try:
        send_message("🧪 Test AI Assistant — tout fonctionne !")
    except Exception as e:
        print(f"⚠️ WhatsApp indisponible: {e}")


def cmd_run_once():
    """Exécute un cycle unique."""
    print("\n🚀 Exécution d'un cycle unique...")
    run_cycle()


def main():
    print(BANNER)

    if len(sys.argv) < 2:
        cmd_run_once()
        return

    cmd = sys.argv[1].lower()

    if cmd in ("--schedule", "-s"):
        start_scheduler()
    elif cmd in ("--test", "-t"):
        cmd_test()
    elif cmd in ("--status", "-st"):
        cmd_status()
    elif cmd in ("--help", "-h"):
        print(__doc__)
    else:
        print(f"❌ Commande inconnue: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
