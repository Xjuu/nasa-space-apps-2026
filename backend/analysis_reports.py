"""Render an already displayed analysis as a PDF; never run analysis or fetch data.

Only plain structured text and bounded PNG figures cross this boundary. Client
HTML, SVG, URLs and scripts are deliberately not part of the report contract.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import html
import io
import os
from pathlib import Path
import shutil
import struct
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PNG_BYTES = 500 * 1024
ROOT = Path(__file__).resolve().parents[1]
_render_slots = asyncio.Semaphore(2)
Short = Annotated[str, StringConstraints(strict=True, max_length=2000)]


class LimitedBodyRoute(APIRoute):
    """Bound the body before FastAPI reads/parses JSON, including chunked requests."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def limited(request: Request):
            size, parts = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise HTTPException(413, "Report payload exceeds 2 MiB.")
                parts.append(chunk)
            request._body = b"".join(parts)
            return await handler(request)

        return limited


class ReportContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(max_length=160)
    value: Short


def _clean_png(data_url: str) -> str:
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("Figures must be inline PNG images.")
    encoded = data_url[len(prefix):]
    if len(encoded) > ((MAX_PNG_BYTES + 2) // 3) * 4:
        raise ValueError("PNG exceeds 500 KiB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid PNG base64.") from None
    if len(raw) > MAX_PNG_BYTES or raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 33 or raw[12:16] != b"IHDR":
        raise ValueError("Invalid or oversized PNG.")
    width, height = struct.unpack(">II", raw[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= 8_000_000):
        raise ValueError("PNG dimensions exceed 4096 px / 8 megapixels.")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format != "PNG" or getattr(picture, "n_frames", 1) != 1:
                raise ValueError("A single PNG frame is required.")
            picture.verify()
        with Image.open(io.BytesIO(raw)) as picture:
            # Strip metadata and embedded ancillary data before handing it to Chromium.
            clean = picture.convert("RGBA")
            out = io.BytesIO()
            clean.save(out, format="PNG")
        result = out.getvalue()
        if len(result) > MAX_PNG_BYTES:
            raise ValueError("Re-encoded PNG exceeds 500 KiB.")
    except (OSError, SyntaxError, ValueError):
        raise ValueError("Invalid, animated or oversized PNG.") from None
    return prefix + base64.b64encode(result).decode("ascii")


class ReportBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heading", "paragraph", "table", "figure"]
    text: str | None = Field(default=None, max_length=12000)
    headers: list[Short] | None = Field(default=None, max_length=16)
    rows: list[Annotated[list[Short], Field(max_length=16)]] | None = Field(default=None, max_length=500)
    png: str | None = Field(default=None, max_length=700_000)
    caption: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_shape(self):
        if self.kind in ("heading", "paragraph"):
            if self.text is None or not self.text.strip():
                raise ValueError("Text blocks require nonempty text.")
            if self.kind == "heading" and len(self.text) > 300:
                raise ValueError("Heading exceeds 300 characters.")
        elif self.kind == "table":
            if not self.headers or self.rows is None:
                raise ValueError("Tables require headers and rows.")
            if any(len(row) != len(self.headers) for row in self.rows):
                raise ValueError("Every table row must match its headers.")
        elif self.kind == "figure":
            if not self.png:
                raise ValueError("Figures require a PNG.")
            self.png = _clean_png(self.png)
        if self.kind != "figure" and self.png is not None:
            raise ValueError("PNG is allowed only in figure blocks.")
        return self


class AnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=180)
    language: Literal["pl", "en"] = "pl"
    generated_at: str = Field(default="", max_length=120)
    context: list[ReportContext] = Field(default_factory=list, max_length=40)
    blocks: list[ReportBlock] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_totals(self):
        if not self.title.strip():
            raise ValueError("A nonempty title is required.")
        if sum(b.kind == "figure" for b in self.blocks) > 8:
            raise ValueError("At most 8 figures are allowed.")
        if sum(len(b.rows or []) for b in self.blocks) > 2500:
            raise ValueError("At most 2500 total table rows are allowed.")
        if len(self.model_dump_json().encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("Report content exceeds 2 MiB.")
        return self


CSS = """
@page{size:A4;margin:20mm 17mm 20mm}
*{box-sizing:border-box}body{font:10pt/1.5 'DejaVu Sans',sans-serif;color:#24333d;margin:0;background:#fff}
.brand{display:flex;align-items:center;gap:3mm;border-bottom:1px solid #ccd8dc;padding-bottom:4mm;margin-bottom:7mm;font-size:15pt;font-weight:bold}
.brand img{width:8mm;height:11mm;object-fit:contain}.brand span{margin-left:auto;color:#526871;font-size:8pt;font-weight:normal}
h1{font-size:25pt;line-height:1.2;letter-spacing:-.5px;margin:0 0 6mm;overflow-wrap:anywhere}
h2{font-size:14pt;color:#145c64;line-height:1.3;margin:7mm 0 3mm;break-after:avoid;overflow-wrap:anywhere}
p{margin:0 0 3.5mm;white-space:pre-wrap;overflow-wrap:anywhere;orphans:3;widows:3}
.context{display:grid;grid-template-columns:1fr 1fr;gap:3mm 5mm;margin:0 0 6mm;padding:4mm;background:#edf4f3;border:1px solid #d5e2e1}
.context div{overflow-wrap:anywhere;break-inside:avoid}.context dt{font-size:8pt;color:#526871}.context dd{margin:0;font-size:9pt;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;table-layout:fixed;margin:4mm 0 6mm;font-size:8.5pt}
thead{display:table-header-group}th{background:#edf4f3;color:#145c64;font-weight:bold}td,th{padding:2.3mm;text-align:left;vertical-align:top;border-bottom:1px solid #d9e1e4;overflow-wrap:anywhere;white-space:pre-wrap}
tr{break-inside:avoid}figure{margin:5mm 0;break-inside:avoid}figure img{display:block;width:auto;max-width:100%;max-height:185mm;margin:auto;object-fit:contain}figcaption{font-size:8.5pt;color:#526871;margin-top:2mm;overflow-wrap:anywhere}
.note{color:#526871;font-size:8pt;border-top:1px solid #d9e1e4;padding-top:3mm;margin-top:7mm}
"""


def render_html(report: AnalysisReport) -> str:
    """All untrusted text goes through escaping; only our own asset is SVG."""
    esc = html.escape
    logo = ROOT / "frontend/assets/logo.svg"
    brand_image = ""
    if logo.is_file():
        encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
        brand_image = f'<img alt="" src="data:image/svg+xml;base64,{encoded}">'
    subtitle = "Raport analizy" if report.language == "pl" else "Analysis report"
    parts = [f'<div class="brand">{brand_image}FireSpot<span>{subtitle}</span></div>', f"<h1>{esc(report.title)}</h1>"]
    if report.generated_at:
        label = "Wygenerowano" if report.language == "pl" else "Generated"
        parts.append(f'<p class="note">{label}: {esc(report.generated_at)}</p>')
    if report.context:
        parts.append('<dl class="context">' + "".join(f"<div><dt>{esc(x.label)}</dt><dd>{esc(x.value)}</dd></div>" for x in report.context) + "</dl>")
    for block in report.blocks:
        if block.kind == "heading":
            parts.append(f"<h2>{esc(block.text or '')}</h2>")
        elif block.kind == "paragraph":
            parts.append(f"<p>{esc(block.text or '')}</p>")
        elif block.kind == "table":
            parts.append("<table><thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in block.headers or []) + "</tr></thead><tbody>")
            parts.extend("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in block.rows or [])
            parts.append("</tbody></table>")
        else:
            parts.append(f'<figure><img alt="{esc(block.caption or "")}" src="{block.png}">')
            if block.caption:
                parts.append(f"<figcaption>{esc(block.caption)}</figcaption>")
            parts.append("</figure>")
    note = ("Eksport aktualnie przedstawionych danych. Nie uruchamia ponownie analizy. Ograniczenia źródłowej analizy pozostają w mocy."
            if report.language == "pl" else "Export of the displayed data. Analysis is not rerun. The limitations of the source analysis still apply.")
    parts.append(f'<p class="note">{note}</p>')
    csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; connect-src 'none'; base-uri 'none'"
    return (f'<!doctype html><html lang="{report.language}"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{esc(report.title)} — FireSpot</title>'
            f'<style>{CSS}</style></head><body>' + "".join(parts) + "</body></html>")


def _browser_path(playwright) -> str:
    configured = os.environ.get("FIRESPOT_CHROMIUM_PATH")
    if configured:
        if Path(configured).is_file():
            return configured
        raise RuntimeError("Configured report browser is unavailable.")
    candidates = [Path(playwright.chromium.executable_path)]
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache/ms-playwright")))
    candidates.extend(sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True))
    for path in candidates:
        if path.is_file():
            return str(path)
    raise RuntimeError("Report browser is unavailable.")


async def render_pdf(report: AnalysisReport) -> bytes:
    from playwright.async_api import async_playwright

    async with _render_slots:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=_browser_path(playwright), args=["--no-sandbox"])
            try:
                context = await browser.new_context(java_script_enabled=False, service_workers="block")
                # No content may retrieve HTTP, file:// or other external resources.
                await context.route("**/*", lambda route: route.abort())
                page = await context.new_page()
                page.set_default_timeout(20_000)
                await page.set_content(render_html(report), wait_until="load")
                await page.emulate_media(media="print")
                return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                                      display_header_footer=True, header_template="<div></div>",
                                      footer_template='<div style="font:8px sans-serif;color:#526871;width:100%;margin:0 64px;display:flex;justify-content:space-between"><span>FireSpot</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
                                      tagged=True, outline=True)
            finally:
                await browser.close()


router = APIRouter(tags=["reports"], route_class=LimitedBodyRoute)


@router.post("/reports/pdf", summary="Download displayed analysis as a FireSpot PDF without rerunning it",
             responses={200: {"content": {"application/pdf": {}}}, 413: {"description": "Payload exceeds 2 MiB"},
                        503: {"description": "PDF renderer unavailable"}})
async def analysis_pdf(report: AnalysisReport) -> Response:
    try:
        pdf = await render_pdf(report)
    except (ImportError, RuntimeError, OSError) as error:
        raise HTTPException(503, "PDF renderer unavailable. Install Playwright and Chromium or set FIRESPOT_CHROMIUM_PATH.") from error
    except Exception as error:
        # Browser exceptions may contain paths or content. Do not expose them.
        raise HTTPException(503, "PDF generation failed. Please try again.") from error
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="firespot-analysis.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
