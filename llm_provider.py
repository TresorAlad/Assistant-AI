"""
llm_provider.py - Fournisseurs IA avec fallback chain et economie de quota
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Protocol

import requests
from dotenv import load_dotenv

from quota_manager import record_llm_call, set_global_cooldown

load_dotenv()

log = logging.getLogger("llm_provider")

AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")

MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "280"))
MAX_HISTORY = int(os.getenv("LLM_MAX_HISTORY", "6"))

JSON_SCHEMA_HINT = """
JSON strict uniquement :
{"type":"reply|escalation|action|skip","message":"...","service":null,"task":null,"data":{}}
"""


class AgentQuotaError(Exception):
    def __init__(self, provider: str, retry_after: int | None = None):
        self.provider = provider
        self.retry_after = retry_after
        wait = retry_after if retry_after and retry_after > 0 else 60
        delay = f"{wait} secondes" if wait < 120 else f"{max(1, round(wait / 60))} min"
        self.user_message = (
            f"⏳ Quota IA ({provider}) atteint — reessayez dans ~{delay}.\n"
            f"En attendant votre message est note pour {os.getenv('OWNER_NAME', 'le proprietaire')}."
        )
        super().__init__(self.user_message)


class AllProvidersExhausted(Exception):
    """Tous les fournisseurs ont echoue (quota ou indisponible)."""

    def __init__(self):
        super().__init__("all_providers_exhausted")


class ChatSession(Protocol):
    def send_message(self, user_text: str, chat_id: str = "") -> str: ...


def _parse_retry_seconds(error: Exception) -> int | None:
    text = str(error)
    match = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", text, re.I)
    if match:
        return max(1, int(float(match.group(1))))
    match = re.search(r'"retryDelay":\s*"(\d+)s"', text)
    if match:
        return int(match.group(1))
    return None


def _is_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "429" in text
        or "resource_exhausted" in text
        or "rate limit" in text
        or "quota" in text
        or "too many requests" in text
        or "insufficient" in text
    )


def _trim_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= MAX_HISTORY + 1:
        return messages
    system = messages[0:1]
    rest = messages[1:]
    return system + rest[-MAX_HISTORY:]


class MultiProviderChatSession:
    """Session avec historique limite et chaine de fallback."""

    def __init__(self, system_instruction: str, response_schema: dict | None = None):
        self._system = system_instruction + "\n" + JSON_SCHEMA_HINT
        self._messages: list[dict[str, str]] = [{"role": "system", "content": self._system}]
        self._schema = response_schema
        self._providers = self._build_provider_chain()

    def _build_provider_chain(self) -> list[tuple[str, str]]:
        chain: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(p: str, m: str) -> None:
            key = (p, m)
            if key not in seen:
                seen.add(key)
                chain.append(key)

        if GROQ_API_KEY:
            add("groq", GROQ_MODEL)
            if GROQ_FALLBACK_MODEL != GROQ_MODEL:
                add("groq", GROQ_FALLBACK_MODEL)
        if GEMINI_API_KEY:
            add("gemini", GEMINI_MODEL)
        if OLLAMA_URL:
            add("ollama", OLLAMA_MODEL)
        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            chain = [x for x in chain if x[0] == "gemini"] + [x for x in chain if x[0] != "gemini"]

        return chain

    def send_message(self, user_text: str, chat_id: str = "") -> str:
        self._messages.append({"role": "user", "content": user_text})
        errors: list[str] = []

        for provider, model in self._providers:
            try:
                content = self._call_provider(provider, model, chat_id)
                self._messages.append({"role": "assistant", "content": content})
                record_llm_call(chat_id)
                return content
            except AgentQuotaError as e:
                errors.append(f"{provider}:{e.provider}")
                set_global_cooldown(e.retry_after or 120)
                log.warning("[LLM] Quota %s/%s, essai suivant...", provider, model)
                continue
            except Exception as e:
                errors.append(f"{provider}:{e}")
                log.warning("[LLM] Echec %s/%s : %s", provider, model, e)
                continue

        log.error("[LLM] Tous providers echoues : %s", errors)
        raise AllProvidersExhausted()

    def _call_provider(self, provider: str, model: str, chat_id: str) -> str:
        if provider == "groq":
            return self._call_groq(model)
        if provider == "gemini":
            return self._call_gemini(model)
        if provider == "ollama":
            return self._call_ollama(model)
        raise ValueError(f"Provider inconnu: {provider}")

    def _call_groq(self, model: str) -> str:
        from groq import Groq

        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY manquante")
        client = Groq(api_key=GROQ_API_KEY)
        messages = _trim_history(self._messages)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.35,
                max_tokens=MAX_TOKENS,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            if _is_quota_error(e):
                raise AgentQuotaError("Groq", _parse_retry_seconds(e)) from e
            raise

    def _call_gemini(self, model: str) -> str:
        from google import genai
        from google.genai import types

        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY manquante")
        client = genai.Client(api_key=GEMINI_API_KEY)
        user_parts = [m["content"] for m in self._messages if m["role"] == "user"]
        last_user = user_parts[-1] if user_parts else ""
        try:
            response = client.models.generate_content(
                model=model,
                contents=last_user,
                config=types.GenerateContentConfig(
                    system_instruction=self._system,
                    response_mime_type="application/json",
                    temperature=0.35,
                    max_output_tokens=MAX_TOKENS,
                ),
            )
            return (response.text or "").strip()
        except Exception as e:
            if _is_quota_error(e):
                raise AgentQuotaError("Gemini", _parse_retry_seconds(e)) from e
            raise

    def _call_ollama(self, model: str) -> str:
        if not OLLAMA_URL:
            raise ValueError("OLLAMA_URL manquante")
        messages = _trim_history(self._messages)
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False, "format": "json"},
            timeout=90,
        )
        resp.raise_for_status()
        content = (resp.json().get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("Ollama reponse vide")
        return content


def resolve_provider() -> str:
    if OLLAMA_URL:
        return "ollama"
    if GROQ_API_KEY:
        return "groq"
    if GEMINI_API_KEY:
        return "gemini"
    return "none"


def create_chat_session(system_instruction: str, response_schema: dict | None = None) -> ChatSession:
    chain = MultiProviderChatSession(system_instruction, response_schema)._providers
    if not chain:
        raise ValueError(
            "Aucune IA configuree. Ajoutez GROQ_API_KEY, GEMINI_API_KEY ou OLLAMA_URL dans .env"
        )
    log.info("[LLM] Chaine: %s", " -> ".join(f"{p}/{m}" for p, m in chain))
    return MultiProviderChatSession(system_instruction, response_schema)


def transcribe_audio_groq(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    import io

    from groq import Groq

    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY manquante")
    client = Groq(api_key=GROQ_API_KEY)
    try:
        transcription = client.audio.transcriptions.create(
            file=(filename, io.BytesIO(audio_bytes)),
            model="whisper-large-v3",
            language="fr",
            response_format="text",
        )
        text = transcription if isinstance(transcription, str) else getattr(transcription, "text", "")
        record_llm_call()
        return (text or "").strip() or "[inaudible]"
    except Exception as e:
        if _is_quota_error(e):
            raise AgentQuotaError("Groq Whisper", _parse_retry_seconds(e)) from e
        raise


def transcribe_audio_ollama(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Secours local si Ollama avec modele whisper disponible — sinon leve."""
    raise NotImplementedError("Transcription Ollama non configuree — utilisez le texte.")


def can_transcribe_voice() -> bool:
    from quota_manager import can_call_llm, get_stats

    ok, _ = can_call_llm("")
    return ok and get_stats()["daily_remaining"] > 3
