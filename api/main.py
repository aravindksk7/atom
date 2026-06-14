from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os

from etl_framework.repository.database import init_db
from etl_framework.utils.logging import configure_logging
from etl_framework.utils.tracing import configure_tracing
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes

app = FastAPI(
    title="ETL Framework API",
    description="Manage, configure, run, and monitor ETL reconciliation tests",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(configs.router, prefix="/api/configs")
app.include_router(runs.router, prefix="/api/runs")
app.include_router(jobs.router, prefix="/api/jobs")
app.include_router(health_routes.router, prefix="/api/health")
app.include_router(adapters.router, prefix="/api/adapters")
app.include_router(compare_routes.router, prefix="/api/compare")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.on_event("startup")
def on_startup():
    configure_logging()
    configure_tracing(enabled=False)
    init_db()


# Serve frontend static files if the folder exists
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="static")
