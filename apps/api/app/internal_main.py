"""Internal ASGI app: `uvicorn app.internal_main:app --host <private-ip> --port 8001`."""

from fastapi import FastAPI

from app.api.internal import router
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging(get_settings().log_level)
app = FastAPI(title="DatabaseMax internal API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
