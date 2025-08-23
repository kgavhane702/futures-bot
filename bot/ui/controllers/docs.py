from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()
templates = Jinja2Templates(directory="bot/ui/views/templates")


@router.get("/docs", response_class=HTMLResponse)
def docs_index(request: Request):
    return templates.TemplateResponse("docs/index.html", {"request": request})


@router.get("/docs/strategies", response_class=HTMLResponse)
def docs_strategies(request: Request):
    return templates.TemplateResponse("docs/strategies.html", {"request": request})


@router.get("/docs/strategies/{strategy_id}", response_class=HTMLResponse)
def docs_strategy(request: Request, strategy_id: str):
    # Map known strategies to templates; fallback to strategies.html
    tpl_map = {
        "mtf_ema_rsi_adx": "docs/strategy_mtf_ema_rsi_adx.html",
        "breakout": "docs/strategy_breakout.html",
    }
    tpl = tpl_map.get(strategy_id.lower(), "docs/strategies.html")
    return templates.TemplateResponse(tpl, {"request": request, "strategy_id": strategy_id})


