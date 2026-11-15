"""Explicit, user-triggered Telegram notifications for a submitted area."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/telegram", tags=["telegram"])
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_SEND_LOCK = threading.Lock()
_SENDING = False
_LAST_SENT = 0.0
_MIN_SEND_INTERVAL_S = 30.0


class AreaAlert(BaseModel):
    area_name: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0, le=250)


class TelegramDeliveryError(RuntimeError):
    pass


def _configured_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip() == name:
                return raw.strip().strip("\"'")
    except FileNotFoundError:
        return ""
    return ""


def _credentials() -> tuple[str, str] | None:
    token = _configured_value("TELEGRAM_BOT_TOKEN")
    chat_id = _configured_value("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return token, chat_id


def _send_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        raise TelegramDeliveryError("Telegram delivery failed. Check connectivity and try again.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramDeliveryError("Telegram did not accept the notification.")


@router.get("/config")
def telegram_config():
    """Return readiness only; credentials are never sent to the browser."""
    return {
        "configured": _credentials() is not None,
        "message": "A notification is sent only after you submit an area and click Send.",
    }


@router.post("/send")
def send_area_alert(body: AreaAlert):
    global _LAST_SENT, _SENDING
    area_name = " ".join(body.area_name.split())
    if not area_name:
        raise HTTPException(422, "Enter a non-empty area name.")
    credentials = _credentials()
    if credentials is None:
        raise HTTPException(503, "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the server environment or .env file.")
    now = time.monotonic()
    with _SEND_LOCK:
        if _SENDING or now - _LAST_SENT < _MIN_SEND_INTERVAL_S:
            raise HTTPException(429, "Please wait before sending another Telegram notification.")
        _SENDING = True
    message = (
        "FireSpot user-requested area notification\n"
        f"Area: {area_name}\n"
        f"Center: {body.lat:.5f}, {body.lon:.5f}\n"
        f"Radius: {body.radius_km:g} km\n"
        "This is a user-submitted area, not an automated fire alert."
    )
    try:
        _send_message(*credentials, message)
    except TelegramDeliveryError as error:
        raise HTTPException(502, str(error)) from None
    finally:
        with _SEND_LOCK:
            _SENDING = False
    with _SEND_LOCK:
        _LAST_SENT = time.monotonic()
    return {"status": "sent", "area": area_name}
