# FridgeChef — AI-Powered Recipe Generation Platform

**Live demo:** [fride-project-production.up.railway.app](https://fride-project-production.up.railway.app)

FridgeChef turns your fridge into a cooking workflow. Scan a fridge photo, cook from saved inventory, scan grocery receipts, stream recipes in real time, estimate nutrition, and manage your kitchen — all from a single dark-mode web app powered by Claude Vision and GPT-4o-mini.

---

## What It Does

- **Fridge scan** — upload a photo, Claude Vision identifies every ingredient and streams a personalized recipe live
- **Cook from inventory** — generate recipes from your existing kitchen stock without a photo
- **Receipt scanner** — photograph a grocery receipt, items are extracted via OCR and merged into inventory automatically
- **Persistent personalization** — dietary profile, macro goals, and skill level shape every recipe
- **Nutrition analysis** — instant macro breakdown (calories, protein, carbs, fat, fiber) powered by GPT-4o-mini
- **Expiry tracking** — inventory items are assigned shelf-life estimates and sorted by urgency
- **Saved recipes** — slide-out drawer with browser-persisted recipe collection

---

## Architecture

```
Browser (Vanilla JS + SSE client)
        │
        ▼
FastAPI (Uvicorn ASGI server)
        │
        ├── /stream ──────────────► Thread pool
        │                           ├── Anthropic Claude API (vision + streaming)
        │                           └── asyncio.Queue (thread→async bridge)
        │
        ├── /nutrition ───────────► OpenAI GPT-4o-mini
        ├── /scan-receipt ────────► Claude Vision (receipt OCR + JSON extraction)
        ├── /stream-from-inventory► Claude text-only (inventory context)
        └── /inventory, /profile ─► SQLAlchemy → SQLite
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| ORM / DB | SQLAlchemy 2.0, SQLite (PostgreSQL-ready) |
| AI — Vision & Recipes | Anthropic Claude claude-sonnet-4-5 |
| AI — Nutrition | OpenAI GPT-4o-mini |
| Images | Unsplash API |
| Frontend | Vanilla JavaScript, SSE, Marked.js, Jinja2 |
| Deployment | Railway, Nixpacks, GitHub CI/CD |

---

## Key Technical Decisions

### Multi-Provider Cost Optimization
Claude handles vision tasks — best multimodal quality, justified at ~$0.02/scan. GPT-4o-mini handles nutrition calculation — simple text task at ~$0.001/call, 20x cheaper than Claude for equivalent output. Each provider is used exactly where its cost/quality ratio is optimal.

### Thread-to-Async Bridge for Streaming
Claude's Python SDK is synchronous. FastAPI runs on an async event loop. Spawning a blocking thread directly would freeze other requests. Solution: a daemon thread runs the blocking stream and posts chunks into an `asyncio.Queue` via `run_coroutine_threadsafe()`. The async generator reads from the queue and yields SSE events — true streaming without blocking.

### SSE Keepalive Through Reverse Proxies
Railway's proxy layer terminates idle SSE connections during Claude's initial thinking period (5–15 seconds of silence). Solution: `asyncio.wait_for()` with a 15-second timeout — on timeout, yields a `{"ping": true}` SSE event the browser silently ignores. Keeps the TCP connection alive indefinitely.

### Stateless Persistent Personalization
Claude has no memory between sessions by design. The app creates the illusion of persistent AI personalization by building a structured context string from the database on every call — inventory sorted by expiry urgency, dietary profile, macro goals — injected into the user message. The AI appears to "know" the user without any stateful model.

### Fuzzy Ingredient Deduplication
Three input sources (fridge scan, receipt scan, manual add) can create the same ingredient. Exact-match dedup fails because "bell pepper" and "red bell pepper" should be treated as the same item. Solution: bidirectional substring containment — `ing_lower in existing or existing in ing_lower`.

### Progressive Markdown Streaming
Streaming raw markdown chunks shows syntax characters mid-render. Buffering until completion creates a blank loading period. Solution: re-render the full accumulated buffer through `marked.parse()` every 200 characters — fast enough to feel live, slow enough to avoid syntax flicker.

### Single-File Frontend Architecture
All 3,800 lines of HTML, CSS, and JavaScript live in one Jinja2 template. Every screen exists simultaneously in the DOM, toggled by a `showScreen()` function — instant transitions with no page reloads, no client-side router, no build step.

---

## Project Structure

```
fridgechef/
├── app/
│   ├── main.py          ~900 lines  — all routes and endpoints
│   ├── llm.py           ~350 lines  — Claude + OpenAI integrations
│   ├── database.py      ~120 lines  — SQLAlchemy models
│   ├── inventory.py     ~280 lines  — inventory logic and deduplication
│   ├── images.py        ~40 lines   — Unsplash integration
│   └── config.py        ~30 lines   — Pydantic settings
├── templates/
│   └── index.html       ~3,800 lines — entire frontend
├── static/              — CSS assets
├── requirements.txt
├── railway.json
└── runtime.txt

Total: ~5,500 lines of code
```

---

## Local Setup

### Requirements
- Python 3.11
- Anthropic API key
- OpenAI API key
- Unsplash access key (optional — recipes still work without it)

### Install

```bash
git clone https://github.com/shubhamjoshipromail-svg/Fride-project
cd Fride-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-sonnet-4-5

OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini

UNSPLASH_ACCESS_KEY=your_unsplash_access_key_here
```

### Run

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Main Flows

### 1. Scan My Fridge
Upload a fridge photo, add preferences, and stream a recipe live into the recipe screen.

### 2. Cook From My Kitchen
Generate a recipe using existing inventory only — uses saved kitchen data, expiring items, and profile context already in the database.

### 3. Scan a Receipt
Upload a grocery receipt image to extract food items and merge them into inventory using category detection and fuzzy dedup logic.

### 4. Nutrition
On the recipe screen, click `calculate nutrition` to estimate calories, protein, carbs, fat, fiber, and servings.

### 5. Saved Recipes
Saved recipes persist per browser/device in `localStorage` without extra backend storage.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | App entry point |
| POST | `/stream` | Stream recipe from uploaded image |
| POST | `/stream-from-inventory` | Stream recipe from inventory only |
| POST | `/scan-receipt` | Parse receipt and merge into inventory |
| POST | `/nutrition` | Nutrition estimate for a recipe |
| GET | `/home-data` | Home screen snapshot |
| GET | `/inventory-full` | Kitchen inventory list |
| GET | `/profile` | Current cooking profile |
| POST | `/profile` | Update cooking profile |

---

## Database

Auto-initialized on startup. Core tables: `households`, `profiles`, `scans`, `inventory_items`, `recipes`.

SQLAlchemy ORM means swapping `DATABASE_URL` to PostgreSQL requires zero code changes.

---

## Deployment

Deployed on Railway with automatic CI/CD — every push to `main` triggers a redeploy via GitHub webhook. Zero manual deployment steps.

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

---

## Notes

- Receipt scanning quality depends on image clarity
- Inventory quality improves as more scans and receipts are added
- Older inventory rows created before parser improvements may have noisy names until replaced by newer scans

---

Built by [Shubham Joshi](https://www.linkedin.com/in/shubham-joshi1) — MS Business Analytics & AI, Johns Hopkins University
