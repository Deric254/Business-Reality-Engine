"""Check an AI key works the way the app uses it, before you rely on it.

    python scripts/check_ai.py --kind anthropic --key sk-... [--model claude-sonnet-5-5]
    python scripts/check_ai.py --kind openai_compatible --key ... --model llama3 --base-url https://api.groq.com/openai/v1

It asks for the small JSON object the app asks for when it turns a question into a query plan, and reports whether the
answer parsed. The authors have not run the AI layer against a real key, so this is the first thing to run. Nothing is
stored anywhere, and the key is not printed."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.reasoning.llm_providers import build_provider  # noqa: E402

SYSTEM = "Reply with one JSON object and nothing else. No prose, no code fences."
USER = 'A shop owner asks: "what were my best products last month?" Reply as {"intent": "top_products", "period": "last_month", "limit": 5}.'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["anthropic", "openai", "openai_compatible"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    a = ap.parse_args()
    provider = build_provider(a.kind, a.key, a.model, a.base_url)
    started = time.time()
    try:
        raw = provider.generate_json(SYSTEM, USER, max_tokens=200)
    except Exception as e:                                     # the point of this script is to show why it failed
        print(f"FAILED after {time.time() - started:.1f}s: {type(e).__name__}: {str(e).replace(a.key, '***')[:300]}")
        return 1
    try:
        parsed = json.loads(raw)
    except ValueError:
        print(f"Reached the model in {time.time() - started:.1f}s, but the answer was not JSON: {raw[:200]!r}")
        return 1
    print(f"OK in {time.time() - started:.1f}s via {provider.name()}: {parsed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
