import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database import InventoryItem, Recipe, Scan


SHELF_LIFE = {
    "produce": 5,
    "dairy": 10,
    "meat": 3,
    "seafood": 2,
    "leftover": 4,
    "condiment": 90,
    "grain": 180,
    "other": 7,
}

CATEGORY_KEYWORDS = {
    "produce": [
        "spinach", "lettuce", "kale", "carrot", "pepper",
        "bell pepper", "onion", "garlic", "tomato", "cucumber",
        "zucchini", "broccoli", "mushroom", "celery", "herb",
        "dill", "parsley", "cilantro", "lemon", "lime",
        "apple", "banana", "berry", "fruit", "vegetable",
        "eggplant", "avocado", "corn", "pea", "bean",
    ],
    "dairy": [
        "milk", "cheese", "butter", "cream", "yogurt",
        "egg", "eggs", "sour cream", "cream cheese",
        "parmesan", "mozzarella", "cheddar", "feta",
    ],
    "meat": [
        "chicken", "beef", "pork", "lamb", "turkey",
        "bacon", "sausage", "ham", "steak", "ground beef",
        "ground turkey", "drumstick", "breast", "thigh",
    ],
    "seafood": [
        "fish", "salmon", "tuna", "shrimp", "prawn",
        "cod", "tilapia", "crab", "lobster", "scallop",
    ],
    "leftover": [
        "leftover", "tupperware", "container", "cooked",
        "prepared", "rice", "pasta", "soup", "stew",
    ],
    "condiment": [
        "sauce", "ketchup", "mustard", "mayo", "mayonnaise",
        "soy sauce", "hot sauce", "vinegar", "oil", "dressing",
        "jam", "jelly", "honey", "syrup",
    ],
    "grain": [
        "bread", "rice", "pasta", "flour", "oat", "cereal",
        "cracker", "tortilla", "noodle", "quinoa", "barley",
    ],
}


def detect_category(item_name: str) -> str:
    name_lower = item_name.lower()
    if any(keyword in name_lower for keyword in ["salt", "olive oil", "black pepper"]):
        return "condiment"
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in name_lower for keyword in keywords):
            return category
    return "other"


def estimate_expiry(category: str, date_added: datetime) -> tuple[datetime, int]:
    days = SHELF_LIFE.get(category, 7)
    expiry = date_added + timedelta(days=days)
    return expiry, days


def parse_ingredients_from_response(raw_response: str) -> list[str]:
    """
    Extract ingredient names from Claude's recipe response.
    Looks for the ingredient assessment section and bullet points.
    Returns a list of clean ingredient name strings.
    """
    ingredients = []
    lines = raw_response.split("\n")

    in_ingredients = False
    for line in lines:
        line = line.strip()
        normalized = line.lower().strip("# ").strip()

        if normalized == "ingredients" or any(keyword in normalized for keyword in [
            "ingredient assessment",
            "identified",
            "i can see",
            "visible ingredients",
            "spotted",
        ]):
            in_ingredients = True
            continue

        if in_ingredients and (
            normalized in {"instructions", "tips", "recipe", "steps"}
            or re.match(r"^\d+\.\s", line)
        ):
            in_ingredients = False

        if in_ingredients and line.startswith(("- ", "* ", "• ")):
            item = line.lstrip("-*• ").strip()
            item = item.replace("**", "")
            item = re.sub(r"^optional:\s*", "", item, flags=re.IGNORECASE)
            item = re.split(r"[,(]", item)[0].strip()
            item = re.sub(
                r"^\d+[\./]?\s*",
                "",
                item,
            ).strip()
            item = re.sub(
                r"^(large|small|medium|fresh|dried|whole|chopped|"
                r"minced|diced|sliced|optional|handful\s+of)\s+",
                "",
                item,
                flags=re.IGNORECASE,
            ).strip()
            item = re.sub(r"^\d+(?:/\d+)?\s*", "", item)
            item = re.sub(
                r"^(cups?|cup|tablespoons?|tbsp|teaspoons?|tsp|cloves?|clove|handful|handfuls)\s+",
                "",
                item,
                flags=re.IGNORECASE,
            ).strip()
            item = re.sub(
                r"^(small|medium|large|fresh|minced|diced|chopped|grated|shredded|sliced)\s+",
                "",
                item,
                flags=re.IGNORECASE,
            ).strip()
            item = re.sub(
                r"\d+[-–]\d+|\d+\s*(cups?|tbsp|tsp|oz|lbs?|kg|g)\s*",
                "",
                item,
                flags=re.IGNORECASE,
            ).strip()
            item = re.sub(r"\s+to taste$", "", item, flags=re.IGNORECASE).strip()
            item = item.strip()
            if item and len(item) > 2:
                ingredients.append(item)

    return ingredients


def save_scan_and_inventory(
    db: Session,
    household_id: int,
    raw_response: str,
    preferences_used: str,
) -> Scan:
    """
    Save a completed scan and populate inventory items from it.
    Merges with existing inventory - doesn't duplicate items.
    """
    scan = Scan(
        household_id=household_id,
        raw_response=raw_response,
        preferences_used=preferences_used,
    )
    db.add(scan)
    db.flush()

    ingredients = parse_ingredients_from_response(raw_response)

    existing_names = {
        item.name.lower()
        for item in db.query(InventoryItem)
        .filter_by(household_id=household_id)
        .filter(InventoryItem.status.in_(["fresh", "expiring_soon"]))
        .all()
    }

    now = datetime.utcnow()
    added_count = 0

    for ingredient in ingredients:
        ing_lower = ingredient.lower()
        is_duplicate = any(
            ing_lower in existing or existing in ing_lower
            for existing in existing_names
        )
        if is_duplicate:
            continue

        category = detect_category(ingredient)
        expiry_date, days_fresh = estimate_expiry(category, now)

        item = InventoryItem(
            household_id=household_id,
            scan_id=scan.id,
            name=ingredient,
            category=category,
            date_added=now,
            expiry_date=expiry_date,
            days_fresh_estimate=days_fresh,
            status="fresh",
        )
        db.add(item)
        existing_names.add(ingredient.lower())
        added_count += 1

    db.commit()
    db.refresh(scan)
    print(f"[FridgeChef] Scan saved. {added_count} new items added to inventory.")
    return scan


def merge_receipt_items(
    db: Session,
    household_id: int,
    items: list[dict],
) -> dict:
    """
    Add receipt items to inventory with smart merging.
    If item already exists: update quantity and
    reset expiry date (you just bought more).
    If new: add fresh.
    Returns counts of added vs updated items.
    """
    now = datetime.utcnow()
    added = 0
    updated = 0

    existing_items = (
        db.query(InventoryItem)
        .filter_by(household_id=household_id)
        .filter(
            InventoryItem.status.in_(
                ["fresh", "expiring_soon"]
            )
        )
        .all()
    )

    existing_map = {
        item.name.lower(): item
        for item in existing_items
    }

    for receipt_item in items:
        name = receipt_item.get("name", "").strip()
        quantity = receipt_item.get(
            "quantity", "some"
        )
        if not name or len(name) < 2:
            continue

        name_lower = name.lower()

        matched = None
        for existing_name, existing_item in existing_map.items():
            if (
                name_lower in existing_name
                or existing_name in name_lower
            ):
                matched = existing_item
                break

        if matched:
            category = matched.category
            expiry_date, days_fresh = estimate_expiry(
                category, now
            )
            matched.quantity = quantity
            matched.date_added = now
            matched.expiry_date = expiry_date
            matched.days_fresh_estimate = days_fresh
            matched.status = "fresh"
            updated += 1
        else:
            category = detect_category(name)
            expiry_date, days_fresh = estimate_expiry(
                category, now
            )
            new_item = InventoryItem(
                household_id=household_id,
                name=name,
                category=category,
                date_added=now,
                expiry_date=expiry_date,
                days_fresh_estimate=days_fresh,
                status="fresh",
                quantity=quantity,
            )
            db.add(new_item)
            existing_map[name_lower] = new_item
            added += 1

    db.commit()
    print(
        f"[FridgeChef] Receipt merged: "
        f"{added} added, {updated} updated"
    )
    return {"added": added, "updated": updated}


def save_recipe(
    db: Session,
    household_id: int,
    scan_id: int,
    title: str,
    markdown_content: str,
    preferences_used: str,
    image_url: str = None,
) -> Recipe:
    """Save a generated recipe linked to a scan."""
    recipe = Recipe(
        household_id=household_id,
        scan_id=scan_id,
        title=title,
        markdown_content=markdown_content,
        preferences_used=preferences_used,
        image_url=image_url,
    )
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    print(f"[FridgeChef] Recipe saved: {title}")
    return recipe


def get_inventory_context(db: Session, household_id: int) -> str:
    """
    Build a context string to inject into Claude prompts.
    Gives Claude awareness of current inventory state.
    """
    items = (
        db.query(InventoryItem)
        .filter_by(household_id=household_id)
        .filter(InventoryItem.status.in_(["fresh", "expiring_soon"]))
        .order_by(InventoryItem.expiry_date)
        .all()
    )

    if not items:
        return ""

    now = datetime.utcnow()
    expiring = []
    fresh = []

    for item in items:
        if item.expiry_date:
            days_left = (item.expiry_date - now).days
            if days_left <= 2:
                expiring.append(
                    f"{item.name} (expires in {days_left} days - USE FIRST)"
                )
            else:
                fresh.append(f"{item.name} ({days_left} days left)")
        else:
            fresh.append(item.name)

    context_parts = []
    if expiring:
        context_parts.append(
            "NEEDS USING URGENTLY:\n" +
            "\n".join(f"  - {item}" for item in expiring)
        )
    if fresh:
        context_parts.append(
            "Also available:\n" +
            "\n".join(f"  - {item}" for item in fresh)
        )

    return "\n".join(context_parts)


def update_item_statuses(db: Session, household_id: int):
    """
    Update statuses based on current date.
    Run this on each app startup and daily.
    """
    now = datetime.utcnow()
    items = (
        db.query(InventoryItem)
        .filter_by(household_id=household_id)
        .filter(InventoryItem.status.in_(["fresh", "expiring_soon"]))
        .all()
    )
    for item in items:
        if not item.expiry_date:
            continue
        days_left = (item.expiry_date - now).days
        if days_left < 0:
            item.status = "expired"
        elif days_left <= 2:
            item.status = "expiring_soon"
        else:
            item.status = "fresh"
    db.commit()
