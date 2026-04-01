import base64
from pathlib import Path

from openai import OpenAI

from app.config import Settings


SYSTEM_PROMPT = """You are a personalized AI sous-chef.
Analyze the uploaded fridge or ingredient image, identify likely edible ingredients,
and create a recipe that uses those ingredients while following the user's dietary
needs or cravings exactly.

Return:
1. A short title.
2. A brief ingredient assessment.
3. A step-by-step recipe.
4. Optional substitutions if the image is ambiguous.

If the image is unclear, say what you are inferring and keep the recipe practical."""


def _encode_image(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("utf-8")


def generate_recipe_from_image(
    image_path: Path,
    user_preferences: str,
    settings: Settings,
) -> str:
    """
    Send fridge imagery and flavor constraints to OpenAI's multimodal model.

    This function is intentionally isolated so the provider can be swapped later
    for a local Ollama vision model without changing the route logic.
    """
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Add it to your environment or .env file."
        )

    mime_type = "image/jpeg"
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix == ".webp":
        mime_type = "image/webp"

    client = OpenAI(api_key=settings.openai_api_key)
    image_b64 = _encode_image(image_path)
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Build a recipe from the visible ingredients in this image. "
                            f"User preferences: {user_preferences or 'No extra preference provided.'}"
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{image_b64}",
                    },
                ],
            },
        ],
    )
    return response.output_text.strip()


# Future Ollama swap idea:
# def generate_recipe_from_image_ollama(image_path: Path, user_preferences: str) -> str:
#     ...
