# AI Sous-Chef

A simple FastAPI web app that accepts a photo of your fridge or ingredients plus a flavor or dietary preference, then asks a multimodal Claude model to generate a tailored recipe.

## Features

- Upload an image of ingredients or an open fridge.
- Add cravings or dietary constraints in plain language.
- Generate a personalized step-by-step recipe.
- Keep the multimodal API call isolated in `app/llm.py` for an easy future Ollama swap.

## Project Structure

```text
app/
  config.py
  llm.py
  main.py
static/css/
templates/
uploads/
requirements.txt
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your Anthropic API key to `.env`:

```bash
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-sonnet-4-6
```

## Run

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Future Extensions

- Add video walkthroughs in the placeholder area inside `templates/index.html`.
- Add recipe persistence or saved favorites next to the existing commented frontend hook.
- Replace `generate_recipe_from_image()` in `app/llm.py` with an Ollama-backed implementation when you're ready.
