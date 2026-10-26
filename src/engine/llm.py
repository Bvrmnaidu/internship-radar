"""Thin LLM wrapper so the rest of the engine never touches a provider SDK directly
(and tests can swap in a fake).

Provider is picked from whichever key is set (first match wins):
  GEMINI_API_KEY     -> Google Gemini free tier (OpenAI-compatible endpoint)   [free]
  LLM_API_KEY + LLM_BASE_URL -> any OpenAI-compatible API (Groq, Mistral, ...)  [free tiers exist]
  ANTHROPIC_API_KEY  -> Claude                                                  [paid]
Override the model with LLM_MODEL (or CLAUDE_MODEL for Claude).
"""

import json, os, re, time

import requests

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
# Gemini's free tier allows only ~20 requests per DAY per model (confirmed from a 429 body,
# Oct 2026). Each model has its own quota, so screening (many calls) uses Flash-Lite and the
# few cover letters use Flash.
WRITER_DEFAULTS = {"gemini": "gemini-flash-latest"}
DEFAULTS = {
    "gemini":    "gemini-flash-lite-latest",   # screening model (own daily quota)
    "anthropic": "claude-haiku-5-5",
    "openai":    "",                   # must set LLM_MODEL for a custom endpoint
}
MIN_INTERVAL = {"gemini": 6.5, "openai": 2.5, "anthropic": 0.5}   # seconds between calls (free-tier RPM)


def detect_provider() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return ""


def available() -> bool:
    return bool(detect_provider())


class QuotaExhausted(RuntimeError):
    """The provider's daily quota for this model is used up: retrying today is pointless."""


class _OpenAICompatClient:
    """Minimal client exposing .messages.create(...) like the Anthropic SDK, over plain requests."""
    def __init__(self, base_url: str, api_key: str):
        self.base_url, self.api_key, self.messages = base_url.rstrip("/"), api_key, self

    def _post(self, payload):
        return requests.post(f"{self.base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             json=payload, timeout=90)

    def create(self, model, max_tokens, system, messages):
        payload = {"model": model, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system}, *messages]}
        r = self._post(payload)
        if r.status_code == 429:
            if "PerDay" in (getattr(r, "text", "") or ""):                     # daily cap: waiting won't help, stop now
                raise QuotaExhausted(f"daily free-tier quota used up for {model}")
            time.sleep(60)                             # per-minute limit: wait, retry exactly once
            r = self._post(payload)
            if r.status_code == 429 and "PerDay" in (getattr(r, "text", "") or ""):
                raise QuotaExhausted(f"daily free-tier quota used up for {model}")
        r.raise_for_status()
        choice = r.json()["choices"][0]
        text = choice["message"].get("content") or ""
        if not text.strip() and choice.get("finish_reason") == "length":
            raise ValueError(f"{model}: output empty, cut off by the token limit (model spent it thinking)")
        from types import SimpleNamespace as NS
        return NS(content=[NS(type="text", text=text)])


class LLM:
    def __init__(self, client=None, model=None, provider=None, role="screen"):
        self.provider = provider or (detect_provider() if client is None else "test")
        if client is None:
            if self.provider == "gemini":
                client = _OpenAICompatClient(GEMINI_BASE, os.environ["GEMINI_API_KEY"])
            elif self.provider == "openai":
                client = _OpenAICompatClient(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"])
            elif self.provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic()
            else:
                raise RuntimeError("No LLM key set (GEMINI_API_KEY / LLM_API_KEY+LLM_BASE_URL / ANTHROPIC_API_KEY)")
        self.client = client
        env_model = os.environ.get("LLM_WRITER_MODEL" if role == "write" else "LLM_MODEL")
        role_default = (WRITER_DEFAULTS if role == "write" else DEFAULTS).get(self.provider)
        self.model = (model or env_model
                      or (os.environ.get("CLAUDE_MODEL") if self.provider == "anthropic" else "")
                      or role_default or DEFAULTS.get(self.provider, ""))
        self.min_interval = MIN_INTERVAL.get(self.provider, 0)
        self.calls, self._last = 0, 0.0

    def text(self, system: str, prompt: str, max_tokens: int = 1200) -> str:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self.calls += 1
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        self._last = time.time()
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    def json(self, system: str, prompt: str, max_tokens: int = 4096) -> dict:
        """Ask for JSON; if the answer is cut off or malformed, ask once more."""
        prompt += "\n\nReturn ONLY a JSON object, no prose, no code fences."
        try:
            return parse_json(self.text(system, prompt, max_tokens))
        except (ValueError, TypeError):
            return parse_json(self.text(system, prompt, max_tokens))


def parse_json(raw: str) -> dict:
    """Tolerant JSON parse: strips code fences / leading prose."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))
