"""
Manual smoke test (not a pytest suite - hits real free-tier APIs) proving each provider's
tool-calling round-trip actually works through the normalized LLMClient interface, before
agent_loop.py depends on it.

Run with: python backend/tests/test_llm_client_smoke.py [gemini|groq|openrouter|all]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_client import Message, TextBlock, ToolDefinition, get_llm_client

WEATHER_TOOL = ToolDefinition(
    name="get_weather",
    description="Get the current weather for a city.",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)


def run_smoke_test(provider: str) -> bool:
    print(f"\n{'=' * 60}\nTesting provider: {provider}\n{'=' * 60}")
    try:
        client = get_llm_client(provider)
        result = client.generate(
            system="You are a helpful assistant. Use the get_weather tool when asked about weather.",
            messages=[Message(role="user", content=[TextBlock(text="What's the weather in Bangalore?")])],
            tools=[WEATHER_TOOL],
        )
        print(f"stop_reason: {result.stop_reason}")
        print(f"raw_usage: {result.raw_usage}")
        for block in result.content:
            print(f"  block: {block}")

        if result.stop_reason != "tool_use":
            print(f"FAIL: expected stop_reason 'tool_use', got '{result.stop_reason}'")
            return False

        tool_calls = [b for b in result.content if b.type == "tool_use"]
        if not tool_calls:
            print("FAIL: no tool_use block found")
            return False
        if tool_calls[0].name != "get_weather":
            print(f"FAIL: expected tool name 'get_weather', got '{tool_calls[0].name}'")
            return False
        if "city" not in tool_calls[0].input:
            print(f"FAIL: expected 'city' arg, got {tool_calls[0].input}")
            return False

        print(f"PASS: {provider} correctly called get_weather(city={tool_calls[0].input.get('city')!r})")
        return True
    except Exception as e:
        print(f"FAIL: {provider} raised {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    providers = ["gemini", "groq", "openrouter"] if target == "all" else [target]

    results = {p: run_smoke_test(p) for p in providers}

    print(f"\n{'=' * 60}\nSummary\n{'=' * 60}")
    for p, ok in results.items():
        print(f"  {p}: {'PASS' if ok else 'FAIL'}")

    if not all(results.values()):
        sys.exit(1)
