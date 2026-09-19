"""Веб-консоль специалиста — минимальный каркас API.

uvicorn sandbox.console.app:app --host 0.0.0.0 --port 8080
Экраны и БД — этап 1. Сейчас сеансы живут в памяти процесса.
"""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="AR-песочница: консоль специалиста")
SESSIONS: dict[str, dict] = {}


class StartSession(BaseModel):
    child_code: str          # псевдоним, например "К-017"; имён нет
    therapist_id: int
    preset: str = "calm"
    planned_minutes: int = 20


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sensor": "unknown", "projector": "unknown", "disk_free_pct": None}


@app.post("/sessions/start")
def start(body: StartSession) -> dict:
    sid = "S-" + uuid.uuid4().hex[:8]
    SESSIONS[sid] = {"id": sid, **body.model_dump(), "started_at": time.time(), "events": [], "status": "running"}
    return SESSIONS[sid]


@app.post("/sessions/{sid}/marker")
def marker(sid: str, note: str = "") -> dict:
    s = SESSIONS.get(sid) or _404()
    s["events"].append({"t": time.time(), "type": "marker", "note": note})
    return {"ok": True, "events": len(s["events"])}


@app.post("/sessions/{sid}/end")
def end(sid: str) -> dict:
    s = SESSIONS.get(sid) or _404()
    s["status"] = "finished"
    s["ended_at"] = time.time()
    return s


@app.get("/sessions")
def list_sessions() -> list[dict]:
    return list(SESSIONS.values())


def _404():
    raise HTTPException(404, "сеанс не найден")
