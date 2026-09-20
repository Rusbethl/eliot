from __future__ import annotations

import os
import signal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("/quit")
def quit_server() -> JSONResponse:
    """Local-tool convenience: server shuts itself down on POST /quit."""
    os.kill(os.getpid(), signal.SIGINT)
    return JSONResponse({"status": "shutting down"})
