from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic
from anthropic import APIStatusError

from app.config import get_settings


def mask_secret(value: str) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "<set>"
    return f"{value[:4]}...{value[-4:]}"


def main() -> int:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)

    settings = get_settings()
    api_key = settings.anthropic_api_key
    model = settings.anthropic_model

    print("Loaded environment from:", env_path)
    print("API key present:", bool(api_key))
    print("API key mask:", mask_secret(api_key))
    print("Model string:", model)
    print("Base URL:", "https://api.anthropic.com/v1/messages")
    print()

    client = Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=128,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly: Claude test OK",
                }
            ],
        )
        print("Status: success")
        print(json.dumps(response.model_dump(), indent=2, default=str))
        return 0
    except APIStatusError as exc:
        print(f"Status: error {exc.status_code}")
        print("Error type:", type(exc).__name__)
        if getattr(exc, "response", None) is not None:
            print("Response headers:")
            print(json.dumps(dict(exc.response.headers), indent=2))
            print("Response body:")
            try:
                print(json.dumps(exc.response.json(), indent=2))
            except Exception:
                print(exc.response.text)
        else:
            print(str(exc))
        return 1
    except Exception as exc:
        print("Status: unexpected_error")
        print("Error type:", type(exc).__name__)
        print(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
