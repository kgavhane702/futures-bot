from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ui.controllers.dashboard import router as dashboard_router


app = FastAPI(title="Futures Bot UI")
app.mount("/static", StaticFiles(directory="ui/views/static"), name="static")
app.include_router(dashboard_router)


