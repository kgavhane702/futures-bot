from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from state import STATE


router = APIRouter()
templates = Jinja2Templates(directory="ui/views/templates")


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


