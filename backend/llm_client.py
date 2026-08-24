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
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from dotenv import load_dotenv

load_dotenv()


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
    def __init__(self, model: str = "gemini-3.6-flash"):
        from google import genai
        api_key = os.environ["GEMINI_API_KEY"]
        self.client = genai.Client(api_key=api_key)
        self.model = model

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
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=block.name, args=block.input,
                    )))
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
    def __init__(self, model: str = "openai/gpt-oss-120b"):
        from groq import Groq
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.model = model

    def generate(self, system, messages, tools, max_tokens=4096) -> GenerateResult:
        return _generate_openai_compatible(self.client, self.model, system, messages, tools, max_tokens, provider_name="groq")


# ---------------------------------------------------------------------------
# OpenRouter implementation (OpenAI-compatible API, different base_url)
# ---------------------------------------------------------------------------

class OpenRouterClient(LLMClient):
    def __init__(self, model: str = "nvidia/nemotron-3-super-120b-a12b:free"):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )
        self.model = model

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
# Factory — reads LLM_PROVIDER from .env
# ---------------------------------------------------------------------------

def get_llm_client(provider: Optional[str] = None) -> LLMClient:
    provider = (provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if provider == "gemini":
        return GeminiClient()
    if provider == "groq":
        return GroqClient()
    if provider == "openrouter":
        return OpenRouterClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected gemini | groq | openrouter)")
