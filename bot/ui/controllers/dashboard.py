from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import os
from fastapi.templating import Jinja2Templates

from ...state import STATE


router = APIRouter()
templates = Jinja2Templates(directory="bot/ui/views/templates")


@router.get("/")
def root(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/positions")
def positions(request: Request):
    return templates.TemplateResponse("positions.html", {"request": request})


@router.get("/logs")
def logs(request: Request):
    return templates.TemplateResponse("logs.html", {"request": request})


@router.get("/stats")
def stats():
    return JSONResponse(STATE.snapshot())


@router.get("/settings")
def settings_get():
    from ...strategies.registry import available_strategy_ids
    settings = {
        "USE_TESTNET": os.getenv("USE_TESTNET", "true"),
        "DRY_RUN": os.getenv("DRY_RUN", "true"),
        "LEVERAGE": os.getenv("LEVERAGE", "3"),
        "UNIVERSE_SIZE": os.getenv("UNIVERSE_SIZE", "200"),
        "MAX_POSITIONS": os.getenv("MAX_POSITIONS", "3"),
        "STRATEGIES": os.getenv("STRATEGIES", "auto"),
        "available_strategies": available_strategy_ids(),
    }
    return JSONResponse(settings)


@router.post("/settings")
async def settings_post(request: Request):
    data = await request.json()
    # Allow-list of keys editable via UI
    allowed = {
        "USE_TESTNET", "DRY_RUN", "LEVERAGE", "UNIVERSE_SIZE", "MAX_POSITIONS", "STRATEGIES"
    }
    lines = []
    try:
        with open(".env", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        pass
    kv = {}
    for line in lines:
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v
    for k, v in data.items():
        if k in allowed and v is not None:
            kv[k] = str(v)
    new_lines = []
    for k, v in kv.items():
        new_lines.append(f"{k}={v}")
    # Ensure all allowed keys exist
    for a in allowed:
        if a not in kv:
            new_lines.append(f"{a}=")
    with open(".env", "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
    return JSONResponse({"ok": True})


