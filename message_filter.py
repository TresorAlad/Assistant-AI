"""
message_filter.py - Filtrage sans appel IA (economie de tokens / quota)
"""

from __future__ import annotations

import re

# Messages ignorés sans réponse (0 token)
TRIVIAL_EXACT = frozenset({
    "ok", "okk", "okay", "ok.", "oui", "ouais", "nn", "non", "merci", "mrrc",
    "thanks", "thx", "cool", "nice", "vu", "bien recu", "recu", "c bon",
    "cb", "dac", "dacc", "👍", "🙏", "😂", "🤣", "😅", "lol", "mdr", "ptdr",
    "hm", "hmm", "ah", "oh", "hein", "?", "!", "...", "👌", "🙂",
})

SPAM_PATTERNS = re.compile(
    r"(gagnez|gratuit|bitcoin|crypto|casino|cliquez ici|bit\.ly|tinyurl)",
    re.I,
)

SUSPICIOUS_LINK = re.compile(r"https?://[^\s]+", re.I)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_empty_or_unsupported(text: str) -> bool:
    return not normalize(text)


def is_trivial(text: str) -> bool:
    t = normalize(text)
    if not t or len(t) <= 2:
        return True
    if t in TRIVIAL_EXACT:
        return True
    if len(t) <= 4 and not any(c.isalpha() for c in t if c.isalpha() and c not in "aoui"):
        return True
    # Uniquement emojis
    if re.fullmatch(r"[\U0001F300-\U0001FAFF\s]+", text.strip()):
        return True
    return False


def is_spam_or_suspicious(text: str) -> bool:
    if SPAM_PATTERNS.search(text):
        return True
    links = SUSPICIOUS_LINK.findall(text)
    if len(links) >= 2:
        return True
    return False


def should_skip_llm(text: str) -> tuple[bool, str | None]:
    """
    Retourne (skip, raison).
    skip=True -> ne pas appeler l'IA.
    """
    if is_empty_or_unsupported(text):
        return True, "empty"
    if is_spam_or_suspicious(text):
        return True, "spam"
    if is_trivial(text):
        return True, "trivial"
    return False, None
