"""One-page INCIDENT BRIEF (self-contained, print-friendly HTML; optional PDF via headless LibreOffice).

    GET /brief?lat&lon&start&hours&ignition=live|point|archive   -> text/html (same as /brief.html)
    GET /brief.html?...                                           -> text/html
    GET /brief.json?...                                           -> the structured content (no image)
    GET /brief.pdf?...                                            -> application/pdf (LibreOffice conversion of the HTML)
    POST /brief.html  body = /simulate response                   -> brief for an already-run simulation

The map is rendered server-side with matplotlib (inline PNG, no external tiles): burned-footprint timeline in the
cividis ramp (colourblind-safe), ignition points marked ESTIMATE, exposed assets as Okabe-Ito coloured markers
with distinct shapes. Every exposure number is an ESTIMATED EXPOSURE, never damage or loss, never money.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from . import exposure as ex

STATUS = ("EXPERIMENTAL / UNCALIBRATED. Forecast confidence is uncalibrated; the low/high ranges are weather "
          "sensitivity scenarios, not confidence intervals. Not for operational decisions; verify with local "
          "authorities and current observations.")

SOURCES = [
    ("Fire detections", "NASA FIRMS (MODIS, VIIRS) / LANCE / ESDIS",
     "We acknowledge the use of data and imagery from NASA's Fire Information for Resource Management System (FIRMS)."),
    ("Weather", "Open-Meteo.com (CC BY 4.0), ERA5 (Copernicus C3S); NOAA / NCEP GFS fallback",
     "Weather data by Open-Meteo.com (https://open-meteo.com/)."),
    ("Land cover / fuel", "(c) ESA WorldCover 2021 (CC BY 4.0)",
     "Contains modified Copernicus Sentinel data processed by the ESA WorldCover consortium."),
    ("Terrain", "Copernicus DEM GLO-30", "Copernicus Digital Elevation Model, accessed via AWS Open Data."),
    ("Vegetation dryness", "Sentinel-2 L2A (Earth Search)", "Contains modified Copernicus Sentinel data."),
    ("Roads, buildings, facilities, power", "(c) OpenStreetMap contributors (ODbL 1.0), via Overpass API",
     "https://www.openstreetmap.org/copyright. OSM completeness varies by region."),
    ("Population (optional)", "Meta / CIESIN High Resolution Settlement Layer (CC BY 4.0), AWS Open Data",
     "Facebook Connectivity Lab and CIESIN, Columbia University: High Resolution Settlement Layer. "
     "Licence/attribution wording to be verified before publication."),
    ("Satellite passes", "CelesTrak orbital elements (SGP4 propagation)", "Orbital elements: CelesTrak (celestrak.org)."),
]
LIMITS = [
    "Experimental, uncalibrated physics simulation (Rothermel/Anderson fuels, MTT raster); it can over- or under-predict "
    "growth by a large factor, especially in savanna/shrub and for fires burning for days.",
    "The initial footprint is built from satellite pixel centres (VIIRS 375 m, MODIS 1 km), not true fire edges; the "
    "ignition points are an ESTIMATE.",
    "Satellites see a fire only at passes; cloud and smoke can hide it. No detection is not proof of no fire.",
    "Exposure counts what lies inside the simulated footprint (ESTIMATED EXPOSURE). It is not damage, not loss, "
    "not a casualty or evacuation estimate, and ignores spotting, suppression, and structure vulnerability.",
    "OpenStreetMap completeness varies by region: unmapped buildings, minor roads and power lines are not counted, "
    "so counts and lengths are lower bounds where the map is incomplete. Overpass failures make results partial "
    "(see coverage).",
    "Arrival times have the frame step as resolution (frame 0 = already inside the initial footprint).",
    "Population figures are static gridded or proxy estimates (no time of day, no occupancy); they are kept apart "
    "from everything else and are not casualty figures.",
    ex.INSURED_LOSS_NOTE,
]

# Okabe-Ito colourblind-safe palette
OI = {"hospital": "#D55E00", "school": "#0072B2", "fire_station": "#CC79A7", "power_site": "#E69F00",
      "settlement": "#000000", "residential_area": "#009E73", "road": "#56B4E9", "rail": "#7F7F7F",
      "power_line": "#E69F00", "building_cell": "#555555"}
MARK = {"hospital": "P", "school": "s", "fire_station": "^", "power_site": "D", "settlement": "o"}


# ------------------------------------------------------------------ next pass
def next_passes(lat: float, lon: float, now: dt.datetime, hours: float = 24, n: int = 4) -> dict:
    """Use backend.nextobs if it exists and exposes a compatible function, else backend.orbits directly."""
    try:
        from . import nextobs as _no   # backend/nextobs.py (GET /nextobs): last detection, passes, cloud visibility
        res = _no.nextobs(lat=lat, lon=lon, time=now.strftime("%Y-%m-%dT%H:%M:%SZ"), radius_km=15.0)
        rows = []
        for p in (res.get("passes") or [])[:n]:
            c = p.get("cloud") or {}
            rows.append({"satellite": p["satellite"], "instrument": p["instrument"], "best": p["time_utc"],
                         "minutes_until": round(p["hours_from_ref"] * 60, 1), "pixel_km": p.get("pixel_km"),
                         "min_scan_deg": p.get("scan_angle_deg"), "day_pass": p.get("day_pass"),
                         "in_progress": p.get("in_progress", False), "quality": p.get("quality_tier"),
                         "cloud": ("%s (%s%%)" % (c.get("visibility"), c.get("cover_pct"))) if c.get("visibility") else None,
                         "usable": p.get("usable")})
        return {"source": "backend.nextobs (GET /nextobs: SGP4 passes + Open-Meteo cloud visibility indicator)",
                "passes": rows, "fire": res.get("fire"), "gap_hours": res.get("gap_hours"),
                "simulate_until": res.get("simulate_until"), "notes": res.get("notes"),
                "next_usable": res.get("next_usable_observation")}
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import orbits
        rows = []
        for s in orbits.passes(lat, lon, now, hours):
            for p in s.get("passes", []):
                if p.get("in_progress") and False:
                    continue
                rows.append({"satellite": s["satellite"], "instrument": s["instrument"], "best": p["best"],
                             "minutes_until": p["minutes_until"], "pixel_km": p["pixel_km"],
                             "min_scan_deg": p["min_scan_deg"], "day_pass": p["day_pass"],
                             "in_progress": p.get("in_progress", False), "tle_age_days": s.get("tle_age_days")})
        rows.sort(key=lambda r: r["minutes_until"])
        return {"source": "backend.orbits (SGP4 on TLEs)", "passes": rows[:n]}
    except Exception as e:  # noqa: BLE001
        return {"source": "unavailable", "error": repr(e)[:200], "passes": []}


# ------------------------------------------------------------------ map
def render_map_png(sim: dict, expo: dict, width_in: float = 7.2, height_in: float = 5.6, dpi: int = 120) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm
    from matplotlib.lines import Line2D

    frames = ex._frames_of(sim)
    env = ex._env_geoms(sim)
    n = len(frames)
    cmap = matplotlib.colormaps["cividis"]
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    allb = [fr["geom"].bounds for fr in frames if not fr["geom"].is_empty] + [g.bounds for g in env.values()]
    w, s = min(b[0] for b in allb), min(b[1] for b in allb)
    e, nn = max(b[2] for b in allb), max(b[3] for b in allb)
    lat0 = (s + nn) / 2
    pad = max(e - w, (nn - s) / math.cos(math.radians(lat0))) * 0.12 + 0.005
    ax.set_xlim(w - pad, e + pad)
    ax.set_ylim(s - pad * math.cos(math.radians(lat0)), nn + pad * math.cos(math.radians(lat0)))
    ax.set_aspect(1 / math.cos(math.radians(lat0)))

    for fr in reversed(frames):
        g = fr["geom"]
        if g.is_empty:
            continue
        col = cmap(fr["index"] / max(n - 1, 1))
        for p in getattr(g, "geoms", [g]):
            x, y = p.exterior.xy
            ax.fill(x, y, facecolor=col, edgecolor="none", alpha=1.0, zorder=2)
    for k, ls in (("low", ":"), ("high", "--")):
        if k in env:
            for p in getattr(env[k], "geoms", [env[k]]):
                x, y = p.exterior.xy
                ax.plot(x, y, color="#222222", lw=1.1, ls=ls, zorder=4)
    feats = expo["assets_geojson"]["features"]
    # buildings (cells) as small grey dots
    bc = [f for f in feats if f["properties"]["class"] == "building_cell"]
    if bc:
        ax.scatter([f["geometry"]["coordinates"][0] for f in bc], [f["geometry"]["coordinates"][1] for f in bc],
                   s=[4 + 2.2 * math.sqrt(f["properties"]["buildings"]) for f in bc], c="#ffffff", edgecolors="#333333",
                   linewidths=0.4, alpha=0.9, zorder=5)
    for f in feats:
        c = f["properties"]["class"]
        g = f["geometry"]
        if c in ("road", "rail", "power_line"):
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for ln in lines:
                xs, ys = zip(*ln)
                ax.plot(xs, ys, color="white", lw=2.6, zorder=5.5, solid_capstyle="round")
                ax.plot(xs, ys, color=OI[c], lw=1.2, zorder=6, ls="-" if c == "road" else "--")
        elif c == "residential_area" and g["type"] in ("Polygon", "MultiPolygon"):
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for pl in polys:
                xs, ys = zip(*pl[0])
                ax.plot(xs, ys, color=OI[c], lw=1.2, zorder=5.8)
        elif c in MARK:
            x, y = g["coordinates"]
            ax.scatter([x], [y], marker=MARK[c], s=70 if c != "settlement" else 55, c="white" if c == "settlement" else OI[c],
                       edgecolors="black", linewidths=1.0, zorder=8)
    # label the first few settlements / facilities
    lab = [a for a in expo["arrivals"] if a["class"] in ("hospital", "settlement", "school", "fire_station", "power_site")][:8]
    for a in lab:
        ax.annotate("%s (+%gh)" % (a["name"][:22], a["arrival_h"]), (a["lon"], a["lat"]), xytext=(5, 5),
                    textcoords="offset points", fontsize=6.5, zorder=9,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    pts = (sim.get("ignition") or {}).get("points") or []
    if pts:
        px, py = zip(*pts[:200])
        ax.scatter(px, py, marker="x", s=26, c="white", linewidths=2.6, zorder=9.5)
        ax.scatter(px, py, marker="x", s=26, c="#B2182B", linewidths=1.3, zorder=10)
    ax.set_xlabel("longitude", fontsize=7)
    ax.set_ylabel("latitude", fontsize=7)
    ax.tick_params(labelsize=6.5)
    ax.grid(color="#cccccc", lw=0.4, zorder=0)
    ax.set_facecolor("#f4f4f4")
    # time colourbar
    sm = cm.ScalarMappable(cmap=cmap, norm=matplotlib.colors.Normalize(0, frames[-1]["t_hours"] or 1))
    cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cb.set_label("simulated hours from start (ESTIMATE)", fontsize=7)
    cb.ax.tick_params(labelsize=6.5)
    leg = [Line2D([], [], marker="x", color="#B2182B", ls="", markersize=6, label="ignition points (ESTIMATE)"),
           Line2D([], [], color="#222222", ls="--", lw=1.1, label="high-weather scenario outline"),
           Line2D([], [], color="#222222", ls=":", lw=1.1, label="low-weather scenario outline"),
           Line2D([], [], marker="P", color="w", markerfacecolor=OI["hospital"], markeredgecolor="k", ls="", label="hospital"),
           Line2D([], [], marker="s", color="w", markerfacecolor=OI["school"], markeredgecolor="k", ls="", label="school"),
           Line2D([], [], marker="^", color="w", markerfacecolor=OI["fire_station"], markeredgecolor="k", ls="", label="fire station"),
           Line2D([], [], marker="D", color="w", markerfacecolor=OI["power_site"], markeredgecolor="k", ls="", label="substation / plant"),
           Line2D([], [], marker="o", color="w", markerfacecolor="white", markeredgecolor="k", ls="", label="settlement"),
           Line2D([], [], color=OI["road"], lw=1.8, label="road exposed"),
           Line2D([], [], color=OI["rail"], lw=1.8, ls="--", label="railway exposed"),
           Line2D([], [], color=OI["power_line"], lw=1.8, ls="--", label="power line exposed"),
           Line2D([], [], marker="o", color="w", markerfacecolor="white", markeredgecolor="#333", ls="", markersize=4,
                  label="building cell (~500 m), size = count")]
    ax.legend(handles=leg, fontsize=6, loc="upper center", bbox_to_anchor=(0.5, -0.11), framealpha=0.9, ncol=4)
    ax.set_title("Simulated burned footprint timeline and ESTIMATED exposed assets (experimental)", fontsize=8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ------------------------------------------------------------------ content
def _fmt_t(s):
    return (s or "").replace("T", " ").replace("Z", " UTC")[:20] if s else "n/a"


def _age(latest_iso, ref: dt.datetime):
    if not latest_iso:
        return None
    t = dt.datetime.fromisoformat(latest_iso.replace("Z", "")).replace(tzinfo=dt.timezone.utc)
    return round((ref - t).total_seconds() / 3600, 1)


def build_brief(sim: dict, expo: dict, now: dt.datetime | None = None, passes: dict | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    q = sim.get("query") or {}
    ign = sim.get("ignition") or {}
    start = q.get("start")
    ref = now
    if ign.get("mode") != "live" and start:
        ref = dt.datetime.fromisoformat(start.replace("Z", "")).replace(tzinfo=dt.timezone.utc)
    latest = ign.get("latest_utc")
    obs = {"mode": ign.get("mode"), "latest_detection_utc": latest, "age_h": _age(latest, ref),
           "age_reference": "now" if ref is now else "simulation start", "n_detections": ign.get("n_detections"),
           "satellites": ign.get("satellites"), "estimate": True,
           "note": ign.get("note") or ign.get("fallback_reason")}
    nf = (passes or {}).get("fire") or {}
    if nf.get("found"):
        obs["last_detection_within_15km_utc"] = nf.get("last_detection_utc")
        obs["last_detection_within_15km_age_h"] = nf.get("observation_age_hours")
    summ = sim.get("summary") or {}
    fc = {"hours": q.get("simulated_hours") or q.get("hours"), "step_min": q.get("step_min"),
          "burned_km2_central_final": summ.get("burned_km2"), "initial_km2": summ.get("initial_km2"),
          "envelope_km2": summ.get("envelope_km2"), "n_fires": summ.get("n_fires"),
          "max_ros_m_min": summ.get("max_ros_m_min"), "calibration": (sim.get("calibration") or {}).get("reason") or
          ("applied (x%s)" % sim.get("ros_multiplier") if sim.get("ros_multiplier") else None),
          "ml_note": ("Daily ML + physics guidance (/predict/simulate) is a separate product with its own uncalibrated "
                      "daily scores; this brief uses the hourly physics simulation (central run plus low/high weather "
                      "scenarios) only." + (" Combined guidance included in the simulation is not summarised here."
                                            if sim.get("guidance") else "")),
          "weather": (sim.get("weather") or {}).get("source") if isinstance(sim.get("weather"), dict) else None}
    return {"generated_utc": now.isoformat(timespec="seconds"), "status": STATUS,
            "location": {"lat": q.get("lat"), "lon": q.get("lon"), "start_utc": start, "hours": fc["hours"],
                         "ignition_mode": ign.get("mode"), "fallback_reason": ign.get("fallback_reason")},
            "observation": obs, "next_pass": passes, "forecast": fc,
            "exposure": {"label": expo["label"], "summary": expo["summary"], "arrivals": expo["arrivals"],
                         "scenario_only_assets": expo["scenario_only_assets"], "first_exposure": expo["first_exposure"],
                         "population": expo["population"], "coverage": expo["coverage"],
                         "resolution_h": expo["resolution_h"], "insured_loss_note": expo["insured_loss_note"]},
            "sources": [{"what": a, "source": b, "attribution": c} for a, b, c in SOURCES], "limitations": LIMITS}


E = html.escape


def _hm(h):
    return "n/a" if h is None else ("%gh" % h)


def render_html(b: dict, png: bytes | None) -> str:
    L, o, f, x = b["location"], b["observation"], b["forecast"], b["exposure"]
    rows = []
    for a in x["arrivals"]:
        extra = ""
        if a.get("exposed_length_km") is not None:
            extra = "%s km exposed" % a["exposed_length_km"]
        rows.append("<tr><td class=n>+%s</td><td>%s</td><td>%s</td><td>%s</td><td class=n>%.4f, %.4f</td><td>%s</td></tr>" % (
            E(_hm(a["arrival_h"])), E(_fmt_t(a["time_utc"])), E(ex.CLASS_LABELS.get(a["class"], a["class"]).split(" (")[0]),
            E(a["name"]), a["lat"], a["lon"], E(extra or a["type"])))
    for a in x["scenario_only_assets"][:8]:
        rows.append("<tr class=sc><td class=n>scenario</td><td>high-weather only</td><td>%s</td><td>%s</td><td class=n>%.4f, %.4f</td><td>%s</td></tr>" % (
            E(ex.CLASS_LABELS.get(a["class"], a["class"]).split(" (")[0]), E(a["name"]), a["lat"], a["lon"], E(a["type"])))
    srows = []
    order = ex.CLASS_PRIORITY + ["building"]
    for c in order:
        s = x["summary"].get(c)
        if not s:
            continue
        fe = s.get("first_exposure")
        sr = s.get("scenario_range") or {}
        rng = ("%s / %s / %s" % (sr.get("low"), sr.get("central"), sr.get("high"))) if sr else "n/a"
        srows.append("<tr><td>%s</td><td class=n>%s %s</td><td class=n>%s</td><td class=n>%s</td><td class=n>%s</td></tr>" % (
            E(ex.CLASS_LABELS.get(c, c)), E(str(s["exposed"])), "km" if s.get("unit") == "km" else "",
            E(_hm(fe["hours_from_start"])) if fe else "not reached", E(str(s["mapped_in_bbox"])), E(rng)))
    pop = x["population"]
    pl = []
    if pop.get("hrsl"):
        h = pop["hrsl"]
        pl.append("<li><b>HRSL gridded estimate</b> (%s): about <b>%s</b> people inside the final central footprint "
                  "(low / central / high scenario: %s / %s / %s).</li>" % (
                      E(str(h.get("source", ""))), E(str(h["exposed"])), h["scenario_range"].get("low"),
                      h["scenario_range"].get("central"), h["scenario_range"].get("high")))
    elif pop.get("hrsl_unavailable"):
        pl.append("<li>HRSL: %s</li>" % E(pop["hrsl_unavailable"]))
    if pop.get("buildings_proxy"):
        p = pop["buildings_proxy"]
        pl.append("<li><b>ROUGH PROXY from OSM buildings</b>: about <b>%s</b> residents (low %s, high %s). %s</li>" % (
            p["central"], p["low"], p["high"], E(p["method"])))
    cov = x["coverage"]
    cov_txt = "complete" if cov.get("complete") else "PARTIAL"
    cov_det = []
    for k, v in (cov.get("osm") or {}).items():
        cov_det.append("%s: %s" % (k, "ok" if v.get("ok") else "FAILED (%s)" % E("; ".join(map(str, v.get("errors", [])))[:160])))
    nxt = b.get("next_pass") or {}
    pass_rows = ""
    if nxt.get("passes"):
        for p in nxt["passes"]:
            pass_rows += "<tr><td>%s (%s)</td><td>%s</td><td class=n>%s min</td><td class=n>%s</td><td>%s</td><td>%s</td></tr>" % (
                E(p["satellite"]), E(p["instrument"]), E(_fmt_t(p["best"])), E(str(p["minutes_until"])),
                "%s x %s km" % tuple(p["pixel_km"]) if p.get("pixel_km") else "", "day" if p.get("day_pass") else "night",
                E(" / ".join(x_ for x_ in (str(p.get("quality") or ""), ("cloud " + p["cloud"]) if p.get("cloud") else "") if x_)))
        gap = ""
        su = nxt.get("simulate_until")
        if su and su.get("hours"):
            gap = ("<p>Next usable observation of this fire in about <b>%s h</b> (%s, %s); the forecast horizon before it can be "
                   "checked is therefore about %s h.</p>" % (E(str(su.get("exact_hours"))), E(str(su.get("satellite"))),
                                                              E(_fmt_t(su.get("until_utc"))), E(str(su["hours"]))))
        passes_html = (gap + "<table><tr><th>Satellite</th><th>Best time</th><th>In</th><th>Pixel (along x across)</th><th></th>"
                       "<th>Scan quality / cloud visibility</th></tr>%s</table>"
                       "<p class=small>Source: %s. Passes are overflights inside the sensor swath; the cloud value is a model "
                       "visibility indicator, not proof that a fire is hidden or seen.</p>" % (pass_rows, E(nxt.get("source", ""))))
    elif nxt.get("result"):
        passes_html = "<pre class=small>%s</pre><p class=small>Source: %s</p>" % (E(json.dumps(nxt["result"], indent=1)[:1500]), E(nxt["source"]))
    else:
        passes_html = "<p>Next satellite pass unavailable (%s).</p>" % E(str(nxt.get("error", "no data")))
    img = ("<img alt='Map of the simulated burned footprint timeline with ESTIMATED exposed assets' src='data:image/png;base64,%s'>"
           % base64.b64encode(png).decode()) if png else "<p>(map unavailable)</p>"
    env = f.get("envelope_km2") or {}
    return """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>FireSpot incident brief (experimental)</title>
<style>
:root{--ink:#1d2b34;--mut:#52646e;--line:#dce3e6;--act:#145c64;--warn:#7a3b00;--warnbg:#fff4e5}
@page{size:A4;margin:12mm}
*{box-sizing:border-box}body{font:13px/1.4 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:#fff;margin:0}
main{max-width:900px;margin:0 auto;padding:14px 16px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:14px;margin:14px 0 4px;color:var(--act);border-bottom:1px solid var(--line);padding-bottom:2px}
.status{background:var(--warnbg);border:1px solid #e6b36c;color:var(--warn);padding:6px 9px;border-radius:5px;font-size:12px;margin:8px 0}
.est{background:#eef3f4;border:1px solid var(--line);padding:5px 9px;border-radius:5px;font-size:12px}
table{border-collapse:collapse;width:100%%;font-size:11.5px;margin:4px 0}th,td{border-bottom:1px solid var(--line);padding:3px 5px;text-align:left;vertical-align:top}
th{background:#f4f7f8}td.n{font-variant-numeric:tabular-nums;white-space:nowrap}tr.sc td{color:var(--mut);font-style:italic}
img{max-width:100%%;height:auto;display:block;margin:6px auto;border:1px solid var(--line)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px}.small{font-size:11px;color:var(--mut)}
ul{margin:4px 0 4px 18px;padding:0}li{margin:2px 0}pre{white-space:pre-wrap}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
@media print{main{padding:0}h2{break-after:avoid}table,img{break-inside:avoid}}
</style></head><body><main>
<h1>FireSpot incident brief</h1>
<div class=small>Generated %s UTC. Research prototype, not endorsed by NASA, ESA, NOAA or any data provider.</div>
<div class=status><b>%s</b></div>
<div class=est><b>%s</b></div>

<h2>1. Location and time</h2>
<div class=grid><div>Point: <b>%s, %s</b></div><div>Simulation start: <b>%s</b></div>
<div>Horizon: <b>%s h</b> (step %s min)</div><div>Ignition source: <b>%s</b>%s</div></div>

<h2>2. Latest observation</h2>
<p>Latest satellite detection used: <b>%s</b>, age <b>%s h</b> (relative to %s). %s detections, satellites: %s.
These are pixel centres (ESTIMATE of where the fire was), not the fire edge.%s</p>%s

<h2>3. Next satellite pass</h2>%s

<h2>4. Forecast window (physics, experimental)</h2>
<ul><li>Central run: <b>%s km&sup2;</b> burned at the end of %s h (initial %s km&sup2;); fires: %s; max rate of spread %s m/min.</li>
<li>Weather scenarios (not confidence intervals): low %s / central %s / high %s km&sup2;.</li>
<li>%s</li></ul>

<h2>5. Map</h2>%s

<h2>6. Potentially exposed assets (ESTIMATED EXPOSURE)</h2>
<p class=small>%s Arrival = first simulated frame whose footprint touches the asset (resolution %s h). Scenario column: low / central / high weather.
OSM coverage: <b>%s</b> (%s).</p>
<table><tr><th>Class</th><th>Exposed (central, end)</th><th>First exposure</th><th>Mapped in area</th><th>Scenario range</th></tr>%s</table>
<h3 style="font-size:12px;margin:8px 0 2px">Estimated arrival order (top %s)</h3>
<table><tr><th>In</th><th>Time</th><th>Type</th><th>Name</th><th>Lat, Lon</th><th>Detail</th></tr>%s</table>

<h2>7. Population (separate, optional, not a loss figure)</h2>
<ul>%s</ul><p class=small>%s</p>

<h2>8. Data sources and attribution</h2>
<table><tr><th>Data</th><th>Source</th><th>Attribution</th></tr>%s</table>

<h2>9. Limitations</h2><ul>%s</ul>
</main></body></html>""" % (
        E(b["generated_utc"].replace("T", " ")[:19]), E(b["status"]), E(x["label"]),
        L["lat"], L["lon"], E(_fmt_t(L["start_utc"])), L["hours"], f.get("step_min"), E(str(L["ignition_mode"])),
        (" (fallback: %s)" % E(L["fallback_reason"])) if L.get("fallback_reason") else "",
        E(_fmt_t(o["latest_detection_utc"])), E(str(o["age_h"])), E(o["age_reference"]), E(str(o["n_detections"])),
        E(", ".join(o["satellites"] or []) or "n/a"),
        (" Last FIRMS detection within 15 km (any sensor): <b>%s</b>, age <b>%s h</b>." % (
            E(_fmt_t(o["last_detection_within_15km_utc"])), E(str(o["last_detection_within_15km_age_h"])))) if o.get("last_detection_within_15km_utc") else "",
        ("<p class=small>%s</p>" % E(o["note"])) if o.get("note") else "",
        passes_html,
        f["burned_km2_central_final"], f["hours"], f["initial_km2"], f["n_fires"], f["max_ros_m_min"],
        env.get("low"), env.get("central"), env.get("high"), E(f["ml_note"]),
        img, E(ex.LABEL), E(str(x["resolution_h"])), cov_txt, E("; ".join(cov_det)),
        "".join(srows), len(x["arrivals"]), "".join(rows) or "<tr><td colspan=6>No mapped assets reached in the simulated window.</td></tr>",
        "".join(pl) or "<li>No population estimate available.</li>", E(pop["note"]),
        "".join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (E(s["what"]), E(s["source"]), E(s["attribution"])) for s in b["sources"]),
        "".join("<li>%s</li>" % E(l) for l in b["limitations"]))


def html_pdf(html_text: str, timeout: float = 90.0) -> bytes | None:
    """LibreOffice headless conversion (installed on the dev box); None when unavailable/failed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "brief.html"
        src.write_text(html_text, encoding="utf-8")
        try:
            subprocess.run([soffice, "--headless", "-env:UserInstallation=file://%s/profile" % d, "--convert-to",
                            "pdf:writer_web_pdf_Export", "--outdir", d, str(src)], timeout=timeout,
                           capture_output=True, check=False)
        except Exception:  # noqa: BLE001
            return None
        out = Path(d) / "brief.pdf"
        return out.read_bytes() if out.exists() else None


# ------------------------------------------------------------------ router
from typing import Annotated  # noqa: E402

from fastapi import Depends as _D, Query as _Q  # noqa: E402

Lat = Annotated[float, _Q(ge=-90, le=90)]
Lon = Annotated[float, _Q(ge=-180, le=180)]
_BRIEF_CACHE: dict = {}


def make_brief(lat, lon, start, hours, step_min, ignition, fuel, radius_km, top_n=25, state=None, sim_fn=None,
               population=True, buildings=True, with_png=True):
    expo, sim = ex.exposure_for(lat, lon, start, hours, step_min, ignition, fuel, radius_km, buildings, population,
                                top_n, sim_fn=sim_fn, state=state, return_sim=True)
    return brief_from(sim, expo, with_png)


def brief_from(sim, expo, with_png=True):
    q = sim.get("query") or {}
    now = dt.datetime.now(dt.timezone.utc)
    passes = next_passes(q.get("lat", 0.0), q.get("lon", 0.0), now)
    b = build_brief(sim, expo, now, passes)
    png = render_map_png(sim, expo) if with_png else None
    return b, png


def _router():
    from fastapi import APIRouter, Body, HTTPException, Query, Response
    r = APIRouter()

    def _gen(lat, lon, start, hours, step_min, ignition, fuel, radius_km, top_n, population, buildings, with_png):
        if ignition not in ("point", "live", "archive"):
            raise HTTPException(400, "ignition must be point, live or archive")
        key = (round(lat, 4), round(lon, 4), str(start), hours, step_min, ignition, fuel, round(radius_km, 2), top_n,
               population, buildings, with_png)
        hit = _BRIEF_CACHE.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        try:
            from backend import app as _app
            state = _app.store.state
        except Exception:  # noqa: BLE001
            _app, state = None, None

        def sim_fn(lat_, lon_, start_, hours_, step_, ign_, fuel_, rad_, st_):
            if _app is not None:
                return _app.simulate_get(lat=lat_, lon=lon_, start=start_, hours=hours_, step_min=step_, ignition=ign_,
                                         fuel=fuel_, envelope=True, frames=True, radius_km=rad_, calibration="on",
                                         weather="grid", goes="off", fwi=False, landscape="on")
            return ex.default_sim(lat_, lon_, start_, hours_, step_, ign_, fuel_, rad_, st_)
        try:
            res = make_brief(lat, lon, start, hours, step_min, ignition, fuel, radius_km, top_n, state, sim_fn,
                             population, buildings, with_png)
        except ValueError as e:
            raise HTTPException(404, "no simulated footprint: %s" % e) from None
        if len(_BRIEF_CACHE) > 6:
            _BRIEF_CACHE.pop(next(iter(_BRIEF_CACHE)))
        _BRIEF_CACHE[key] = (time.time(), res)
        return res

    def params(lat: Lat, lon: Lon, start: str | None = Query(None, description="YYYY-MM-DDTHH:MM UTC, default now"),
               hours: int = Query(48, ge=1, le=168), step_min: int = Query(60, ge=10, le=720),
               ignition: str = Query("live"), fuel: str = Query("auto"), radius_km: float = Query(30.0, ge=0.5, le=60.0),
               top_n: int = Query(25, ge=1, le=100), population: bool = Query(True), buildings: bool = Query(True)):
        return dict(lat=lat, lon=lon, start=start, hours=hours, step_min=step_min, ignition=ignition, fuel=fuel,
                    radius_km=radius_km, top_n=top_n, population=population, buildings=buildings)

    from fastapi import Depends

    @r.get("/brief", response_class=Response, tags=["brief"], summary="One-page incident brief (HTML)")
    @r.get("/brief.html", response_class=Response, tags=["brief"], include_in_schema=False)
    def brief_html(p: dict = Depends(params)):
        b, png = _gen(**p, with_png=True)
        return Response(render_html(b, png), media_type="text/html; charset=utf-8")

    @r.get("/brief.json", tags=["brief"], summary="Structured incident-brief content (no map image)")
    def brief_json(p: dict = Depends(params)) -> dict:
        b, _ = _gen(**p, with_png=False)
        return b

    @r.get("/brief.pdf", response_class=Response, tags=["brief"], summary="Incident brief as PDF (LibreOffice conversion)")
    def brief_pdf(p: dict = Depends(params)):
        b, png = _gen(**p, with_png=True)
        pdf = html_pdf(render_html(b, png))
        if pdf is None:
            raise HTTPException(501, "PDF conversion unavailable on this server; open /brief.html and print to PDF")
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="firespot_brief.pdf"'})

    @r.post("/brief.html", response_class=Response, tags=["brief"], summary="Brief for an already-run simulation")
    def brief_post(body: dict = Body(...), buildings: bool = Query(True), population: bool = Query(True)):
        sim = body.get("simulation", body)
        if not sim.get("frames"):
            raise HTTPException(400, "body needs frames[] with burned GeoJSON per frame")
        try:
            expo = ex.run_exposure(sim, buildings=buildings, population=population)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        b, png = brief_from(sim, expo, True)
        return Response(render_html(b, png), media_type="text/html; charset=utf-8")
    return r


router = _router()
