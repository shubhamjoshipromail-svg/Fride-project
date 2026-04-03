import base64
from pathlib import Path

import anthropic

from app.config import Settings


SYSTEM_PROMPT = """You are a personalized AI sous-chef.
Always start your response with a single line in this exact format:
TITLE: [recipe name here]
Then continue with the full recipe below it.

Analyze the uploaded fridge or ingredient image, identify likely edible ingredients,
and create a recipe that uses those ingredients while following the user's dietary
needs or cravings exactly.

Return:
1. A short title.
2. A brief ingredient assessment.
3. A step-by-step recipe.
4. Optional substitutions if the image is ambiguous.

If the image is unclear, say what you are inferring and keep the recipe practical."""

def generate_recipe_from_image(
    image_path: Path,
    user_preferences: str,
    settings: Settings,
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
        max_tokens=1024,
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
                        "text": (
                            "Build a recipe from the visible ingredients in this image. "
                            f"User preferences: {user_preferences or 'No extra preference provided.'}"
                        ),
                    },
                ],
            }
        ],
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    response_text = "\n".join(text_blocks).strip()

    title = "Chef's Special"
    cleaned_lines = []
    for line in response_text.splitlines():
        if line.startswith("TITLE:"):
            extracted = line.replace("TITLE:", "", 1).strip()
            if extracted:
                title = extracted
            continue
        cleaned_lines.append(line)

    cleaned_response = "\n".join(cleaned_lines).strip()
    return (cleaned_response, title)


def stream_recipe_from_image(
    image_path: Path,
    user_preferences: str,
    settings: Settings,
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
        max_tokens=1024,
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
                        "text": (
                            "Build a recipe from the visible ingredients "
                            "in this image. "
                            f"User preferences: {user_preferences or 'No extra preference provided.'}"
                        ),
                    },
                ],
            }
        ],
    ) as stream:
        for chunk in stream.text_stream:
            if chunk:
                yield chunk


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
