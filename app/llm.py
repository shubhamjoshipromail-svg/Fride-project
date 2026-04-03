import base64
from pathlib import Path

import anthropic

from app.config import Settings


SYSTEM_PROMPT = """You are a personalized AI sous-chef.
Analyze the uploaded fridge image, identify all visible 
edible ingredients, and generate a recipe tailored to 
the user's preferences.

CRITICAL FORMATTING RULES — follow exactly:
1. Start with: TITLE: [recipe name]
2. Then write the full recipe in clean markdown
3. Use ## for section headers (Ingredients, Instructions)
4. Use bullet points for ingredients
5. Use numbered list for steps
6. Bold the action word at the start of each step
7. End with a ## Tips section with 1-2 practical tips
8. NEVER return JSON
9. NEVER use code blocks or code fences
10. NEVER use backticks anywhere in your response
11. Write in plain conversational markdown only

The recipe should feel like it was written by a 
knowledgeable friend — warm, practical, specific.
Include quantities for every ingredient.
Each step should be detailed enough that a beginner 
can follow it confidently."""


def _clean_json_payload(raw_text: str) -> str:
    return raw_text.strip().replace("```json", "").replace("```", "").strip()


def parse_recipe_response(raw_text: str) -> tuple[str, str]:
    cleaned = _clean_json_payload(raw_text)
    title = "Chef's Special"

    cleaned_lines = []
    for line in cleaned.splitlines():
        normalized = line.lstrip("# ").strip()
        if normalized.startswith("TITLE:"):
            extracted = normalized.replace("TITLE:", "", 1).strip()
            if extracted:
                title = extracted
            continue
        cleaned_lines.append(line)

    cleaned_response = "\n".join(cleaned_lines).strip()
    return cleaned_response, title


def _build_prompt(
    user_preferences: str,
    inventory_context: str = "",
) -> str:
    pref_text = (
        user_preferences or "No extra preference provided."
    )
    text = (
        "Build a recipe from the visible ingredients "
        "in this image.\n\n"
    )
    if inventory_context:
        text += (
            "IMPORTANT — Known fridge inventory "
            "from previous scans:\n"
            f"{inventory_context}\n\n"
            "Prioritize ingredients that are expiring "
            "soon. Cross-reference the image with "
            "this known inventory.\n\n"
        )
    text += f"User preferences: {pref_text}"
    return text


def generate_recipe_from_image(
    image_path: Path,
    user_preferences: str,
    settings: Settings,
    inventory_context: str = "",
) -> tuple[str, str]:
    """
    Send fridge imagery and flavor constraints to Anthropic's Claude multimodal model.

    This function is intentionally isolated so the provider can be swapped later
    for a local Ollama vision model without changing the route logic.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured. Add it to your environment or .env file."
        )

    mime_type = "image/jpeg"
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix == ".webp":
        mime_type = "image/webp"

    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": _build_prompt(
                            user_preferences,
                            inventory_context
                        ),
                    },
                ],
            }
        ],
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    response_text = "\n".join(text_blocks).strip()
    return parse_recipe_response(response_text)


def stream_recipe_from_image(
    image_path: Path,
    user_preferences: str,
    settings: Settings,
    inventory_context: str = "",
):
    """
    Streaming version of generate_recipe_from_image.
    Yields raw text chunks as they arrive from the API.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("API key is not configured.")

    mime_type = "image/jpeg"
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix == ".webp":
        mime_type = "image/webp"

    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": _build_prompt(
                            user_preferences,
                            inventory_context
                        ),
                    },
                ],
            }
        ],
    ) as stream:
        for chunk in stream.text_stream:
            if chunk:
                yield chunk


def stream_recipe_from_inventory(
    inventory_context: str,
    user_preferences: str,
    settings: Settings,
):
    """
    Generate a recipe from known inventory only.
    No image needed - uses existing inventory as
    the ingredient source.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("API key is not configured.")
    if not inventory_context:
        raise RuntimeError(
            "No inventory found. Scan your fridge first."
        )

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key
    )

    pref_text = (
        user_preferences or "No extra preference provided."
    )

    prompt = (
        "Generate a recipe using ingredients from "
        "my fridge inventory below. Do not invent "
        "ingredients I haven't listed - only use "
        "what's available.\n\n"
        f"Current inventory:\n{inventory_context}\n\n"
        f"User preferences: {pref_text}"
    )

    with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    ) as stream:
        for chunk in stream.text_stream:
            if chunk:
                yield chunk


def parse_receipt_image(
    image_path: Path,
    settings: Settings,
) -> list[dict]:
    """
    Parse a receipt photo and extract purchased items.
    Returns a list of dicts with name and quantity.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("API key is not configured.")

    mime_type = "image/jpeg"
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix == ".webp":
        mime_type = "image/webp"

    image_b64 = base64.b64encode(
        image_path.read_bytes()
    ).decode("utf-8")
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key
    )

    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a grocery receipt. "
                            "Extract all food and grocery "
                            "items purchased. "
                            "Ignore non-food items, "
                            "prices, taxes, store info.\n\n"
                            "Return ONLY a JSON array. "
                            "No explanation. No markdown. "
                            "Just raw JSON like this:\n"
                            '[{"name": "spinach", '
                            '"quantity": "1 bag"}, '
                            '{"name": "milk", '
                            '"quantity": "1 gallon"}]\n\n'
                            "Clean the names - no brand "
                            "names, no abbreviations, "
                            "just the ingredient name."
                        ),
                    },
                ],
            }
        ],
    )

    import json as _json

    text_blocks = [
        block.text for block in response.content
        if block.type == "text"
    ]
    raw = "\n".join(text_blocks).strip()
    clean = (
        raw
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )
    try:
        items = _json.loads(clean)
        if isinstance(items, list):
            return items
        return []
    except Exception:
        return []


def get_nutrition(
    recipe_text: str,
    settings: Settings,
) -> dict:
    """
    Send recipe text to GPT-4o-mini and get nutrition estimate.
    Returns dict with calories, protein, carbs, fat, fiber, servings.
    Returns empty dict silently on any failure.
    """
    import json

    NUTRITION_PROMPT = """You are a precise nutrition calculator.
    Given a recipe, estimate the nutritional content per serving.
    
    Rules:
    - Base estimates on standard ingredient quantities
    - If quantities are vague, use reasonable home-cooking assumptions
    - Account for cooking methods (fried adds fat, boiled doesn't)
    - Be realistic, not optimistic
    
    Respond ONLY with a valid JSON object.
    No explanation. No markdown. No code fences. Just raw JSON:
    {
      "calories": 450,
      "protein": 32,
      "carbs": 28,
      "fat": 18,
      "fiber": 6,
      "servings": 2
    }
    All values must be integers."""

    if not settings.openai_api_key:
        return {}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=150,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": NUTRITION_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        "Calculate nutrition for this recipe:\n\n"
                        f"{recipe_text[:3000]}"
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        required = ["calories", "protein", "carbs", "fat"]
        for key in required:
            if key not in data:
                return {}
            data[key] = int(data[key])
        if "fiber" in data:
            data["fiber"] = int(data["fiber"])
        if "servings" in data:
            data["servings"] = int(data["servings"])
        return data
    except Exception:
        return {}


# Future Ollama swap idea:
# def generate_recipe_from_image_ollama(image_path: Path, user_preferences: str) -> str:
#     ...
