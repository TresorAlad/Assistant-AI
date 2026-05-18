"""
ai_summary.py — Module d'analyse IA des emails

Ce module gère :
- L'analyse des emails récupérés via OpenAI
- La détection des informations importantes
- La classification par priorité
- La génération d'un résumé structuré prêt pour WhatsApp
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Modèle à utiliser
MODEL = "gpt-4o-mini"

# Prompt système pour l'analyse des emails
SYSTEM_PROMPT = """Tu es un assistant personnel intelligent spécialisé dans l'analyse d'emails.

Ton rôle est de :
1. Analyser une liste d'emails reçus
2. Détecter les informations importantes et urgentes
3. Classer chaque email par priorité (🔴 Urgent, 🟡 Important, 🟢 Normal, ⚪ Ignorable)
4. Produire un résumé clair et concis en français

Format de réponse OBLIGATOIRE :

📧 *Résumé de vos emails* — {date}

📊 *Vue d'ensemble :*
• {nombre} emails analysés
• {catégorisation rapide}

⚠️ *Priorités :*
{liste des actions prioritaires avec emoji de priorité}

📋 *Détail par email :*
{pour chaque email : emoji priorité + expéditeur + résumé en 1-2 lignes}

💡 *Recommandations :*
{actions suggérées}

Règles :
- Sois concis mais complet
- Utilise des emojis pour la lisibilité
- Mets en gras les éléments importants avec *texte*
- Détecte les opportunités (freelance, emploi, collaboration)
- Signale les deadlines et dates limites
- Ignore les newsletters et spams évidents
- Écris toujours en français
"""


def _format_emails_for_analysis(emails: list) -> str:
    """
    Formate la liste des emails en texte structuré pour l'envoi à l'IA.

    Args:
        emails: Liste de dictionnaires contenant les données des emails

    Returns:
        Texte formaté prêt pour l'analyse
    """
    if not emails:
        return "Aucun email à analyser."

    formatted = f"Date du rapport : {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n"
    formatted += f"Nombre d'emails : {len(emails)}\n"
    formatted += "=" * 40 + "\n\n"

    for i, email in enumerate(emails, 1):
        formatted += f"--- Email {i}/{len(emails)} ---\n"
        formatted += f"De : {email.get('from', 'Inconnu')}\n"
        formatted += f"Sujet : {email.get('subject', 'Sans sujet')}\n"
        formatted += f"Date : {email.get('date', 'Inconnue')}\n"
        formatted += f"Contenu : {email.get('body', email.get('snippet', 'Vide'))}\n\n"

    return formatted


def generate_summary(emails: list) -> str:
    """
    Génère un résumé intelligent des emails via OpenAI.

    Args:
        emails: Liste de dictionnaires contenant les données des emails

    Returns:
        Résumé formaté prêt pour WhatsApp
    """
    if not emails:
        now = datetime.now().strftime("%d/%m/%Y à %H:%M")
        return (
            f"📧 *Résumé de vos emails* — {now}\n\n"
            "📭 Aucun nouvel email à signaler.\n\n"
            "✅ Votre boîte de réception est à jour !"
        )

    # Vérifier la clé API
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-xxxx"):
        raise ValueError(
            "❌ Clé OpenAI non configurée !\n"
            "   Ajoutez votre clé dans le fichier .env :\n"
            "   OPENAI_API_KEY=sk-votre-cle-ici"
        )

    # Formater les emails pour l'analyse
    email_text = _format_emails_for_analysis(emails)

    print(f"\n🤖 Analyse IA de {len(emails)} email(s) en cours...")

    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Analyse les {len(emails)} emails suivants et produis un résumé "
                        f"structuré avec priorités et recommandations :\n\n{email_text}"
                    ),
                },
            ],
            temperature=0.3,  # Bas pour des résumés factuels et cohérents
            max_tokens=1500,
        )

        summary = response.choices[0].message.content.strip()
        print("✅ Résumé IA généré avec succès.")
        return summary

    except Exception as e:
        print(f"❌ Erreur lors de l'analyse IA : {e}")
        raise


def generate_quick_summary(emails: list) -> str:
    """
    Génère un résumé rapide sans IA (fallback).
    Utile si l'API OpenAI est indisponible.

    Args:
        emails: Liste de dictionnaires contenant les données des emails

    Returns:
        Résumé basique formaté
    """
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")

    if not emails:
        return f"📧 Résumé — {now}\n\n📭 Aucun nouvel email."

    lines = [f"📧 *Résumé rapide* — {now}\n"]
    lines.append(f"📊 {len(emails)} email(s) reçu(s)\n")

    for i, email in enumerate(emails, 1):
        sender = email.get("from", "Inconnu")
        subject = email.get("subject", "Sans sujet")
        # Nettoyer le nom de l'expéditeur
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')
        lines.append(f"  {i}. *{sender}*\n     📋 {subject}")

    lines.append("\n💡 Consultez votre boîte mail pour les détails.")
    return "\n".join(lines)


# ============================================
# Test autonome du module
# ============================================
if __name__ == "__main__":
    print("=" * 50)
    print("🧪 Test du module AI Summary")
    print("=" * 50)

    # Données de test
    test_emails = [
        {
            "from": "Jean Dupont <jean@example.com>",
            "subject": "Opportunité freelance - Projet Python",
            "date": "17/05/2026 à 10:00",
            "snippet": "Bonjour, nous cherchons un développeur Python pour un projet de 3 mois...",
            "body": "Bonjour, nous cherchons un développeur Python pour un projet de 3 mois. Budget: 5000€. Répondez avant vendredi.",
        },
        {
            "from": "LinkedIn <notifications@linkedin.com>",
            "subject": "Marie Martin a consulté votre profil",
            "date": "17/05/2026 à 09:30",
            "snippet": "Marie Martin, Recruteuse chez TechCorp, a consulté votre profil...",
            "body": "Marie Martin, Recruteuse chez TechCorp, a consulté votre profil LinkedIn.",
        },
        {
            "from": "Service des impôts <impots@gouv.fr>",
            "subject": "Rappel : Déclaration de revenus",
            "date": "17/05/2026 à 08:00",
            "snippet": "Rappel : la date limite de votre déclaration est le 25 mai...",
            "body": "Rappel : la date limite pour votre déclaration de revenus 2025 est le 25 mai 2026. Connectez-vous sur impots.gouv.fr.",
        },
    ]

    # Test résumé rapide (sans IA)
    print("\n--- Résumé rapide (sans IA) ---")
    print(generate_quick_summary(test_emails))

    # Test résumé IA (nécessite clé API valide)
    print("\n--- Résumé IA ---")
    try:
        print(generate_summary(test_emails))
    except ValueError as e:
        print(e)
    except Exception as e:
        print(f"❌ Erreur : {e}")
