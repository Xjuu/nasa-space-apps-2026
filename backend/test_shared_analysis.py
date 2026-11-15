from fastapi import FastAPI
from fastapi.testclient import TestClient

from . import shared_analysis as sharing


def client():
    app = FastAPI()
    app.include_router(sharing.router)
    return TestClient(app)


def snapshot():
    return {
        "version": 1,
        "created_utc": "2026-11-15T12:00:00Z",
        "mode": "Combined ML + physics forecast summary",
        "location": {"lat": 51.5, "lon": -0.1},
        "query": {"start_utc": "2026-11-15T12:00", "days": 3, "fuel": "temperate"},
        "model": {"blender": "blend", "fit_year": 2024},
        "history": {"reduced": False, "ignition_source": "VIIRS"},
        "summary": {"burned_km2": 1.25, "max_ros_m_min": 2.5, "frames": 24},
        "warnings": ["Experimental summary."],
    }


def use_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(sharing, "DB_PATH", tmp_path / "shared.sqlite3")


def test_share_returns_opaque_id_and_round_trips_validated_snapshot(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    api = client()
    response = api.post("/share", json=snapshot())
    assert response.status_code == 200
    result = response.json()
    assert len(result["id"]) >= 20
    assert result["ttl_seconds"] == sharing.TTL_SECONDS
    assert "location" not in result
    assert api.get(f"/share/{result['id']}").json()["summary"]["burned_km2"] == 1.25


def test_share_rejects_unapproved_fields_and_oversized_body(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    api = client()
    with_secret = snapshot()
    with_secret["token"] = "should not be stored"
    assert api.post("/share", json=with_secret).status_code == 422

    oversized = snapshot()
    oversized["warnings"] = ["x" * sharing.MAX_BODY_BYTES]
    assert api.post("/share", json=oversized).status_code == 413


def test_expired_share_returns_gone(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    api = client()
    monkeypatch.setattr(sharing.time, "time", lambda: 1_000.0)
    result = api.post("/share", json=snapshot()).json()
    monkeypatch.setattr(sharing.time, "time", lambda: 1_000.0 + sharing.TTL_SECONDS + 1)

    response = api.get(f"/share/{result['id']}")
    assert response.status_code == 410
    assert response.json()["detail"] == "Share link expired."


def test_unknown_share_is_not_confused_with_expired_share(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    response = client().get("/share/unknown-id")
    assert response.status_code == 404
    assert response.json()["detail"] == "Share link not found."


def test_routes_are_mounted_on_application(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("EQUIFIRE_NO_LOAD", "1")
    monkeypatch.setenv("EQUIFIRE_NO_FEEDBACK", "1")
    from .app import app

    api = TestClient(app)
    config = api.get("/telegram/config")
    assert config.status_code == 200
    assert "TELEGRAM_BOT_TOKEN" not in config.text
    created = api.post("/share", json=snapshot())
    assert created.status_code == 200
    share_id = created.json()["id"]
    assert api.get(f"/share/{share_id}").status_code == 200
