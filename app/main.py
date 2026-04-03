import asyncio
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings
from app.images import fetch_food_image
from app.llm import generate_recipe_from_image, get_nutrition, stream_recipe_from_image


app = FastAPI(title="AI Sous-Chef")
settings = get_settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def startup():
    print(f"[FridgeChef] Recipe model: {settings.anthropic_model}")
    print(f"[FridgeChef] Nutrition model: gpt-4o-mini")
    print(f"[FridgeChef] Anthropic key set: {bool(settings.anthropic_api_key)}")
    print(f"[FridgeChef] OpenAI key set: {bool(settings.openai_api_key)}")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "recipe": None,
            "image_url": None,
            "error": None,
            "preferences": "",
        },
    )


@app.post("/", response_class=HTMLResponse)
async def generate_recipe(
    request: Request,
    image: UploadFile = File(...),
    preferences: str = Form(""),
) -> HTMLResponse:
    saved_path: Path | None = None
    recipe = None
    recipe_title = None
    image_url = None
    error = None

    try:
        if not image.filename:
            raise ValueError("Please upload an image of your ingredients or fridge.")

        suffix = Path(image.filename).suffix.lower() or ".jpg"
        saved_path = settings.upload_dir / f"{uuid4().hex}{suffix}"
        saved_path.write_bytes(await image.read())

        recipe, recipe_title = generate_recipe_from_image(
            saved_path, preferences.strip(), settings
        )
        image_url = fetch_food_image(recipe_title, settings.unsplash_access_key)
    except Exception as exc:
        error = str(exc)
    finally:
        if saved_path and saved_path.exists():
            saved_path.unlink(missing_ok=True)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "recipe": recipe,
            "image_url": image_url,
            "error": error,
            "preferences": preferences,
        },
    )


@app.post("/stream", response_class=StreamingResponse)
async def stream_recipe(
    request: Request,
    image: UploadFile = File(...),
    preferences: str = Form(""),
):
    """
    Streams the recipe as Server-Sent Events (SSE).
    Frontend connects via fetch() and reads chunks as they arrive.
    """
    saved_path: Path | None = None

    try:
        if not image.filename:
            async def error_stream():
                yield f"data: {json.dumps({'error': 'Please upload an image.'})}\n\n"

            return StreamingResponse(
                error_stream(),
                media_type="text/event-stream",
            )

        suffix = Path(image.filename).suffix.lower() or ".jpg"
        saved_path = settings.upload_dir / f"{uuid4().hex}{suffix}"
        saved_path.write_bytes(await image.read())
        image_path_copy = saved_path

        async def generate():
            full_text = ""
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

            def run_stream():
                try:
                    for chunk in stream_recipe_from_image(
                        image_path_copy,
                        preferences.strip(),
                        settings,
                    ):
                        asyncio.run_coroutine_threadsafe(
                            queue.put(("chunk", chunk)),
                            loop,
                        )
                    asyncio.run_coroutine_threadsafe(queue.put(("done", "")), loop)
                except Exception as exc:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("error", str(exc))),
                        loop,
                    )

            worker = Thread(target=run_stream, daemon=True)
            worker.start()

            try:
                while True:
                    kind, payload = await queue.get()

                    if kind == "chunk":
                        full_text += payload
                        yield f"data: {json.dumps({'chunk': payload})}\n\n"
                        await asyncio.sleep(0)
                        continue

                    if kind == "error":
                        yield f"data: {json.dumps({'error': payload})}\n\n"
                        break

                    if kind == "done":
                        lines = full_text.split("\n")
                        title_line = next(
                            (line for line in lines if line.startswith("TITLE:")),
                            "",
                        )
                        recipe_title = title_line.replace("TITLE:", "", 1).strip()
                        image_url = fetch_food_image(
                            recipe_title or "food recipe",
                            settings.unsplash_access_key,
                        )
                        yield (
                            f"data: {json.dumps({'done': True, 'title': recipe_title, 'image_url': image_url or ''})}\n\n"
                        )
                        break
            finally:
                if image_path_copy and image_path_copy.exists():
                    image_path_copy.unlink(missing_ok=True)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    except Exception as exc:
        async def error_stream():
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
        )


@app.post("/nutrition")
async def nutrition(
    request: Request,
    recipe_text: str = Body(..., embed=True),
):
    """
    Accepts plain recipe text, returns nutrition JSON.
    Uses GPT-4o-mini for cost efficiency.
    Never crashes - returns empty dict on any failure.
    """
    if not recipe_text or not recipe_text.strip():
        return {"nutrition": {}, "error": "No recipe text provided."}
    try:
        result = get_nutrition(recipe_text.strip(), settings)
        if not result:
            return {
                "nutrition": {},
                "error": "Could not calculate nutrition. Check your OpenAI API key.",
            }
        return {"nutrition": result, "error": None}
    except Exception as exc:
        return {"nutrition": {}, "error": str(exc)}
