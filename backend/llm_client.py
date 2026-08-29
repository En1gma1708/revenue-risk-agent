"""
Swappable LLM client interface — the agent loop calls ONE shape (`LLMClient.generate`) regardless
of which free-tier provider is active. This exists specifically because the LLM provider decision
(see DEVLOG.md 2026-08-24) was made under a hard no-spend constraint and needed to stay revisable:
Gemini, Groq, and OpenRouter are all wired up so they can be A/B tested once the agent loop exists,
selected via LLM_PROVIDER in .env.

Design note: this normalizes to Anthropic-style message/tool-use shapes (role, content blocks with
type "text" | "tool_use" | "tool_result") because that vocabulary is what the rest of this project's
docs (PRD.md, agent loop design) already use, and translating INTO that shape once here is simpler
than making agent_loop.py aware of three different native formats.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional  # noqa: F401 (Any used by provider_state)

from dotenv import load_dotenv

load_dotenv()

# Free-tier rate limits are a real, expected condition (see DEVLOG.md -- Gemini's free tier is as
# low as 5 requests/minute on some models), not an edge case to ignore. Retry with backoff rather
# than let a batch run crash on the first 429.
MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_DELAY_SECONDS = 15.0


def _extract_retry_delay_seconds(error_message: str) -> Optional[float]:
    """Best-effort parse of a provider's suggested retry delay from its error text (e.g. Gemini's
    'Please retry in 37.475434769s.'). Falls back to None if not found."""
    match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_message, re.IGNORECASE)
    return float(match.group(1)) + 1.0 if match else None


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "rate limit" in text.lower() or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


class DailyQuotaExhausted(Exception):
    """Raised instead of retrying when the error is clearly a PER-DAY quota, not a per-minute
    rate limit -- e.g. Gemini's free tier is 20 requests/day on some models (confirmed live,
    see DEVLOG.md). Retrying a daily cap with a 60s backoff just burns the retry budget for no
    reason; the caller should switch provider or stop, not wait."""


def _is_daily_quota_error(exc: Exception) -> bool:
    text = str(exc)
    return "PerDay" in text or "per day" in text.lower() or "RequestsPerDay" in text


def with_rate_limit_retry(fn):
    def wrapped(*args, **kwargs):
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - provider SDKs each raise their own exception types
                if not _is_rate_limit_error(e):
                    raise
                if _is_daily_quota_error(e):
                    raise DailyQuotaExhausted(
                        f"Daily free-tier quota exhausted for this provider/model: {e}. "
                        f"Retrying won't help until the quota resets -- switch LLM_PROVIDER "
                        f"or wait for the daily reset."
                    ) from e
                if attempt == MAX_RATE_LIMIT_RETRIES - 1:
                    raise
                delay = _extract_retry_delay_seconds(str(e)) or DEFAULT_RETRY_DELAY_SECONDS
                print(f"[llm_client] Rate limited (attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES}), "
                      f"waiting {delay:.1f}s before retry...")
                time.sleep(delay)
        raise RuntimeError("unreachable")  # loop always returns or raises
    return wrapped


# ---------------------------------------------------------------------------
# Normalized message/content shapes
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict   # JSON schema, same shape Anthropic/OpenAI/Gemini all expect for parameters


@dataclass
class ToolUseBlock:
    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    # Opaque per-provider round-trip data (e.g. Gemini's thought_signature, required on
    # multi-turn function-call parts or the API rejects the next turn). agent_loop.py never
    # reads this; only the client that produced it reads it back on the next turn.
    provider_state: Optional[Any] = None


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ToolResultBlock:
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: list[ContentBlock]


@dataclass
class GenerateResult:
    content: list[ContentBlock]
    stop_reason: Literal["tool_use", "end_turn", "max_tokens"]
    raw_usage: dict = field(default_factory=dict)


class LLMClient:
    """Interface every provider client implements. agent_loop.py depends only on this."""

    def generate(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition],
        max_tokens: int = 4096,
    ) -> GenerateResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Gemini implementation
# ---------------------------------------------------------------------------

class GeminiClient(LLMClient):
    def __init__(self, model: str = "gemini-3.6-flash", api_key: Optional[str] = None):
        from google import genai
        self.client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self.model = model

    @with_rate_limit_retry
    def generate(self, system, messages, tools, max_tokens=4096) -> GenerateResult:
        from google.genai import types

        gemini_tools = [
            types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters=t.input_schema,
                )
            ])
            for t in tools
        ]

        contents = []
        for m in messages:
            parts = []
            for block in m.content:
                if isinstance(block, TextBlock):
                    parts.append(types.Part(text=block.text))
                elif isinstance(block, ToolUseBlock):
                    part = types.Part(function_call=types.FunctionCall(
                        name=block.name, args=block.input,
                    ))
                    if block.provider_state is not None:
                        part.thought_signature = block.provider_state
                    parts.append(part)
                elif isinstance(block, ToolResultBlock):
                    parts.append(types.Part(function_response=types.FunctionResponse(
                        name=block.tool_use_id, response={"result": block.content},
                    )))
            role = "model" if m.role == "assistant" else "user"
            contents.append(types.Content(role=role, parts=parts))

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=gemini_tools if gemini_tools else None,
                max_output_tokens=max_tokens,
            ),
        )

        out_blocks: list[ContentBlock] = []
        has_tool_call = False
        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "function_call", None) is not None:
                    has_tool_call = True
                    out_blocks.append(ToolUseBlock(
                        id=part.function_call.name,   # Gemini has no call id; name doubles as one
                        name=part.function_call.name,
                        input=dict(part.function_call.args or {}),
                        # Must be replayed on the next turn's matching function_call part, or
                        # Gemini rejects the request with a 400 (missing thought_signature) --
                        # see DEVLOG.md for the failure this fixes.
                        provider_state=getattr(part, "thought_signature", None),
                    ))
                elif getattr(part, "text", None):
                    out_blocks.append(TextBlock(text=part.text))

        return GenerateResult(
            content=out_blocks,
            stop_reason="tool_use" if has_tool_call else "end_turn",
            raw_usage={"provider": "gemini", "model": self.model},
        )


# ---------------------------------------------------------------------------
# Groq implementation (OpenAI-compatible tool-calling shape)
# ---------------------------------------------------------------------------

class GroqClient(LLMClient):
    def __init__(self, model: str = "openai/gpt-oss-120b", api_key: Optional[str] = None):
        from groq import Groq
        self.client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])
        self.model = model

    @with_rate_limit_retry
    def generate(self, system, messages, tools, max_tokens=4096) -> GenerateResult:
        return _generate_openai_compatible(self.client, self.model, system, messages, tools, max_tokens, provider_name="groq")


# ---------------------------------------------------------------------------
# OpenRouter implementation (OpenAI-compatible API, different base_url)
# ---------------------------------------------------------------------------

class OpenRouterClient(LLMClient):
    def __init__(self, model: str = "nvidia/nemotron-3-super-120b-a12b:free", api_key: Optional[str] = None):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=api_key or os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )
        self.model = model

    @with_rate_limit_retry
    def generate(self, system, messages, tools, max_tokens=4096) -> GenerateResult:
        return _generate_openai_compatible(self.client, self.model, system, messages, tools, max_tokens, provider_name="openrouter")


# ---------------------------------------------------------------------------
# Shared OpenAI-compatible-format helper (Groq and OpenRouter both speak this dialect)
# ---------------------------------------------------------------------------

def _generate_openai_compatible(client, model, system, messages, tools, max_tokens, provider_name) -> GenerateResult:
    oa_tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]

    oa_messages: list[dict] = [{"role": "system", "content": system}]
    # OpenAI-compatible APIs need one assistant message with tool_calls[], followed by
    # one "tool" role message PER tool_result (not bundled), unlike Anthropic's single
    # user message with multiple tool_result blocks. Translate accordingly.
    for m in messages:
        text_parts = [b.text for b in m.content if isinstance(b, TextBlock)]
        tool_uses = [b for b in m.content if isinstance(b, ToolUseBlock)]
        tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]

        if tool_uses:
            oa_messages.append({
                "role": "assistant",
                "content": " ".join(text_parts) or None,
                "tool_calls": [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {"name": tu.name, "arguments": json.dumps(tu.input)},
                    }
                    for tu in tool_uses
                ],
            })
        elif text_parts:
            oa_messages.append({"role": m.role, "content": " ".join(text_parts)})

        for tr in tool_results:
            oa_messages.append({
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": tr.content,
            })

    response = client.chat.completions.create(
        model=model,
        messages=oa_messages,
        tools=oa_tools if oa_tools else None,
        max_tokens=max_tokens,
    )

    choice = response.choices[0]
    out_blocks: list[ContentBlock] = []
    if choice.message.content:
        out_blocks.append(TextBlock(text=choice.message.content))
    has_tool_call = bool(choice.message.tool_calls)
    for tc in (choice.message.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            args = {}
        out_blocks.append(ToolUseBlock(id=tc.id, name=tc.function.name, input=args))

    return GenerateResult(
        content=out_blocks,
        stop_reason="tool_use" if has_tool_call else "end_turn",
        raw_usage={"provider": provider_name, "model": model},
    )


# ---------------------------------------------------------------------------
# Factory — reads LLM_PROVIDER from .env. Supports MULTIPLE free accounts per provider
# (added 2026-08-29 after a full day of a single account's daily quota being the hard ceiling
# on batch throughput -- see DEVLOG.md "we really have to improve this"). Each *_API_KEY env var
# may hold a comma-separated list of keys, one per account: e.g.
#   GROQ_API_KEY=key_from_account_1,key_from_account_2
# get_llm_client(provider) still returns ONE client (the first/only account) for simple callers;
# get_llm_clients_for_provider(provider) returns ALL configured accounts as separate clients, for
# callers (run_batch.py) that want to round-robin across accounts to multiply real daily capacity.
# ---------------------------------------------------------------------------

_PROVIDER_CLASSES = {"gemini": GeminiClient, "groq": GroqClient, "openrouter": OpenRouterClient}
_PROVIDER_ENV_VARS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def _keys_for_provider(provider: str) -> list[str]:
    raw = os.environ.get(_PROVIDER_ENV_VARS[provider], "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise KeyError(f"No API key(s) configured for {provider} (set {_PROVIDER_ENV_VARS[provider]} in .env)")
    return keys


def get_llm_client(provider: Optional[str] = None) -> LLMClient:
    provider = (provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if provider not in _PROVIDER_CLASSES:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected gemini | groq | openrouter)")
    key = _keys_for_provider(provider)[0]   # first configured account
    return _PROVIDER_CLASSES[provider](api_key=key)


def get_llm_clients_for_provider(provider: str) -> list[LLMClient]:
    """Returns one client per configured account (comma-separated keys) for this provider, in
    order. Use this instead of get_llm_client() when you want to spread load across multiple
    free accounts of the SAME provider, not just across different providers."""
    provider = provider.lower()
    if provider not in _PROVIDER_CLASSES:
        raise ValueError(f"Unknown provider: {provider!r} (expected gemini | groq | openrouter)")
    cls = _PROVIDER_CLASSES[provider]
    return [cls(api_key=key) for key in _keys_for_provider(provider)]
