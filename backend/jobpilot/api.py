from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .company_api import router as company_router
from .config import settings
from .db import SessionLocal, session
from .history_api import router as history_router
from .http import authenticate, serialize as serialize
from .job_api import router as job_router
from .linkedin_api import router as linkedin_router
from .linkedin_jobs import router as linkedin_jobs_router
from .profile_api import router as profile_router
from .resume_api import router as resume_router
from .search_api import router as search_router
from .services import (
    bootstrap,
)
from .source_api import router as source_router
from .notifications import router as notification_router
from .top_vacancies import router as top_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings().hosted_mode and not settings().auth_required:
        raise RuntimeError("Hosted mode requires AUTH_REQUIRED=true")
    if settings().auth_required and len(settings().api_token.get_secret_value()) < 32:
        raise RuntimeError("API_TOKEN must be at least 32 characters")
    settings().data_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        bootstrap(db)
        from .companies import seed_companies
        from .search_setup import ensure_resume_searches
        ensure_resume_searches(db)
        seed_companies(db)
        db.commit()
    from .search_runtime import SearchRuntime
    runtime = SearchRuntime() if settings().embedded_searches else None
    app.state.search_runtime = runtime
    if runtime:
        runtime.start()
    try:
        yield
    finally:
        if runtime:
            runtime.stop()


app = FastAPI(title="JobPilot", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(db: Session = Depends(session)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        if not settings().embedded_searches:
            from redis import Redis
            Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception as exc:
        raise HTTPException(503, "Dependency unavailable") from exc
    return {"status": "ready"}


app.include_router(profile_router)
app.include_router(resume_router)
app.include_router(source_router)
app.include_router(job_router)
app.include_router(history_router)
app.include_router(search_router)
app.include_router(linkedin_router)
app.include_router(linkedin_jobs_router)
app.include_router(company_router)
app.include_router(notification_router)
app.include_router(top_router)


@app.get("/release", dependencies=[Depends(authenticate)])
def release() -> dict[str, str | int]:
    import os
    from .matching import SCORING_VERSION
    return {"commit": os.environ.get("RENDER_GIT_COMMIT", "local"), "top_window_hours": 72,
            "top_min_score": 60, "scoring_version": SCORING_VERSION}
