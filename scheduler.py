"""
scheduler.py — Module de planification automatique

Ce module gère :
- L'exécution automatique à intervalle régulier
- La planification quotidienne (matin/soir)
- La gestion du cycle complet (lecture → analyse → envoi)
"""

import os
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

# Import des modules du projet
from gmail_reader import fetch_emails
from ai_summary import generate_summary, generate_quick_summary
from whatsapp_sender import send_summary, check_connection


def run_cycle():
    """
    Exécute un cycle complet :
    1. Lecture des emails
    2. Analyse IA
    3. Envoi WhatsApp
    """
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"🔄 Cycle démarré — {now}")
    print(f"{'='*50}")

    max_emails = int(os.getenv("MAX_EMAILS", "10"))
    labels = os.getenv("GMAIL_LABELS", "INBOX").split(",")
    labels = [l.strip() for l in labels]

    try:
        # Étape 1 : Lecture des emails
        emails = fetch_emails(max_results=max_emails, labels=labels)

        if not emails:
            print("📭 Pas de nouveaux emails — cycle terminé.")
            return

        # Étape 2 : Analyse IA
        try:
            summary = generate_summary(emails)
        except Exception as e:
            print(f"⚠️ Erreur IA, utilisation du résumé rapide : {e}")
            summary = generate_quick_summary(emails)

        # Étape 3 : Envoi WhatsApp
        success = send_summary(summary)

        if success:
            print(f"\n✅ Cycle terminé avec succès — {len(emails)} email(s) traité(s)")
        else:
            print("\n⚠️ Cycle terminé mais envoi WhatsApp échoué")
            print(f"   Résumé généré :\n{summary}")

    except Exception as e:
        print(f"\n❌ Erreur durant le cycle : {e}")
        # En cas d'erreur critique, notifier si possible
        try:
            send_summary(f"❌ *Erreur AI Assistant*\n\n{str(e)[:500]}")
        except Exception:
            pass


def start_scheduler():
    """
    Démarre le planificateur selon la configuration .env.

    Modes disponibles :
    - interval : toutes les X minutes
    - daily_morning : chaque matin
    - daily_evening : chaque soir
    - custom : matin + soir
    """
    mode = os.getenv("SCHEDULE_MODE", "interval").lower()
    scheduler = BlockingScheduler()

    print("\n" + "=" * 50)
    print("⏰ AI Personal Assistant — Scheduler")
    print("=" * 50)

    if mode == "interval":
        minutes = int(os.getenv("SCHEDULE_INTERVAL_MINUTES", "30"))
        scheduler.add_job(run_cycle, IntervalTrigger(minutes=minutes),
                          id="email_cycle", name=f"Cycle toutes les {minutes} min")
        print(f"📅 Mode: Intervalle — toutes les {minutes} minutes")

    elif mode == "daily_morning":
        time_str = os.getenv("SCHEDULE_MORNING_TIME", "08:00")
        h, m = time_str.split(":")
        scheduler.add_job(run_cycle, CronTrigger(hour=int(h), minute=int(m)),
                          id="morning_cycle", name=f"Résumé du matin ({time_str})")
        print(f"📅 Mode: Matin — chaque jour à {time_str}")

    elif mode == "daily_evening":
        time_str = os.getenv("SCHEDULE_EVENING_TIME", "20:00")
        h, m = time_str.split(":")
        scheduler.add_job(run_cycle, CronTrigger(hour=int(h), minute=int(m)),
                          id="evening_cycle", name=f"Résumé du soir ({time_str})")
        print(f"📅 Mode: Soir — chaque jour à {time_str}")

    elif mode == "custom":
        morning = os.getenv("SCHEDULE_MORNING_TIME", "08:00")
        evening = os.getenv("SCHEDULE_EVENING_TIME", "20:00")
        hm, mm = morning.split(":")
        he, me = evening.split(":")
        scheduler.add_job(run_cycle, CronTrigger(hour=int(hm), minute=int(mm)),
                          id="morning", name=f"Matin ({morning})")
        scheduler.add_job(run_cycle, CronTrigger(hour=int(he), minute=int(me)),
                          id="evening", name=f"Soir ({evening})")
        print(f"📅 Mode: Custom — {morning} et {evening}")

    else:
        print(f"❌ Mode inconnu: {mode}")
        sys.exit(1)

    # Exécuter un premier cycle immédiatement
    print("\n🚀 Exécution du premier cycle...")
    run_cycle()

    print("\n⏳ Planificateur actif — Ctrl+C pour arrêter")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Planificateur arrêté.")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
