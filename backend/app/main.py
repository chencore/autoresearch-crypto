import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import http_exception_handler, unhandled_exception_handler
from app.services.data_download_manager import data_download_manager
from app.services.evolution_manager import evolution_manager

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="autoresearch-crypto-backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def _capture_event_loop() -> None:
    loop = asyncio.get_running_loop()
    evolution_manager.set_loop(loop)
    data_download_manager.set_loop(loop)


app.include_router(api_router, prefix="/api/v1")
