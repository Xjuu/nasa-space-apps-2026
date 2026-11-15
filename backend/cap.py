"""Common Alerting Protocol 1.2 export of a spread forecast."""
from __future__ import annotations

import datetime as dt
import hashlib
import xml.etree.ElementTree as ET

from . import geo
from .data import date_of
from .spread import Forecast

NS = "urn:oasis:names:tc:emergency:cap:1.2"
SENDER = "equifire-spaceapps-demo"
# severity by predicted newly affected area (km2 of 0.05 deg cells) at the last forecast day
SEVERITY = [(1000.0, "Extreme"), (300.0, "Severe"), (100.0, "Moderate"), (0.0, "Minor")]


def severity_for(km2: float) -> str:
    for lim, name in SEVERITY:
        if km2 >= lim:
            return name
    return "Minor"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()  # "+00:00", as CAP requires


def _cap_polygon(ring: list[list[float]]) -> str:
    return " ".join(f"{lat:.4f},{lon:.4f}" for lon, lat in ring)


def to_cap(fc: Forecast, now: dt.datetime | None = None) -> bytes:
    now = now or dt.datetime.now(dt.timezone.utc)
    start_day = dt.datetime.fromisoformat(date_of(fc.t0)).replace(tzinfo=dt.timezone.utc)
    historical = fc.observed_through_day > 0 or (now - start_day).days > 2
    last = fc.per_day[-1] if fc.per_day else {"predicted_km2": 0.0, "predicted_cells": 0}
    km2 = float(last["predicted_km2"])
    sev = severity_for(km2)
    probs = [d["mean_probability"] for d in fc.per_day if d["mean_probability"] is not None]
    certainty = "Likely" if probs and max(probs) > 0.5 else "Possible"
    ident = hashlib.sha1(f"{fc.t0}:{fc.cx}:{fc.cy}:{fc.days}".encode()).hexdigest()[:12]

    ET.register_namespace("", NS)
    q = lambda t: f"{{{NS}}}{t}"  # noqa: E731
    alert = ET.Element(q("alert"))

    def sub(parent, tag, text=None):
        e = ET.SubElement(parent, q(tag))
        if text is not None:
            e.text = str(text)
        return e

    sub(alert, "identifier", f"equifire-{date_of(fc.t0)}-{fc.cx}-{fc.cy}-{ident}")
    sub(alert, "sender", SENDER)
    sub(alert, "sent", _iso(now))
    sub(alert, "status", "Exercise" if historical else "Actual")
    sub(alert, "msgType", "Alert")
    sub(alert, "scope", "Public")
    if historical:
        sub(alert, "note", "Historical replay of archived satellite data (demo); not a live alert.")
    info = sub(alert, "info")
    sub(info, "language", "en-US")
    sub(info, "category", "Fire")
    sub(info, "event", "Wildfire spread forecast")
    sub(info, "responseType", "Monitor")
    sub(info, "urgency", "Expected")
    sub(info, "severity", sev)
    sub(info, "certainty", certainty)
    sub(info, "effective", _iso(start_day))
    sub(info, "onset", _iso(start_day + dt.timedelta(days=1)))
    sub(info, "expires", _iso(start_day + dt.timedelta(days=fc.days + 1)))
    sub(info, "senderName", "FireSpot (NASA Space Apps 2026)")
    sub(info, "headline", f"Wildfire near {fc.lat:.2f}, {fc.lon:.2f}: {last['predicted_cells']} grid cells "
                          f"(~{km2:.0f} km2) may newly burn within {fc.days} day(s)")
    lines = [f"Active fire: {len(fc.start)} burning 0.05-degree cells on {date_of(fc.t0)} (VIIRS)."]
    for d in fc.per_day:
        s = f"By {d['date']} (day {d['day']}): {d['predicted_cells']} cells, ~{d['predicted_km2']:.0f} km2"
        if d.get("iou") is not None:
            s += f"; observed {d['observed_cells']} cells, overlap IoU {d['iou']:.0%}"
        lines.append(s + ".")
    w = fc.wind
    if w.get("speed_kmh") is not None:
        lines.append(f"Wind max {w['speed_kmh']:.0f} km/h from {w['from_deg']:.0f} deg.")
    sub(info, "description", " ".join(lines))
    sub(info, "instruction", "Monitor the fire; prepare for possible evacuation in the indicated areas. "
                             "Satellite-derived estimate, verify locally.")
    for name, val in (("severity_km2", f"{km2:.1f}"), ("burning_cells", len(fc.start)),
                      ("forecast_days", fc.days), ("model", "equifire.direct")):
        p = sub(info, "parameter")
        sub(p, "valueName", name)
        sub(p, "value", val)

    start = {(c["gx"], c["gy"]) for c in fc.start}
    for d, cells in zip(fc.per_day, fc.masks):
        rings = geo.hull_rings(sorted(start | set(cells)))
        if not rings:
            continue
        area = sub(info, "area")
        sub(area, "areaDesc", f"Fire and predicted spread by {d['date']} (day {d['day']}, "
                              f"{d['predicted_cells']} new cells)")
        for r in rings:
            sub(area, "polygon", _cap_polygon(r))
    ET.indent(alert)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(alert, encoding="utf-8")
