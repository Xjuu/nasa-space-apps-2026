"""Isolated archived-single-run weather reader with explicit availability guards.

Historical Forecast's stitched first hours are NOT accepted here. In particular,
Open-Meteo documents pre-May-2026 IFS HRES as Cycle49R1 hindcasts: a historical
initialisation timestamp alone does not make those forecasts historically issued.
This module changes no production provider or model. It never falls back to ERA5.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

UTC = timezone.utc
ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"
DOCS = "https://open-meteo.com/en/docs/single-runs-api"
HOURLY = ("wind_speed_10m", "wind_direction_10m", "temperature_2m", "relative_humidity_2m", "precipitation")
DAILY = ("wind_speed_10m_max", "wind_direction_10m_dominant", "temperature_2m_max", "relative_humidity_2m_min", "precipitation_sum")
EXPECTED_UNITS = dict(zip(HOURLY, ("km/h", "°", "°C", "%", "mm")))
PROFILES = {
    "ecmwf_ifs": {"start": "2024-03-14T00:00:00Z", "operational_from": "2026-05-12T06:00:00Z",
                  "past_kind": "cycle49r1_hindcast_or_unverified_operational", "cycle": "49R1 before 2026-05-12T06:00Z; 50R1 afterwards"},
    "gfs_global": {"start": "2026-04-02T00:00:00Z", "operational_from": "2026-04-02T00:00:00Z",
                   "past_kind": "unsupported", "cycle": "not supplied by response"},
}


class ForecastWeatherError(RuntimeError):
    """Unavailable, unsupported, incomplete, or unsafe forecast input."""


def utc(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required.")
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def select_run(cutoff: str | datetime, assumed_latency_hours: float = 12) -> datetime:
    """Conservative scheduling assumption; NOT proof of historical availability."""
    if not math.isfinite(assumed_latency_hours) or assumed_latency_hours < 6:
        raise ValueError("Assumed global-model latency must be at least six hours.")
    limit = utc(cutoff) - timedelta(hours=assumed_latency_hours)
    return limit.replace(hour=(limit.hour // 6) * 6, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class AvailabilityEvidence:
    """A trusted external record of API availability, not a current reconstruction.

    The caller must retain the referenced poll/metadata record. This reader checks
    its consistency; it cannot authenticate an arbitrary externally supplied record.
    """
    model: str
    run_init: str
    available_at: str
    record_id: str
    source: str = "recorded_api_availability"


def guard_run(model: str, run_init: str | datetime, cutoff: str | datetime,
              evidence: AvailabilityEvidence | None = None, require_as_issued: bool = True) -> dict:
    init, boundary = utc(run_init), utc(cutoff)
    if model not in PROFILES:
        raise ForecastWeatherError("An explicit supported single model is required; no best_match or seamless.")
    if init > boundary:
        raise ForecastWeatherError("Run initialisation exceeds the information cutoff.")
    if init.hour not in (0, 6, 12, 18) or any((init.minute, init.second, init.microsecond)):
        raise ForecastWeatherError("Run does not match a six-hour UTC initialisation cycle.")
    profile = PROFILES[model]
    if init < utc(profile["start"]):
        raise ForecastWeatherError(f"No documented single-run coverage for {model} at {iso(init)}.")
    archive_kind = "operational_archive" if init >= utc(profile["operational_from"]) else profile["past_kind"]
    available, evidence_id = None, None
    if evidence is not None:
        if evidence.model != model or utc(evidence.run_init) != init:
            raise ForecastWeatherError("Availability record belongs to a different run/model.")
        if evidence.source != "recorded_api_availability" or not evidence.record_id.strip():
            raise ForecastWeatherError("A retained API-availability record is required.")
        available = utc(evidence.available_at)
        if available < init:
            raise ForecastWeatherError("Availability precedes initialisation.")
        if available > boundary:
            raise ForecastWeatherError("Run was not yet available at the information cutoff.")
        evidence_id = evidence.record_id
    eligible = archive_kind == "operational_archive" and available is not None
    if require_as_issued and not eligible:
        raise ForecastWeatherError("As-issued input refused: hindcast/unverified archive or missing historical availability evidence.")
    return {"model": model, "requested_run_init_utc": iso(init), "information_cutoff_utc": iso(boundary),
            "run_init_returned_by_api": False, "archive_kind": archive_kind, "model_cycle_note": profile["cycle"],
            "available_at_utc": iso(available) if available else None, "availability_record_id": evidence_id,
            "as_issued_eligible": eligible, "no_future_run": True,
            "availability_limit": "An assumed latency or current metadata is not historical availability evidence.",
            "source": ENDPOINT, "documentation": DOCS}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_payload(payload: dict, metadata: dict, forecast_days: int) -> dict:
    """Validate time/unit/coverage invariants without filling missing values."""
    if payload.get("utc_offset_seconds") != 0:
        raise ForecastWeatherError("Non-UTC response cannot be used in this UTC feature protocol.")
    units = payload.get("hourly_units") or {}
    if any(units.get(k) != v for k, v in EXPECTED_UNITS.items()):
        raise ForecastWeatherError("Unexpected or missing hourly weather units.")
    data = payload.get("hourly") or {}
    times = data.get("time") or []
    init, cutoff = utc(metadata["requested_run_init_utc"]), utc(metadata["information_cutoff_utc"])
    expected = [init + timedelta(hours=h) for h in range(forecast_days * 24)]
    # Open-Meteo's timezone=GMT ISO strings lack an offset; UTC is explicit in the request/response.
    parsed = [utc(t + "Z" if len(t) == 16 else t) for t in times]
    if parsed != expected:
        raise ForecastWeatherError("Returned horizon is not the requested contiguous single-run horizon.")
    if any(len(data.get(k, [])) != len(times) for k in HOURLY):
        raise ForecastWeatherError("Hourly variables have inconsistent coverage.")
    hourly = []
    for i, t in enumerate(parsed):
        values = {k: data[k][i] for k in HOURLY}
        hourly.append({"time_utc": iso(t), "lead_hours_from_init": (t-init).total_seconds()/3600,
                       "lead_hours_from_cutoff": (t-cutoff).total_seconds()/3600, **values,
                       "complete": all(_finite(v) for v in values.values())})
    daily_data = payload.get("daily") or {}
    daily_units = payload.get("daily_units") or {}
    expected_daily = dict(zip(DAILY, ("km/h", "°", "°C", "%", "mm")))
    if any(daily_units.get(k) != v for k, v in expected_daily.items()):
        raise ForecastWeatherError("Unexpected or missing daily weather units.")
    dates = daily_data.get("time") or []
    if any(len(daily_data.get(k, [])) != len(dates) for k in DAILY):
        raise ForecastWeatherError("Daily variables have inconsistent coverage.")
    daily = []
    for i, date in enumerate(dates):
        start = utc(date + "T00:00:00Z")
        hours = [r for r in hourly if start <= utc(r["time_utc"]) < start+timedelta(days=1)]
        values = [daily_data[k][i] for k in DAILY]
        complete = len(hours) == 24 and all(r["complete"] for r in hours) and all(_finite(v) for v in values)
        daily.append({"date": date, "values": values if complete else None, "complete": complete,
                      "hours_present": len(hours), "lead_start_hours_from_init": (start-init).total_seconds()/3600,
                      "lead_start_hours_from_cutoff": (start-cutoff).total_seconds()/3600,
                      "fields": list(DAILY)})
    if not hourly or not any(r["complete"] for r in hourly):
        raise ForecastWeatherError("The run contains no complete finite weather hours.")
    return {"metadata": {**metadata, "returned_grid": {k: payload.get(k) for k in ("latitude", "longitude", "elevation")},
                         "coverage": {"start_utc": hourly[0]["time_utc"], "end_utc": hourly[-1]["time_utc"],
                                      "hours": len(hourly), "complete_hours": sum(r["complete"] for r in hourly),
                                      "complete_days": sum(r["complete"] for r in daily)},
                         "units": units, "daily_units": daily_units}, "hourly": hourly, "daily": daily}


class ForecastWeather:
    def __init__(self, cache_dir: str | Path | None = None, max_requests: int = 10, session=None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parents[1]/"cache/forecast_weather"
        self.max_requests, self.requests_made = max_requests, 0
        if max_requests < 0 or max_requests > 29:
            raise ValueError("Use a bounded pilot budget of 0..29 HTTP requests.")
        self.session = session

    def fetch(self, lat: float, lon: float, run_init: str | datetime, cutoff: str | datetime, *,
              model: str = "ecmwf_ifs", forecast_days: int = 5,
              evidence: AvailabilityEvidence | None = None, require_as_issued: bool = True) -> dict:
        metadata = guard_run(model, run_init, cutoff, evidence, require_as_issued)
        if not (math.isfinite(lat) and -90 <= lat <= 90 and math.isfinite(lon) and -180 <= lon <= 180):
            raise ValueError("Invalid coordinates.")
        if not isinstance(forecast_days, int) or not 1 <= forecast_days <= 7:
            raise ValueError("Pilot horizon must be 1..7 days.")
        if utc(run_init).hour != 0:
            raise ForecastWeatherError("Single Runs daily variables require a 00UTC run; select an older midnight cycle.")
        params = {"latitude": lat, "longitude": lon, "models": model,
                  "run": utc(run_init).strftime("%Y-%m-%dT%H:%M"), "hourly": ",".join(HOURLY),
                  "daily": ",".join(DAILY), "forecast_days": forecast_days, "timezone": "GMT",
                  "temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"}
        tag = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / (tag + ".json")
        if path.exists():
            record = json.loads(path.read_text())
            if record.get("request") != params:
                raise ForecastWeatherError("Cached request provenance does not match.")
            payload = record["response"]
            content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if record.get("response_sha256") != content_hash:
                raise ForecastWeatherError("Cached response checksum mismatch.")
            cache_hit = True
        else:
            if self.requests_made >= self.max_requests:
                raise ForecastWeatherError("HTTP pilot budget exhausted; no fallback or retries.")
            if self.session is None:
                import requests
                self.session = requests.Session()
            self.requests_made += 1
            response = self.session.get(ENDPOINT, params=params, timeout=20)
            if response.status_code != 200:
                try:
                    detail = str(response.json().get("reason", ""))[:300]
                except (ValueError, AttributeError):
                    detail = ""
                raise ForecastWeatherError(f"Single Runs HTTP {response.status_code}; no fallback or retries. {detail}")
            payload = response.json()
            # Validate before caching; empty/incorrect responses cannot become successful cache hits.
            parse_payload(payload, metadata, forecast_days)
            record = {"request": params, "response": payload,
                      "retrieved_at_utc": iso(datetime.now(UTC)),
                      "response_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, delete=False) as handle:
                try:
                    json.dump(record, handle)
                    handle.close()
                    os.replace(handle.name, path)
                finally:
                    Path(handle.name).unlink(missing_ok=True)
            cache_hit = False
        metadata.update(request=params, response_sha256=record["response_sha256"],
                        retrieved_at_utc=record["retrieved_at_utc"], cache_hit=cache_hit)
        return parse_payload(payload, metadata, forecast_days)


def daily_tuple(run: dict, valid_date: str, *, require_as_issued: bool = True) -> tuple[float, ...]:
    if require_as_issued and not run["metadata"].get("as_issued_eligible"):
        raise ForecastWeatherError("Unverified/hindcast data cannot supply as-issued ML features.")
    row = next((r for r in run["daily"] if r["date"] == valid_date), None)
    if row is None or not row["complete"]:
        raise ForecastWeatherError("A complete 24-hour UTC day is required; no partial-day aggregation.")
    return tuple(float(v) for v in row["values"])
