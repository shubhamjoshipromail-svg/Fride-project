import asyncio
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR, get_settings
from app.database import SessionLocal, get_db, init_db
from app.images import fetch_food_image
from app.inventory import (
    get_inventory_context,
    merge_receipt_items,
    save_recipe,
    save_scan_and_inventory,
    update_item_statuses,
)
from app.llm import (
    generate_recipe_from_image,
    get_nutrition,
    parse_receipt_image,
    parse_recipe_response,
    stream_recipe_from_image,
    stream_recipe_from_inventory,
)


app = FastAPI(title="AI Sous-Chef")
settings = get_settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def startup():
    init_db()
    db = SessionLocal()
    try:
        update_item_statuses(db, household_id=1)
    finally:
        db.close()
    print(f"[FridgeChef] Recipe model: {settings.anthropic_model}")
    print(f"[FridgeChef] Nutrition model: gpt-4o-mini")
    print(f"[FridgeChef] Anthropic key set: {bool(settings.anthropic_api_key)}")
    print(f"[FridgeChef] OpenAI key set: {bool(settings.openai_api_key)}")
    print(f"[FridgeChef] Database: ready")


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

            inventory_ctx = ""
            ctx_db = SessionLocal()
            try:
                inventory_ctx = get_inventory_context(
                    ctx_db, household_id=1
                )
                if inventory_ctx:
                    print(
                        f"[FridgeChef] Inventory context: "
                        f"{len(inventory_ctx)} chars"
                    )
            except Exception as ctx_err:
                print(
                    f"[FridgeChef] Inventory error: "
                    f"{ctx_err}"
                )
            finally:
                ctx_db.close()

            profile_ctx = ""
            profile_db = SessionLocal()
            try:
                from app.database import Profile

                profile = profile_db.query(
                    Profile
                ).filter_by(household_id=1).first()
                if profile:
                    parts = []
                    if profile.name:
                        parts.append(
                            f"Chef's name: {profile.name}"
                        )
                    if profile.diet_type != "none":
                        parts.append(
                            f"Diet: {profile.diet_type} "
                            f"— strictly follow this"
                        )
                    parts.append(
                        f"Cooking for: "
                        f"{profile.cooking_for} people"
                    )
                    parts.append(
                        f"Skill level: {profile.skill_level}"
                    )
                    parts.append(
                        f"Daily goals: "
                        f"{profile.daily_calories} cal, "
                        f"{profile.daily_protein}g protein, "
                        f"{profile.daily_carbs}g carbs, "
                        f"{profile.daily_fat}g fat"
                    )
                    profile_ctx = "\n".join(parts)
            except Exception as prof_err:
                print(
                    f"[FridgeChef] Profile error: "
                    f"{prof_err}"
                )
            finally:
                profile_db.close()

            def run_stream():
                try:
                    enriched_prefs = preferences.strip()
                    if profile_ctx:
                        enriched_prefs = (
                            f"{enriched_prefs}\n\n"
                            f"User profile:\n{profile_ctx}"
                            if enriched_prefs
                            else f"User profile:\n{profile_ctx}"
                        )
                    for chunk in stream_recipe_from_image(
                        image_path_copy,
                        enriched_prefs,
                        settings,
                        inventory_ctx,
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
                        _, recipe_title = parse_recipe_response(full_text)
                        db = SessionLocal()
                        try:
                            scan = save_scan_and_inventory(
                                db=db,
                                household_id=1,
                                raw_response=full_text,
                                preferences_used=preferences.strip(),
                            )
                            import re as _re

                            clean_title = _re.sub(
                                r"\b(with|and|the|a|an|of|for|in|on|"
                                r"fresh|quick|easy|simple|homemade|"
                                r"classic|creamy|crispy|spicy|style)\b",
                                "",
                                (recipe_title or "food recipe"),
                                flags=_re.IGNORECASE,
                            ).strip()
                            clean_title = _re.sub(r"\s+", " ", clean_title).strip()
                            image_url = fetch_food_image(
                                clean_title or "food recipe",
                                settings.unsplash_access_key,
                            )
                            save_recipe(
                                db=db,
                                household_id=1,
                                scan_id=scan.id,
                                title=recipe_title,
                                markdown_content=full_text,
                                preferences_used=preferences.strip(),
                                image_url=image_url,
                            )
                        finally:
                            db.close()
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


@app.get("/inventory")
async def get_inventory_status():
    """
    Debug endpoint - shows current inventory state.
    Visit http://127.0.0.1:8000/inventory in browser
    to verify database is working.
    """
    db = SessionLocal()
    try:
        from app.database import InventoryItem, Recipe, Scan

        items = db.query(InventoryItem).filter_by(
            household_id=1
        ).all()
        recipes = db.query(Recipe).filter_by(
            household_id=1
        ).all()
        scans = db.query(Scan).filter_by(
            household_id=1
        ).all()
        return {
            "total_scans": len(scans),
            "total_recipes": len(recipes),
            "inventory_items": [
                {
                    "name": item.name,
                    "category": item.category,
                    "status": item.status,
                    "days_fresh": item.days_fresh_estimate,
                    "expiry": str(item.expiry_date) if item.expiry_date else None,
                }
                for item in items
            ],
        }
    finally:
        db.close()


@app.get("/home-data")
async def home_data():
    db = SessionLocal()
    try:
        from app.database import InventoryItem, Recipe, Scan
        from datetime import datetime, timedelta

        now = datetime.utcnow()

        expiring = db.query(InventoryItem).filter(
            InventoryItem.household_id == 1,
            InventoryItem.status.in_(
                ["fresh", "expiring_soon"]
            ),
            InventoryItem.expiry_date <= now + timedelta(days=3),
            InventoryItem.expiry_date >= now,
        ).order_by(InventoryItem.expiry_date).limit(5).all()

        total_inventory = db.query(InventoryItem).filter(
            InventoryItem.household_id == 1,
            InventoryItem.status.in_(
                ["fresh", "expiring_soon"]
            ),
        ).count()

        recent_recipes = db.query(Recipe).filter(
            Recipe.household_id == 1
        ).order_by(
            Recipe.created_at.desc()
        ).limit(5).all()

        total_scans = db.query(Scan).filter(
            Scan.household_id == 1
        ).count()

        def days_until(dt):
            if not dt:
                return None
            delta = (dt - now).days
            return max(0, delta)

        return {
            "total_inventory": total_inventory,
            "total_scans": total_scans,
            "expiring_items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "days_left": days_until(item.expiry_date),
                    "status": item.status,
                }
                for item in expiring
            ],
            "recent_recipes": [
                {
                    "id": recipe.id,
                    "title": recipe.title,
                    "image_url": recipe.image_url or "",
                    "created_at": recipe.created_at.isoformat(),
                    "saved": recipe.saved,
                }
                for recipe in recent_recipes
            ],
        }
    finally:
        db.close()


@app.get("/inventory-full")
async def inventory_full():
    db = SessionLocal()
    try:
        from app.database import InventoryItem
        from datetime import datetime

        items = db.query(InventoryItem).filter(
            InventoryItem.household_id == 1,
            InventoryItem.status.in_(
                ["fresh", "expiring_soon"]
            ),
        ).order_by(InventoryItem.expiry_date).all()
        now = datetime.utcnow()
        return {
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "status": item.status,
                    "days_left": max(
                        0,
                        (item.expiry_date - now).days
                    ) if item.expiry_date else None,
                    "date_added": item.date_added.isoformat(),
                    "quantity": item.quantity,
                }
                for item in items
            ]
        }
    finally:
        db.close()


@app.post("/inventory/mark-used/{item_id}")
async def mark_item_used(item_id: int):
    db = SessionLocal()
    try:
        from app.database import InventoryItem

        item = db.query(InventoryItem).filter(
            InventoryItem.id == item_id,
            InventoryItem.household_id == 1,
        ).first()
        if not item:
            return {
                "success": False,
                "error": "Item not found"
            }
        item.status = "used"
        db.commit()
        return {"success": True, "item_id": item_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@app.delete("/inventory/delete/{item_id}")
async def delete_item(item_id: int):
    db = SessionLocal()
    try:
        from app.database import InventoryItem

        item = db.query(InventoryItem).filter(
            InventoryItem.id == item_id,
            InventoryItem.household_id == 1,
        ).first()
        if not item:
            return {
                "success": False,
                "error": "Item not found"
            }
        db.delete(item)
        db.commit()
        return {"success": True, "item_id": item_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@app.post("/inventory/add")
async def add_inventory_item(
    name: str = Body(..., embed=True),
    category: str = Body(default="other", embed=True),
):
    db = SessionLocal()
    try:
        from app.database import InventoryItem
        from app.inventory import (
            detect_category,
            estimate_expiry,
        )
        from datetime import datetime

        name = name.strip()
        if not name:
            return {
                "success": False,
                "error": "Name required"
            }

        if not category or category == "auto":
            category = detect_category(name)

        now = datetime.utcnow()
        expiry_date, days_fresh = estimate_expiry(
            category, now
        )

        item = InventoryItem(
            household_id=1,
            name=name,
            category=category,
            date_added=now,
            expiry_date=expiry_date,
            days_fresh_estimate=days_fresh,
            status="fresh",
            quantity="some",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return {
            "success": True,
            "item": {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "status": item.status,
                "days_left": days_fresh,
                "quantity": item.quantity,
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@app.get("/profile")
async def get_profile():
    db = SessionLocal()
    try:
        from app.database import Profile

        profile = db.query(Profile).filter_by(
            household_id=1
        ).first()
        if not profile:
            return {
                "name": "",
                "daily_calories": 2000,
                "daily_protein": 150,
                "daily_carbs": 200,
                "daily_fat": 65,
                "diet_type": "none",
                "cooking_for": 1,
                "skill_level": "beginner",
            }
        return {
            "name": profile.name or "",
            "daily_calories": profile.daily_calories,
            "daily_protein": profile.daily_protein,
            "daily_carbs": profile.daily_carbs,
            "daily_fat": profile.daily_fat,
            "diet_type": profile.diet_type,
            "cooking_for": profile.cooking_for,
            "skill_level": profile.skill_level,
        }
    finally:
        db.close()


@app.post("/profile")
async def update_profile(
    name: str = Body(default="", embed=True),
    daily_calories: int = Body(
        default=2000, embed=True
    ),
    daily_protein: int = Body(
        default=150, embed=True
    ),
    daily_carbs: int = Body(
        default=200, embed=True
    ),
    daily_fat: int = Body(default=65, embed=True),
    diet_type: str = Body(
        default="none", embed=True
    ),
    cooking_for: int = Body(default=1, embed=True),
    skill_level: str = Body(
        default="beginner", embed=True
    ),
):
    db = SessionLocal()
    try:
        from app.database import Profile
        from datetime import datetime

        profile = db.query(Profile).filter_by(
            household_id=1
        ).first()
        if not profile:
            profile = Profile(household_id=1)
            db.add(profile)
        profile.name = name
        profile.daily_calories = daily_calories
        profile.daily_protein = daily_protein
        profile.daily_carbs = daily_carbs
        profile.daily_fat = daily_fat
        profile.diet_type = diet_type
        profile.cooking_for = cooking_for
        profile.skill_level = skill_level
        profile.updated_at = datetime.utcnow()
        db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@app.post("/stream-from-inventory",
          response_class=StreamingResponse)
async def stream_recipe_inventory(
    request: Request,
    preferences: str = Form(""),
):
    """
    Stream a recipe using existing inventory only.
    No image upload needed.
    """
    async def generate():
        inventory_ctx = ""
        ctx_db = SessionLocal()
        try:
            inventory_ctx = get_inventory_context(
                ctx_db, household_id=1
            )
        finally:
            ctx_db.close()

        if not inventory_ctx:
            yield (
                f"data: {json.dumps({'error': 'No inventory found. Scan your fridge first to build your inventory.'})}\n\n"
            )
            return

        profile_ctx = ""
        profile_db = SessionLocal()
        try:
            from app.database import Profile
            profile = profile_db.query(
                Profile
            ).filter_by(household_id=1).first()
            if profile:
                parts = []
                if profile.name:
                    parts.append(
                        f"Chef: {profile.name}"
                    )
                if profile.diet_type != "none":
                    parts.append(
                        f"Diet: {profile.diet_type}"
                        f" - strictly follow this"
                    )
                parts.append(
                    f"Cooking for: "
                    f"{profile.cooking_for} people"
                )
                parts.append(
                    f"Skill level: {profile.skill_level}"
                )
                parts.append(
                    f"Daily goals: "
                    f"{profile.daily_calories} cal, "
                    f"{profile.daily_protein}g protein"
                )
                profile_ctx = "\n".join(parts)
        except Exception:
            pass
        finally:
            profile_db.close()

        enriched_prefs = preferences.strip()
        if profile_ctx:
            enriched_prefs = (
                f"{enriched_prefs}\n\n"
                f"User profile:\n{profile_ctx}"
                if enriched_prefs
                else f"User profile:\n{profile_ctx}"
            )

        full_text = ""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, str]] = (
            asyncio.Queue()
        )

        def run_stream():
            try:
                for chunk in stream_recipe_from_inventory(
                    inventory_ctx,
                    enriched_prefs,
                    settings,
                ):
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("chunk", chunk)),
                        loop,
                    )
                asyncio.run_coroutine_threadsafe(
                    queue.put(("done", "")), loop
                )
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(
                    queue.put(("error", str(exc))),
                    loop,
                )

        worker = Thread(
            target=run_stream, daemon=True
        )
        worker.start()

        try:
            while True:
                kind, payload = await queue.get()

                if kind == "chunk":
                    full_text += payload
                    yield (
                        f"data: {json.dumps({'chunk': payload})}\n\n"
                    )
                    await asyncio.sleep(0)
                    continue

                if kind == "error":
                    yield (
                        f"data: {json.dumps({'error': payload})}\n\n"
                    )
                    break

                if kind == "done":
                    _, recipe_title = (
                        parse_recipe_response(full_text)
                    )
                    db = SessionLocal()
                    try:
                        import re as _re
                        clean_title = _re.sub(
                            r'\b(with|and|the|a|an|of|'
                            r'for|in|on|fresh|quick|'
                            r'easy|simple|homemade|'
                            r'classic|creamy|crispy|'
                            r'spicy|style)\b',
                            '',
                            recipe_title or "food recipe",
                            flags=_re.IGNORECASE,
                        ).strip()
                        clean_title = _re.sub(
                            r'\s+', ' ', clean_title
                        ).strip()
                        image_url = fetch_food_image(
                            clean_title or "food recipe",
                            settings.unsplash_access_key,
                        )
                        save_recipe(
                            db=db,
                            household_id=1,
                            scan_id=None,
                            title=recipe_title,
                            markdown_content=full_text,
                            preferences_used=(
                                preferences.strip()
                            ),
                            image_url=image_url,
                        )
                    finally:
                        db.close()
                    yield (
                        f"data: {json.dumps({'done': True, 'title': recipe_title, 'image_url': image_url or ''})}\n\n"
                    )
                    break
        finally:
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/scan-receipt")
async def scan_receipt(
    image: UploadFile = File(...),
):
    """
    Parse a receipt photo and add items to inventory.
    Returns summary of what was added/updated.
    """
    saved_path = None
    try:
        if not image.filename:
            return {
                "success": False,
                "error": "Please upload a receipt image."
            }

        suffix = (
            Path(image.filename).suffix.lower() or ".jpg"
        )
        saved_path = (
            settings.upload_dir / f"{uuid4().hex}{suffix}"
        )
        saved_path.write_bytes(await image.read())

        items = parse_receipt_image(
            saved_path, settings
        )

        if not items:
            return {
                "success": False,
                "error": (
                    "Could not extract items from receipt. "
                    "Try a clearer photo."
                ),
                "items": [],
            }

        db = SessionLocal()
        try:
            result = merge_receipt_items(
                db=db,
                household_id=1,
                items=items,
            )
        finally:
            db.close()

        return {
            "success": True,
            "items": items,
            "added": result["added"],
            "updated": result["updated"],
            "total": len(items),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if saved_path and saved_path.exists():
            saved_path.unlink(missing_ok=True)
