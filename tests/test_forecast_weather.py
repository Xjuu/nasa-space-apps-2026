"""Offline chronology, coverage and provenance contracts for Single Runs weather."""
from datetime import timedelta
import json

import pytest

from equifire.forecast_weather import (AvailabilityEvidence, DAILY, EXPECTED_UNITS, HOURLY, ForecastWeather,
                                      ForecastWeatherError, daily_tuple, guard_run, iso, parse_payload, select_run, utc)


def response_payload(init="2025-05-27T00:00:00Z", days=2):
    start = utc(init)
    hourly = {"time": [(start+timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(days*24)]}
    for variable, value in zip(HOURLY, (12, 270, 20, 40, 0)):
        hourly[variable] = [value]*(days*24)
    daily = {"time": [(start+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]}
    for variable, value in zip(DAILY, (12, 270, 20, 40, 0)):
        daily[variable] = [value]*days
    return {"latitude": 55, "longitude": -101, "utc_offset_seconds": 0,
            "hourly_units": {"time": "iso8601", **EXPECTED_UNITS}, "hourly": hourly,
            "daily_units": dict(zip(DAILY, ("km/h", "°", "°C", "%", "mm"))), "daily": daily}


def research_meta():
    return guard_run("ecmwf_ifs", "2025-05-27T00:00:00Z", "2025-05-28T00:00:00Z", require_as_issued=False)


class Session:
    def __init__(self, payload=None, status=200):
        self.payload, self.status, self.calls = payload or response_payload(), status, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outer = self

        class Response:
            status_code = outer.status

            def json(self):
                return outer.payload

        return Response()


def test_historical_hindcast_refused_in_strict_mode_before_http(tmp_path):
    session = Session()
    provider = ForecastWeather(tmp_path, session=session)
    with pytest.raises(ForecastWeatherError, match="As-issued input refused"):
        provider.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z")
    assert not session.calls
    assert provider.requests_made == 0


@pytest.mark.parametrize("model,init,cutoff,match", [
    ("ecmwf_ifs", "2025-05-28T06:00Z", "2025-05-28T00:00Z", "exceeds"),
    ("gfs_global", "2025-05-27T00:00Z", "2025-05-28T00:00Z", "coverage"),
    ("best_match", "2025-05-27T00:00Z", "2025-05-28T00:00Z", "explicit"),
    ("ecmwf_ifs", "2024-03-13T00:00Z", "2024-03-14T00:00Z", "coverage"),
    ("ecmwf_ifs", "2025-05-27T03:00Z", "2025-05-28T00:00Z", "cycle"),
])
def test_no_future_unknown_model_or_unsupported_archive(model, init, cutoff, match):
    with pytest.raises(ForecastWeatherError, match=match):
        guard_run(model, init, cutoff, require_as_issued=False)


def test_initialisation_is_not_publication():
    init, cutoff = "2026-06-01T00:00Z", "2026-06-01T06:00Z"
    with pytest.raises(ForecastWeatherError, match="As-issued input refused"):
        guard_run("gfs_global", init, cutoff)
    late = AvailabilityEvidence("gfs_global", init, "2026-06-01T07:00Z", "poll-7")
    with pytest.raises(ForecastWeatherError, match="not yet available"):
        guard_run("gfs_global", init, cutoff, late)
    good = AvailabilityEvidence("gfs_global", init, "2026-06-01T05:30Z", "retained-poll-5:30")
    result = guard_run("gfs_global", init, cutoff, good)
    assert result["as_issued_eligible"]
    assert result["available_at_utc"] == "2026-06-01T05:30:00Z"
    assert not result["run_init_returned_by_api"]


def test_availability_record_cannot_launder_old_hindcast():
    evidence = AvailabilityEvidence("ecmwf_ifs", "2025-05-27T00:00Z", "2025-05-27T06:00Z", "asserted-record")
    with pytest.raises(ForecastWeatherError, match="As-issued input refused"):
        guard_run("ecmwf_ifs", evidence.run_init, "2025-05-28T00:00Z", evidence)


@pytest.mark.parametrize("evidence", [
    AvailabilityEvidence("gfs_global", "2026-06-01T00:00Z", "2026-06-01T06:00Z", "other-model"),
    AvailabilityEvidence("ecmwf_ifs", "2026-06-01T06:00Z", "2026-06-01T07:00Z", "other-init"),
    AvailabilityEvidence("ecmwf_ifs", "2026-06-01T00:00Z", "2026-05-31T23:00Z", "before-init"),
    AvailabilityEvidence("ecmwf_ifs", "2026-06-01T00:00Z", "2026-06-01T06:00Z", "", "current_metadata"),
])
def test_inconsistent_availability_evidence_rejected(evidence):
    with pytest.raises(ForecastWeatherError):
        guard_run("ecmwf_ifs", "2026-06-01T00:00Z", "2026-06-01T12:00Z", evidence)


def test_utc_and_latency_selection():
    assert iso(select_run("2025-05-28T02:00+02:00")) == "2025-05-27T12:00:00Z"
    with pytest.raises(ValueError, match="timezone"):
        utc("2025-05-28T00:00")
    with pytest.raises(ValueError, match="at least six"):
        select_run("2025-05-28T00:00Z", 0)


def test_coverage_lead_times_and_strict_feature_access():
    run = parse_payload(response_payload(), research_meta(), 2)
    assert run["hourly"][24]["lead_hours_from_init"] == 24
    assert run["hourly"][24]["lead_hours_from_cutoff"] == 0
    assert run["metadata"]["coverage"]["complete_days"] == 2
    assert daily_tuple(run, "2025-05-28", require_as_issued=False) == (12, 270, 20, 40, 0)
    with pytest.raises(ForecastWeatherError, match="cannot supply as-issued"):
        daily_tuple(run, "2025-05-28")


def test_missing_hour_does_not_become_zero_or_complete_day():
    payload = response_payload()
    payload["hourly"]["precipitation"][24] = None
    run = parse_payload(payload, research_meta(), 2)
    assert run["daily"][1]["values"] is None
    assert run["hourly"][24]["precipitation"] is None
    with pytest.raises(ForecastWeatherError, match="complete 24-hour"):
        daily_tuple(run, "2025-05-28", require_as_issued=False)


def test_empty_forecast_refused_and_missing_valid_date_not_filled():
    payload = response_payload()
    for name in HOURLY:
        payload["hourly"][name] = [None]*48
    with pytest.raises(ForecastWeatherError, match="no complete finite"):
        parse_payload(payload, research_meta(), 2)
    run = parse_payload(response_payload(), research_meta(), 2)
    with pytest.raises(ForecastWeatherError, match="complete 24-hour"):
        daily_tuple(run, "2025-05-29", require_as_issued=False)


@pytest.mark.parametrize("mutation", [
    lambda p: p["hourly_units"].update(wind_speed_10m="m/s"),
    lambda p: p["daily_units"].update(temperature_2m_max="°F"),
    lambda p: p["hourly"]["time"].pop(),
    lambda p: p["hourly"].update(wind_speed_10m=[]),
    lambda p: p.update(utc_offset_seconds=3600),
    lambda p: p["daily"].update(temperature_2m_max=[]),
])
def test_units_and_horizon_mismatch_rejected(mutation):
    payload = response_payload()
    mutation(payload)
    with pytest.raises(ForecastWeatherError):
        parse_payload(payload, research_meta(), 2)


def test_atomic_cache_is_offline_reusable_and_rechecks_cutoff(tmp_path):
    session = Session()
    provider = ForecastWeather(tmp_path, session=session)
    a = provider.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z", forecast_days=2, require_as_issued=False)
    assert not a["metadata"]["cache_hit"]
    assert provider.requests_made == 1
    assert session.calls[0][0].startswith("https://single-runs-api.")
    offline = ForecastWeather(tmp_path, max_requests=0, session=session)
    b = offline.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z", forecast_days=2, require_as_issued=False)
    assert b["metadata"]["cache_hit"] and len(session.calls) == 1
    with pytest.raises(ForecastWeatherError, match="exceeds"):
        offline.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-26T00:00Z", forecast_days=2, require_as_issued=False)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    record["response"]["hourly"]["temperature_2m"][0] = 999
    path.write_text(json.dumps(record))
    with pytest.raises(ForecastWeatherError, match="checksum"):
        offline.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z", forecast_days=2, require_as_issued=False)


def test_budget_and_rate_limit_have_no_fallback(tmp_path):
    provider = ForecastWeather(tmp_path, max_requests=0, session=Session())
    with pytest.raises(ForecastWeatherError, match="budget exhausted"):
        provider.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z", forecast_days=2, require_as_issued=False)
    provider = ForecastWeather(tmp_path, session=Session({"reason": "Too many requests"}, status=429))
    with pytest.raises(ForecastWeatherError, match="HTTP 429"):
        provider.fetch(55, -101, "2025-05-27T00:00Z", "2025-05-28T00:00Z", forecast_days=2, require_as_issued=False)
    assert provider.requests_made == 1
    assert not list(tmp_path.glob("*.json"))


def test_daily_api_midnight_constraint_guarded_without_http(tmp_path):
    session = Session()
    provider = ForecastWeather(tmp_path, session=session)
    with pytest.raises(ForecastWeatherError, match="00UTC"):
        provider.fetch(55, -101, "2025-05-27T12:00Z", "2025-05-28T00:00Z", require_as_issued=False)
    assert not session.calls
