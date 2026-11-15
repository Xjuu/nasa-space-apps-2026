# FireSpot interface system

Mode: Operate. Precise, map-first analysis workspace for prolonged use in ordinary daylight and office settings.

Use white analysis surfaces, slate text (#1d2b34), light neutral separators (#dce3e6), and dark teal actions (#145c64). Reserve data colour for satellite ages, simulated timing, risk and alert severity. Keep viridis/cividis data ramps. No decorative gradients, neon, pink accents, oversized pills, nested cards or promotional hero.

Use Inter/system sans, 14px body, 12px field labels, 16px section headings, 23px page title and tabular figures. Controls have 5–6px corners and a visible focus ring. Desktop controls remain compact; touch targets are at least 44px on phones.

The global header contains the product identity, four underlined workspace tabs with satellite fire detections integrated into Monitor. One flat analysis rail uses separators and consistent edges. In Forecast, setup comes before area details. Detailed scientific limits use native disclosure, with the experimental/uncalibrated status always visible. Monitor uses red fire detection dots for the former tracking feed; the age ramp remains in Forecast. The player is always available in Forecast. Keep the fixed map sizing and zoom-coverage constraints.

References: `design/references/linear.DESIGN.md` for hierarchy/density and `vercel.DESIGN.md` for restrained controls and edges; use their principles without copying marketing layouts or identity. Impeccable Operate/quieter and Vercel Web Interface Guidelines govern functional polish. Taste Skill is installed; marketing-only hero/effect rules do not override this operational product brief.

Satellite positions and orbit tracks remain available as checked-by-default Layers overlays in all workspaces and in both map/globe views. Preserve user checkbox choices across tabs and reloads. Only Terra/Aqua (MODIS) and Suomi NPP/NOAA-20/NOAA-21 (VIIRS) are included; the separate tracking page stays retired.

## Dark theme (VS Code style)

Default theme. `frontend/theme-vscode.css` (loaded last) re-skins everything under `html[data-theme="dark"]`; the light Operate rules above are unchanged and remain the `light` theme. Header toggle `#theme-toggle` (sun/moon) stores the choice in `localStorage["equifire:theme"]`; an inline script in `<head>` sets it before first paint (default `dark`); `frontend/theme-vscode.js` handles the toggle.

Tokens: editor/map `#1e1e1e`; sidebar and header `#181818`; raised/inset surfaces `#1f1f1f` and `#252526`; separators `#2b2b2b`, control borders `#3c3c3c`; inputs `#313131`. Text `#cccccc`, headings `#e6e6e6`, muted `#9d9d9d`. Primary action `#0e639c` (hover `#1177bb`, white text); links, focus ring, active-tab underline and icons `#3794ff`; selection `#264f78`. Status: ok `#89d185` on `#16251a` (dot `#3fb950`), warning `#e2c04a`/`#cca700` on `#2b2410`, error `#f48771` on `#3a1d1d`. Corners 4-6px, 1px borders, no gradients, no glows; status chips are small pills. UI text 13px system sans; coordinates, numbers, times, ids use monospace (`ui-monospace, Cascadia, SF Mono, Consolas`). Thin dark scrollbars. Header is 52px on desktop; phone touch targets stay >= 44px.

Data colours keep their meaning and cues (dash, marker shape, hatch). Only lightness is lifted where a colour would vanish on dark: Okabe-Ito blue `#0072B2` to `#3aa0e6`, vermillion `#D55E00` to `#ee7d2c`, green `#009E73` to `#2fc79a`, grey `#7F7F7F` to `#a8a8a8`, harmonized teal `#145c64` to `#4fd1c0`. Viridis/cividis ramps are unchanged (empty cells `#2d2d2d`). The dark-purple "critical" alert level keeps its fill and gets a light outline `#d9bff2`/`#e3cff5`; near-black map outlines (`#010658`, `#1d2b34`) become light. SVG charts are recoloured with attribute selectors, and Leaflet canvas vectors by a draw-time colour swap in `theme-vscode.js` (preferCanvas). 2D map: OSM and Esri Topo tiles are darkened with `filter: invert(1) hue-rotate(180deg) ...` on their `.leaflet-layer` only (markers, overlays, radar and the Esri satellite imagery are not filtered).
