from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from etl_framework.repository.database import init_db
from etl_framework.utils.logging import configure_logging
from etl_framework.utils.tracing import configure_tracing
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes
from api.routes import auth as auth_routes
from api.routes import audit as audit_routes
from api.routes import tokens, notifications, schedules, lineage as lineage_routes
from api.middleware.auth import BearerTokenMiddleware

app = FastAPI(
    title="ETL Framework API",
    description="Manage, configure, run, and monitor ETL reconciliation tests",
    version="2.0.0",
)

# CORS must be registered BEFORE auth middleware: Starlette applies middleware LIFO,
# so CORSMiddleware runs outermost and handles OPTIONS preflights before BearerTokenMiddleware sees them.
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BearerTokenMiddleware)

app.include_router(configs.router, prefix="/api/configs")
app.include_router(runs.router, prefix="/api/runs")
app.include_router(jobs.router, prefix="/api/jobs")
app.include_router(health_routes.router, prefix="/api/health")
app.include_router(adapters.router, prefix="/api/adapters")
app.include_router(compare_routes.router, prefix="/api/compare")
app.include_router(auth_routes.router)
app.include_router(audit_routes.router, prefix="/api/audit")
app.include_router(tokens.router, prefix="/api/tokens")
app.include_router(notifications.router, prefix="/api/notifications")
app.include_router(schedules.router, prefix="/api/schedules")
app.include_router(lineage_routes.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.on_event("startup")
def on_startup():
    configure_logging()
    configure_tracing(enabled=False)
    init_db()
    from api.services import scheduler as _sched
    _sched.start()


@app.on_event("shutdown")
def on_shutdown():
    from api.services import scheduler as _sched
    _sched.stop()


# Serve frontend static files if the folder exists
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="static")
