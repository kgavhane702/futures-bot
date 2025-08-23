from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .controllers.dashboard import router as dashboard_router
from .controllers.docs import router as docs_router


app = FastAPI(title="Futures Bot UI", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory="bot/ui/views/static"), name="static")
app.include_router(dashboard_router)
app.include_router(docs_router)


