"""
api.py
------
FastAPI entry point for the warehouse optimization engine.

Run locally:
    uvicorn api:app --reload --port 8000

Run on Render (start command):
    uvicorn api:app --host 0.0.0.0 --port $PORT

This file only exposes HTTP endpoints. All optimization logic (OR-Tools
CP-SAT batching, greedy / nearest-neighbor / 2-opt routing, CSV loading)
stays in ``src/`` and is reused as-is via ``src/api_service.py``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# The engine in src/ uses flat imports (``from batch_planner import ...``),
# so src/ has to be on sys.path. Resolving it from __file__ makes this work
# regardless of the working directory (local shell, Render, pytest, ...).
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from api_service import (  # noqa: E402
    DEFAULT_ALGORITHM,
    UnknownAlgorithmError,
    run_optimization,
)

logger = logging.getLogger("warehouse_api")

app = FastAPI(
    title="Warehouse Picking Optimization API",
    description="HTTP wrapper around the OR-Tools CP-SAT batching and "
                "greedy / nearest-neighbor / 2-opt routing engine in src/.",
    version="1.0.0",
)

# --------------------------------------------------------------------------- #
# CORS
#
# The dashboard is a separate static site (different origin), so the browser
# blocks its calls unless the API allows it. The API is read-only, has no
# cookies or auth, and serves bundled sample data, so "*" is safe as a
# default. To lock it down, set ALLOWED_ORIGINS on the backend service, e.g.
#   ALLOWED_ORIGINS=https://my-dashboard.onrender.com,http://localhost:5500
# --------------------------------------------------------------------------- #
_origins_env = os.environ.get("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _origins_env in ("", "*") else [
    o.strip().rstrip("/") for o in _origins_env.split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # must stay False when allow_origins is "*"
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class OptimizeRequest(BaseModel):
    """Optional request body for POST /optimize."""

    algorithm: Optional[str] = DEFAULT_ALGORITHM
    """One of: greedy, nearest_neighbor, 2-opt (``two_opt`` is also accepted)."""


@app.get("/")
def root() -> dict:
    """Service status and a map of the available endpoints."""
    return {
        "service": "warehouse-optimization-api",
        "status": "running",
        "version": app.version,
        "endpoints": {
            "health": "GET /health",
            "optimize": "POST /optimize",
            "docs": "GET /docs",
        },
        "algorithms": ["greedy", "nearest_neighbor", "2-opt"],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# A plain ``def`` (not ``async def``) on purpose: the engine is blocking and
# CPU-bound, so FastAPI runs it in a worker thread instead of freezing the
# event loop (which would also block /health).
@app.post("/optimize")
def optimize(body: Optional[OptimizeRequest] = None) -> dict:
    """
    Run the existing optimization pipeline on the bundled sample data and
    return the same data the dashboard renders, plus summary metrics.

    The body is optional; ``{}`` or no body uses the default (2-opt).
    """
    requested = body.algorithm if body is not None else None
    try:
        return run_optimization(requested)
    except UnknownAlgorithmError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (FileNotFoundError, ValueError) as exc:
        # Expected data problems raised by the engine (missing/bad CSVs, an
        # order too big for one tote, ...). The message is already user-safe.
        logger.error("Optimization failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Optimization failed: {exc}")
    except Exception:  # noqa: BLE001 - last-resort guard, details go to the log
        logger.exception("Unexpected error during optimization")
        raise HTTPException(status_code=500, detail="Unexpected error during optimization.")
