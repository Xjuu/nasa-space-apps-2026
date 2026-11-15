"""Offline contract, hostile-input and actual browser/PDF checks for saved analyses."""
import base64
import io
from pathlib import Path
import shutil
import struct
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend import analysis_reports as reports


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(reports.router)
    with TestClient(app) as session:
        yield session


def payload():
    return {"title": "Dzienne ryzyko ML + fizyka", "language": "pl", "generated_at": "2026-11-15T18:00:00Z",
            "context": [{"label": "Obszar", "value": "Flin Flon · 54,77° N"}],
            "blocks": [{"kind": "heading", "text": "Dane i ograniczenia"},
                       {"kind": "paragraph", "text": "Żółć, ogień i źródła. Kolory pokazują czas, nie pewność."},
                       {"kind": "table", "headers": ["Godzina UTC", "Powierzchnia km²"],
                        "rows": [["12:00", "1,25"], ["13:00", "2,50"]]}]}


def png_data(size=(30, 20)):
    out = io.BytesIO()
    Image.new("RGB", size, (20, 92, 100)).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_structured_text_is_escaped_everywhere():
    body = payload()
    attack = '<script>alert(1)</script><img src="file:///etc/passwd"><iframe src="https://example.com">'
    body["title"] = attack
    body["generated_at"] = attack
    body["context"] = [{"label": attack, "value": attack}]
    body["blocks"] = [{"kind": "paragraph", "text": attack}, {"kind": "table", "headers": [attack], "rows": [[attack]]},
                      {"kind": "figure", "png": png_data(), "caption": attack}]
    rendered = reports.render_html(reports.AnalysisReport.model_validate(body))
    assert "<script>" not in rendered
    assert '<img src="file:' not in rendered
    assert "<iframe" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "default-src 'none'" in rendered
    assert '<html lang="pl">' in rendered


@pytest.mark.parametrize("edit", [
    lambda x: x.update(title=""),
    lambda x: x.update(language="fr"),
    lambda x: x.update(blocks=[]),
    lambda x: x.update(blocks=[{"kind": "html", "text": "<b>bad</b>"}]),
    lambda x: x.update(blocks=[{"kind": "table", "headers": ["a", "b"], "rows": [["one"]]}]),
    lambda x: x.update(blocks=[{"kind": "table", "headers": ["a"], "rows": [["x"]] * 501}]),
    lambda x: x.update(blocks=[{"kind": "paragraph", "text": "x"}] * 121),
    lambda x: x.update(blocks=[{"kind": "figure", "png": "https://example.com/image.png"}]),
    lambda x: x.update(blocks=[{"kind": "figure", "png": "data:image/svg+xml;base64,PHN2Zz4="}]),
    lambda x: x.update(blocks=[{"kind": "figure", "png": "data:image/png;base64,NOT BASE64"}]),
    lambda x: x.update(blocks=[{"kind": "figure", "png": png_data()}] * 9),
    lambda x: x.update(html="<script>unexpected</script>"),
])
def test_reject_invalid_or_unbounded_structures(client, edit):
    body = payload()
    edit(body)
    assert client.post("/reports/pdf", json=body).status_code == 422


def test_png_dimensions_checked_before_decompression(client):
    raw = base64.b64decode(png_data().split(",", 1)[1])
    bad = raw[:16] + struct.pack(">II", 100_000, 100_000) + raw[24:]
    body = payload()
    body["blocks"] = [{"kind": "figure", "png": "data:image/png;base64," + base64.b64encode(bad).decode()}]
    assert client.post("/reports/pdf", json=body).status_code == 422


def test_body_limit_covers_streamed_request_without_content_length(client):
    chunks = iter([b'{"title":"', b"x" * reports.MAX_BODY_BYTES, b'"}'])
    response = client.post("/reports/pdf", content=chunks, headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_missing_browser_returns_actionable_503(client, monkeypatch):
    monkeypatch.setenv("FIRESPOT_CHROMIUM_PATH", "/nonexistent/firespot-report-browser")
    response = client.post("/reports/pdf", json=payload())
    assert response.status_code == 503
    assert "FIRESPOT_CHROMIUM_PATH" in response.json()["detail"]
    assert "/nonexistent/" not in response.text


def test_actual_pdf_includes_polish_data_table_and_figure_without_network(client, tmp_path, monkeypatch):
    # No socket/network access may be needed for a report. This remains a real
    # Playwright browser render; no model, Overpass or analysis module is imported.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    body = payload()
    body["blocks"].append({"kind": "figure", "png": png_data(), "caption": "Wykres: dane bieżącej analizy"})
    response = client.post("/reports/pdf", json=body)
    if response.status_code == 503:
        pytest.fail("Actual PDF renderer must be installed for this test: " + response.text)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    output = tmp_path / "analysis.pdf"
    output.write_bytes(response.content)
    if shutil.which("pdftotext"):
        text = subprocess.check_output(["pdftotext", str(output), "-"], text=True)
        for expected in ("Żółć", "Flin Flon", "12:00", "1,25", "2,50", "nie pewność", "Wykres:"):
            assert expected in text
    if shutil.which("pdfimages"):
        listing = subprocess.check_output(["pdfimages", "-list", str(output)], text=True)
        assert "image" in listing  # PNG really reached the PDF, not only its caption.


def test_long_table_repeats_header_on_later_pages(client, tmp_path):
    body = payload()
    body["language"] = "en"
    body["blocks"] = [{"kind": "table", "headers": ["Unique repeated header", "Measured value"],
                       "rows": [[f"Hour {i}", f"{i}.25"] for i in range(140)]}]
    response = client.post("/reports/pdf", json=body)
    assert response.status_code == 200, response.text[:250]
    output = tmp_path / "multipage.pdf"
    output.write_bytes(response.content)
    if shutil.which("pdftotext"):
        text = subprocess.check_output(["pdftotext", "-layout", str(output), "-"], text=True)
        assert text.count("Unique repeated header") >= 3
        assert "Hour 139" in text
        assert "Analysis report" in text
        assert "Analysis is not rerun" in text
