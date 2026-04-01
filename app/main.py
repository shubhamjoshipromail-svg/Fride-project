from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings
from app.llm import generate_recipe_from_image


app = FastAPI(title="AI Sous-Chef")
settings = get_settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "recipe": None,
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
    error = None

    try:
        if not image.filename:
            raise ValueError("Please upload an image of your ingredients or fridge.")

        suffix = Path(image.filename).suffix.lower() or ".jpg"
        saved_path = settings.upload_dir / f"{uuid4().hex}{suffix}"
        saved_path.write_bytes(await image.read())

        recipe = generate_recipe_from_image(saved_path, preferences.strip(), settings)
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
            "error": error,
            "preferences": preferences,
        },
    )
