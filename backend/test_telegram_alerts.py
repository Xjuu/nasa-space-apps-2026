from fastapi import FastAPI
from fastapi.testclient import TestClient

from . import telegram_alerts as telegram


def client():
    app = FastAPI()
    app.include_router(telegram.router)
    return TestClient(app)


def test_missing_configuration_never_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram, "_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    send = lambda *args: (_ for _ in ()).throw(AssertionError("must not send"))
    monkeypatch.setattr(telegram, "_send_message", send)
    api = client()

    assert api.get("/telegram/config").json()["configured"] is False
    response = api.post("/telegram/send", json={
        "area_name": "Test area", "lat": 51.5, "lon": -0.1, "radius_km": 10,
    })
    assert response.status_code == 503
    assert "TELEGRAM_BOT_TOKEN" in response.json()["detail"]


def test_explicit_area_submission_sends_only_configured_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram, "_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(telegram, "_LAST_SENT", 0.0)
    monkeypatch.setattr(telegram, "_SENDING", False)
    sent = []
    monkeypatch.setattr(telegram, "_send_message", lambda *args: sent.append(args))
    api = client()

    status = api.get("/telegram/config").json()
    response = api.post("/telegram/send", json={
        "area_name": "North ridge", "lat": 51.5, "lon": -0.1, "radius_km": 10,
    })

    assert status == {
        "configured": True,
        "message": "A notification is sent only after you submit an area and click Send.",
    }
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert len(sent) == 1
    assert sent[0][:2] == ("test-token", "test-chat")
    assert "North ridge" in sent[0][2]
    assert "not an automated fire alert" in sent[0][2]


def test_invalid_area_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram, "_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    response = client().post("/telegram/send", json={
        "area_name": "Test area", "lat": 91, "lon": 0, "radius_km": 10,
    })
    assert response.status_code == 422
