"""Short-lived, public read-only links for small forecast summaries."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BODY_BYTES = 64 * 1024
TTL_SECONDS = 24 * 60 * 60
MAX_SHARES = 500
DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "shared_analysis.sqlite3"


class LimitedBodyRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def limited(request: Request):
            size, parts = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise HTTPException(413, "Share summary exceeds 64 KiB.")
                parts.append(chunk)
            request._body = b"".join(parts)
            return await handler(request)

        return limited


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShareLocation(StrictModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=120)


class ShareQuery(StrictModel):
    start_utc: str | None = Field(default=None, max_length=40)
    days: int | None = Field(default=None, ge=1, le=30)
    fuel: str | None = Field(default=None, max_length=80)
    ignition_source: str | None = Field(default=None, max_length=120)
    selection_objective: str | None = Field(default=None, max_length=120)


class ShareModel(StrictModel):
    blender: str | None = Field(default=None, max_length=120)
    fit_year: int | None = Field(default=None, ge=1900, le=2200)


class ShareHistory(StrictModel):
    reduced: bool | None = None
    as_of_utc: str | None = Field(default=None, max_length=40)
    ignition_source: str | None = Field(default=None, max_length=120)
    ignition_shifted_days: int | None = Field(default=None, ge=0, le=365)


class ShareSummary(StrictModel):
    burned_km2: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_ros_m_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    frames: int | None = Field(default=None, ge=0, le=1000)


class ShareSnapshot(StrictModel):
    version: int = Field(ge=1, le=1)
    created_utc: str = Field(max_length=40)
    mode: str = Field(min_length=1, max_length=80)
    location: ShareLocation
    query: ShareQuery
    model: ShareModel
    history: ShareHistory
    summary: ShareSummary
    warnings: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, warnings: list[str]) -> list[str]:
        if any(len(warning) > 500 for warning in warnings):
            raise ValueError("Each warning must be at most 500 characters.")
        return warnings


router = APIRouter(prefix="/share", tags=["shared analysis"], route_class=LimitedBodyRoute)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS shared_analysis ("
        "id TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL, payload TEXT)"
    )
    return connection


@router.post("")
def create_share(snapshot: ShareSnapshot):
    payload = snapshot.model_dump(exclude_none=True)
    now = time.time()
    share_id = secrets.token_urlsafe(16)
    expires_epoch = now + TTL_SECONDS
    with closing(_connect()) as connection:
        with connection:
            connection.execute("UPDATE shared_analysis SET payload = NULL WHERE expires_at <= ? AND payload IS NOT NULL", (now,))
            connection.execute("DELETE FROM shared_analysis WHERE expires_at <= ?", (now - TTL_SECONDS,))
            connection.execute(
                "INSERT INTO shared_analysis (id, created_at, expires_at, payload) VALUES (?, ?, ?, ?)",
                (share_id, now, expires_epoch, json.dumps(payload, separators=(",", ":"), allow_nan=False)),
            )
            connection.execute(
                "DELETE FROM shared_analysis WHERE id IN ("
                "SELECT id FROM shared_analysis ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (MAX_SHARES,),
            )
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)).isoformat()
    return {"id": share_id, "expires_at": expires_at, "ttl_seconds": TTL_SECONDS}


@router.get("/{share_id}")
def get_share(share_id: str):
    if len(share_id) > 64 or not share_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(404, "Share link not found.")
    now = time.time()
    with closing(_connect()) as connection:
        with connection:
            connection.execute("UPDATE shared_analysis SET payload = NULL WHERE expires_at <= ? AND payload IS NOT NULL", (now,))
            connection.execute("DELETE FROM shared_analysis WHERE expires_at <= ?", (now - TTL_SECONDS,))
            row = connection.execute(
                "SELECT expires_at, payload FROM shared_analysis WHERE id = ?", (share_id,)
            ).fetchone()
    if row is None:
        raise HTTPException(404, "Share link not found.")
    if row[0] <= now or row[1] is None:
        raise HTTPException(410, "Share link expired.")
    return json.loads(row[1])
