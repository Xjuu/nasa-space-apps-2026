/* EquiFire: 2D map + 3D globe wildfire workspace (live fires, satellites, weather, spread prediction) */
(function () {
  "use strict";

  // ---------------------------------------------------------------- Icons (lucide@0.344.0 from unpkg, no emojis)
  const ICONS = (window.lucide && window.lucide.icons) || {};
  const ATTR_ESC = (v) => String(v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function icon(name, cls) {
    const key = name.replace(/(^|-)([a-z0-9])/g, (m, a, b) => b.toUpperCase());
    const node = ICONS[key];
    if (!node) return "";
    const attrs = { ...node[1], class: `lucide lucide-${name}${cls ? " " + cls : ""}`, "aria-hidden": "true", focusable: "false" };
    const kids = (node[2] || []).map(([tag, a]) => `<${tag} ${Object.entries(a).map(([k, v]) => `${k}="${ATTR_ESC(v)}"`).join(" ")}/>`).join("");
    return `<svg ${Object.entries(attrs).map(([k, v]) => `${k}="${ATTR_ESC(v)}"`).join(" ")}>${kids}</svg>`;
  }
  function hydrateIcons(root) {
    (root || document).querySelectorAll("i[data-icon]").forEach((i) => {
      if (i.dataset.icon === i.dataset.done) return;
      i.innerHTML = icon(i.dataset.icon);
      i.dataset.done = i.dataset.icon;
      i.setAttribute("aria-hidden", "true");
    });
  }
  hydrateIcons();

  // "Estimated location" badge: the reason is shown as a tooltip on hover / focus / tap.
  function estBadge(reason, label) {
    return `<span class="est-badge" tabindex="0" role="note" data-tip="${ATTR_ESC(reason)}" title="${ATTR_ESC(reason)}" aria-label="${ATTR_ESC((label || "Estimated location") + ": " + reason)}">${icon("crosshair")}${esc(label || "Estimated location")}</span>`;
  }
  function estLine(reason, text, label) {
    return `<p class="est-line">${estBadge(reason, label)}${esc(text || "")}</p>`;
  }
  const SAT_NAMES = { N: "Suomi NPP", N20: "NOAA-20", N21: "NOAA-21", T: "Terra", A: "Aqua", MODIS: "MODIS (Terra/Aqua)" };
  function pixelReason(sat) {
    const modis = /MODIS|Terra|Aqua|^[TA]$/i.test(String(sat || ""));
    return modis
      ? "MODIS detection: the point is the centre of a ~1 km pixel (up to ~2 x 5 km near the swath edge). The fire can be anywhere inside that footprint."
      : "VIIRS detection: the point is the centre of a ~375 m pixel (up to ~0.8 km near the swath edge). The fire can be anywhere inside that footprint.";
  }

  // ---------------------------------------------------------------- Configuration
  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || (location.port === "5173" ? `${location.protocol}//${location.hostname}:8000` : location.origin)).replace(/\/+$/, "");
  const MAX_AREA_KM2 = 25000.0;
  const VIIRS_START = "2012-01-20";  // before this the archive is MODIS-only and reconstructed to VIIRS-equivalent
  const EARTH_RADIUS_KM = 6371.0088;

  // ML forecast days: cividis (colourblind-safe sequential, dark navy -> yellow)
  const CIVIDIS = ["#00224e", "#35456c", "#666970", "#948e77", "#c8b866", "#fee838"];
  const DAY_COLORS = ["#00224e", "#2a3f6c", "#575d6d", "#7d7c78", "#a69d75", "#d3c164", "#fee838"];
  const dayColor = (d) => DAY_COLORS[Math.min(Math.max(d, 1), DAY_COLORS.length) - 1];
  // Detection age: ordinal yellow / orange / dark purple (no red-green)
  const AGE_COLORS = { fresh: "#f6c445", mid: "#d9711c", old: "#5b2a86" };

  // DOM Elements
  const $ = (id) => document.getElementById(id);
  const el = {
    statusPill: $("status-pill"),
    btnViewGlobe: $("btn-view-globe"),
    btnViewMap: $("btn-view-map"),
    globeContainer: $("globe-container"),
    mapContainer: $("map"),
    areaBadge: $("area-badge"),
    areaText: $("area-text"),
    bboxCoords: $("bbox-coords"),
    bboxDims: $("bbox-dims"),
    btnShowFires: $("btn-show-fires"),
    firesInfo: $("fires-info"),
    error: $("error"),
    tabLive: $("tab-live"),
    tabDate: $("tab-date"),
    dateControls: $("date-controls"),
    liveResults: $("live-results"),
    date: $("date"),
    chkFires: $("chk-fires"),
    chkActual: $("chk-actual"),
    btnForecast: $("btn-forecast"),
    statActiveSpots: $("stat-active-spots"),
    statMaxFrp: $("stat-max-frp"),
    statLastTime: $("stat-last-time"),
    liveCountBadge: $("live-count-badge"),
    spotsContainer: $("spots-container"),
    results: $("results"),
    point: $("point"),
    anomaly: $("anomaly"),
    overpass: $("overpass"),
    forecastMsg: $("forecast-msg"),
    forecastBody: document.querySelector("#forecast-table tbody"),
    dlCap: $("dl-cap"),
    dlGeojson: $("dl-geojson"),
    api: $("api"),
    btnCenterBox: $("btn-center-box"),
    btnDrawBox: $("btn-draw-box"),
  };
  el.api.textContent = API;

  // ---------------------------------------------------------------- State
  const state = {
    view: "map", // 'globe' or 'map'
    mode: "live",  // 'live' or 'date'
    // Default to active Amazon hotspot (-6.57, -51.10) with ~24,500 km² box
    bbox: [-51.30, -6.80, -50.90, -6.40],
    center: { lat: -6.60, lon: -51.10 },
    areaKm2: 0,
    isValidArea: true,
    isDrawing: false,
    loadedFires: [],
    selectedPoint: null,
    backendReady: false,
    dateRange: [null, null],
  };

  // Fire-history mode (area of interest, burning calendar, charts)
  const hist = {
    aoi: null,          // {type: "bbox", bbox} | {type: "polygon", geometry, bbox}
    year: null,         // compared year (null = latest with data)
    metric: "modis_cell_days", // one sensor across the full archive; avoids the 2012 mixed-sensor jump
    calMode: "count",   // "count" (viridis) | "z" (blue-orange diverging)
    data: null,
    seq: 0,
    cache: new Map(),
    drawing: null,      // {kind: "rect" | "poly", pts: [LatLng]}
  };

  // ---------------------------------------------------------------- Area Math
  function computeBboxArea(west, south, east, north) {
    const dLon = ((east - west) % 360 + 360) % 360;
    const dLonRad = (dLon * Math.PI) / 180;
    const sinDiff = Math.abs(Math.sin((north * Math.PI) / 180) - Math.sin((south * Math.PI) / 180));
    return EARTH_RADIUS_KM * EARTH_RADIUS_KM * dLonRad * sinDiff;
  }

  function setBbox(w, s, e, n) {
    state.bbox = [
      Math.max(-180, Math.min(180, Math.min(w, e))),
      Math.max(-90, Math.min(90, Math.min(s, n))),
      Math.max(-180, Math.min(180, Math.max(w, e))),
      Math.max(-90, Math.min(90, Math.max(s, n))),
    ];
    state.center = {
      lat: (state.bbox[1] + state.bbox[3]) / 2,
      lon: (state.bbox[0] + state.bbox[2]) / 2,
    };
    state.areaKm2 = computeBboxArea(...state.bbox);
    if ($("location-lat")) $("location-lat").value = state.center.lat.toFixed(4);
    if ($("location-lon")) $("location-lon").value = state.center.lon.toFixed(4);
    state.isValidArea = state.areaKm2 <= MAX_AREA_KM2 + 1.0;

    // Approximate width and height in km
    const dLatKm = Math.abs(state.bbox[3] - state.bbox[1]) * 111.13;
    const midLatRad = (state.center.lat * Math.PI) / 180;
    const dLonKm = Math.abs(state.bbox[2] - state.bbox[0]) * 111.32 * Math.cos(midLatRad);

    // Update UI badge
    if (state.isValidArea) {
      el.areaBadge.className = "area-pill valid";
      el.areaText.textContent = `Area: ${state.areaKm2.toFixed(1)} km² / 25,000 km²`;
      el.btnShowFires.disabled = false;
      showError("");
    } else {
      el.areaBadge.className = "area-pill invalid";
      el.areaText.textContent = `Area ${state.areaKm2.toFixed(0)} km² exceeds the 25,000 km² maximum`;
      el.btnShowFires.disabled = true;
      showError(`Selected area is ${state.areaKm2.toFixed(1)} km², which exceeds the maximum limit of 25,000 km².\nPlease shrink the box.`);
    }

    el.bboxCoords.textContent = `${state.bbox[0].toFixed(3)}, ${state.bbox[1].toFixed(3)} to ${state.bbox[2].toFixed(3)}, ${state.bbox[3].toFixed(3)}`;
    el.bboxDims.textContent = `~${dLonKm.toFixed(1)} km × ${dLatKm.toFixed(1)} km`;

    updateVisualsOnBboxChange();
  }

  function createBoxAt(lat, lon, targetAreaKm2 = MAX_AREA_KM2 * 0.98) {
    const sideKm = Math.sqrt(targetAreaKm2);
    const dLat = sideKm / 111.13;
    const cosLat = Math.max(0.1, Math.cos((lat * Math.PI) / 180));
    const dLon = sideKm / (111.32 * cosLat);
    setBbox(lon - dLon / 2, lat - dLat / 2, lon + dLon / 2, lat + dLat / 2);
  }

  // ---------------------------------------------------------------- 2D Map (Leaflet)
  const MERCATOR_LIMIT = 85.0511287798;
  const map = L.map("map", {
    preferCanvas: true, zoomControl: false, worldCopyJump: true,
    maxBounds: [[-MERCATOR_LIMIT, -540], [MERCATOR_LIMIT, 540]], maxBoundsViscosity: 1,
  }).setView([state.center.lat, state.center.lon], 9);
  function constrainMapViewport() {
    const height = el.mapContainer.clientHeight;
    if (!height || !el.mapContainer.clientWidth) return;
    // At zoom 0 the Mercator world is only 256 px tall. Prevent zooming out until
    // it is shorter than the map viewport, which exposes the grey polar bands.
    const minZoom = Math.max(0, Math.ceil(Math.log2(height / 256)));
    if (map.getMinZoom() !== minZoom) map.setMinZoom(minZoom);
    map.panInsideBounds(map.options.maxBounds, {animate: false});
  }
  const mapSize = new ResizeObserver(() => {
    if (!el.mapContainer.clientHeight || !el.mapContainer.clientWidth) return;
    map.invalidateSize({pan: false, debounceMoveend: true});
    constrainMapViewport();
  });
  mapSize.observe(el.mapContainer);
  constrainMapViewport();
  L.control.zoom({ position: "bottomright" }).addTo(map);

  // Keyless basemaps (CARTO now returns "API key required" tiles to browsers)
  const baseOSM = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const baseSat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 18,
    attribution: "Imagery &copy; Esri",
  });
  const baseTopo = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 18,
    attribution: "&copy; Esri",
  });
  const BASEMAPS = { osm: baseOSM, sat: baseSat, topo: baseTopo };
  document.querySelectorAll('input[name="basemap"]').forEach((r) => r.addEventListener("change", () => {
    if (!r.checked) return;
    Object.values(BASEMAPS).forEach((l) => map.removeLayer(l));
    BASEMAPS[r.value].addTo(map).bringToBack();
  }));

  map.createPane("detectionDots").style.zIndex = 430;
  const firesLayer = L.layerGroup().addTo(map);
  const predLayer = L.layerGroup().addTo(map);
  const actualLayer = L.layerGroup().addTo(map);
  let bboxRectangle = null;
  let pointMarker = null;

  function updateLeafletBbox() {
    const [w, s, e, n] = state.bbox;
    const bounds = [[s, w], [n, e]];
    const color = state.isValidArea ? "#514cf1" : "#a1173d";

    if (bboxRectangle) {
      bboxRectangle.setBounds(bounds);
      bboxRectangle.setStyle({ color, fillColor: color });
    } else {
      bboxRectangle = L.rectangle(bounds, {
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.06,
        dashArray: "6 5",
        interactive: false,
      }).addTo(map);
    }
  }

  // Shared rectangle tool: mouse drag or two pointer taps, in each area workspace.
  let drawStartLatLng = null;
  let drawTempRect = null;
  let drawTargetMode = null;
  let lastDrawCompleted = 0;
  const drawHint = $("map-draw-hint");
  const mapDrawButton = $("map-draw-area");

  function startDrawMode() {
    if (state.view !== "map") switchView("map");
    if (hist.drawing) endAoiDraw();
    stopDrawMode();
    state.isDrawing = true;
    drawTargetMode = state.mode;
    [el.btnDrawBox, mapDrawButton].forEach(button => {
      button.classList.add("active"); button.setAttribute("aria-pressed", "true");
      button.querySelector("span").textContent = "Cancel drawing";
    });
    drawHint.textContent = "Drag a rectangle, or tap two opposite corners.";
    drawHint.hidden = false;
    map.getContainer().style.cursor = "crosshair";
    map.dragging.disable();
    map.doubleClickZoom.disable();
  }

  function stopDrawMode() {
    state.isDrawing = false;
    [el.btnDrawBox, mapDrawButton].forEach(button => {
      button.querySelector("span").textContent = "Draw area";
      button.setAttribute("aria-pressed", "false"); button.classList.remove("active");
    });
    drawHint.hidden = true;
    map.getContainer().style.cursor = "";
    map.dragging.enable(); map.doubleClickZoom.enable();
    if (drawTempRect) { drawTempRect.remove(); drawTempRect = null; }
    drawStartLatLng = null;
    drawTargetMode = null;
  }

  function drawBounds(cursor) {
    const bounds = L.latLngBounds(drawStartLatLng, cursor);
    if (!drawTempRect) drawTempRect = L.rectangle(bounds, {
      color: "#145c64", weight: 2, fillOpacity: .12, dashArray: "6 5", interactive: false,
    }).addTo(map);
    else { drawTempRect.setBounds(bounds); drawTempRect.setStyle({color: "#145c64"}); }
    return bounds;
  }

  function finishAreaDraw(cursor) {
    const bounds = drawBounds(cursor);
    if (map.latLngToContainerPoint(drawStartLatLng).distanceTo(map.latLngToContainerPoint(cursor)) < 4) {
      drawHint.textContent = "Now select the opposite corner.";
      return;
    }
    const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
    const [w, south, east, north] = bbox;
    const area = computeBboxArea(w, south, east, north);
    const invalid = drawTargetMode === "history"
      ? east - w > AOI_MAX_SPAN || north - south > AOI_MAX_SPAN
      : area > MAX_AREA_KM2 + 1;
    if (!(east > w && north > south) || invalid) {
      drawHint.textContent = drawTargetMode === "history"
        ? "Area too large. Draw a smaller rectangle (maximum 40° × 40°)."
        : "Area too large. Draw a smaller rectangle (maximum 25,000 km²).";
      drawTempRect.setStyle({color: "#b42318"});
      drawStartLatLng = null;
      return;
    }
    const mode = drawTargetMode;
    lastDrawCompleted = Date.now();
    stopDrawMode();
    if (mode === "history") setAoi({type: "bbox", bbox}, {fit: false});
    else {
      setBbox(...bbox);
      if (bboxRectangle && !map.hasLayer(bboxRectangle)) bboxRectangle.addTo(map);
      if (mode === "date" && state.selectedPoint &&
          !(state.selectedPoint.lon >= w && state.selectedPoint.lon <= east && state.selectedPoint.lat >= south && state.selectedPoint.lat <= north)) {
        state.selectedPoint = null; pointSeq++;
        if (pointMarker) { pointMarker.remove(); pointMarker = null; }
        $("sel-point").hidden = true;
        sim.reset("Area changed. Select a fire in this area.");
      }
      if (mode === "date" || mode === "live") loadFires();
    }
    if (window.EquiFire && window.EquiFire.emit) window.EquiFire.emit("area", {
      mode, bbox, center: {lat: (south+north)/2, lon: (w+east)/2},
    });
  }

  const drawingSurface = map.getContainer();
  function drawingPoint(event) {
    const point = map.mouseEventToLatLng(event);
    return L.latLng(Math.max(-85, Math.min(85, point.lat)), Math.max(-180, Math.min(180, point.lng)));
  }
  drawingSurface.addEventListener("pointerdown", event => {
    if (!state.isDrawing || event.isPrimary === false || (event.button != null && event.button !== 0) || event.target.closest(".leaflet-control,.leaflet-popup")) return;
    event.preventDefault(); event.stopPropagation();
    if (!drawStartLatLng) drawStartLatLng = drawingPoint(event);
    drawBounds(drawingPoint(event));
    drawingSurface.setPointerCapture(event.pointerId);
  }, true);
  drawingSurface.addEventListener("pointermove", event => {
    if (!state.isDrawing || !drawStartLatLng) return;
    drawBounds(drawingPoint(event));
  }, true);
  drawingSurface.addEventListener("pointerup", event => {
    if (!state.isDrawing || !drawStartLatLng) return;
    event.preventDefault(); event.stopPropagation();
    if (drawingSurface.hasPointerCapture(event.pointerId)) drawingSurface.releasePointerCapture(event.pointerId);
    finishAreaDraw(drawingPoint(event));
  }, true);
  drawingSurface.addEventListener("pointercancel", () => { if (state.isDrawing) stopDrawMode(); }, true);
  drawingSurface.addEventListener("click", event => {
    if ((state.isDrawing || Date.now() - lastDrawCompleted < 300) && !event.target.closest(".leaflet-control")) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  mapDrawButton.addEventListener("click", () => state.isDrawing ? stopDrawMode() : startDrawMode());

  map.on("click", (e) => {
    if (Date.now() - lastDrawCompleted < 250 || state.isDrawing || hist.drawing || state.mode === "alerts") return;
    if (state.mode === "history") {
      setAoiSquare(e.latlng.lat, e.latlng.lng);
      return;
    }
    if (state.mode === "date") {
      // In date mode, click point runs forecast
      analyzePoint(e.latlng.lat, e.latlng.lng);
    }
  });

  // ---------------------------------------------------------------- 3D Globe (Globe.gl)
  let globe = null;

  function initGlobe() {
    if (globe) return;
    const container = el.globeContainer;

    globe = Globe()(container)
      .globeImageUrl("assets/earth-blue-marble.jpg")
      .bumpImageUrl("assets/earth-topology.png")
      .backgroundColor("#020433")
      .atmosphereColor("#8f8cff")
      .atmosphereAltitude(0.16)
      .pointLat("lat")
      .pointLng("lon")
      .pointColor("color")
      .pointAltitude("altitude")
      .pointRadius("radius")
      .pointLabel("tooltip")
      .ringsData([])
      .ringLat("lat")
      .ringLng("lon")
      .ringColor(() => (t) => `rgba(255, 107, 53, ${Math.max(0, 1 - t)})`)
      .ringMaxRadius(0.8)
      .ringPropagationSpeed(1.6)
      .ringRepeatPeriod(900)
      .polygonsData([])
      .polygonGeoJsonGeometry((d) => d.geometry)
      .polygonCapColor((d) => { const p = d.properties || {}; return p.sim ? p.fill : p.aoi ? "rgba(86, 180, 233, 0.14)" : "rgba(143, 140, 255, 0.18)"; })
      .polygonSideColor((d) => { const p = d.properties || {}; return p.sim ? p.fill : p.aoi ? "rgba(86, 180, 233, 0.3)" : "rgba(143, 140, 255, 0.3)"; })
      .polygonStrokeColor((d) => { const p = d.properties || {}; return p.sim ? "#ffffff" : p.aoi ? "#56B4E9" : "#b9b7ff"; })
      .polygonAltitude((d) => ((d.properties || {}).sim ? 0.004 : 0.008));
    globe.pointsData($("lyr-fires").checked ? (state.globePoints || []) : []);

    sats.attachGlobe(globe);

    // Initial camera placement
    globe.pointOfView({ lat: state.center.lat, lng: state.center.lon, altitude: 2.2 }, 0);

    // Globe click interaction: center box and focus camera
    globe.onGlobeClick(({ lat, lng }) => {
      if (state.mode === "alerts") return;
      if (state.mode === "history") {
        setAoiSquare(lat, lng);
        return;
      }
      createBoxAt(lat, lng, MAX_AREA_KM2 * 0.98);
      globe.pointOfView({ lat, lng, altitude: 0.8 }, 800);
      if (state.isValidArea) {
        loadFires();
      }
    });

    globe.onPointClick((point) => {
      if (point && point.lat != null && point.lon != null) {
        if (state.mode === "date") {
          if (point.live && !sim.usesLiveFires()) {
            showError("This is a current detection. Select Nearest live fire to forecast it instead of the archive date.");
            return;
          }
          analyzePoint(point.lat, point.lon);
        } else {
          globe.pointOfView({ lat: point.lat, lng: point.lon, altitude: 0.5 }, 600);
        }
      }
    });

    updateGlobeBbox();
  }

  function updateGlobeBbox() {
    if (!globe) return;
    const [w, s, e, n] = state.bbox;
    const polygonFeature = {
      type: "Feature",
      properties: {},
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [w, s],
            [e, s],
            [e, n],
            [w, n],
            [w, s],
          ],
        ],
      },
    };
    const feats = state.mode === "history" || state.mode === "alerts" ? [] : [polygonFeature];
    if (hist.aoi && state.mode === "history") {
      feats.push({ type: "Feature", properties: { aoi: true }, geometry: hist.aoi.type === "polygon" ? hist.aoi.geometry : bboxGeometry(hist.aoi.bbox) });
    }
    if (state.mode === "date" && typeof sim !== "undefined") feats.push(...sim.globeFeatures());
    globe.polygonsData(feats);
  }

  function updateVisualsOnBboxChange() {
    updateLeafletBbox();
    updateGlobeBbox();
  }

  // ---------------------------------------------------------------- View Switcher
  function switchView(viewName) {
    state.view = viewName;
    if (viewName === "map" && typeof wx !== "undefined") setTimeout(() => wx.loadWind(), 300);
    el.btnViewMap.setAttribute("aria-pressed", String(viewName === "map"));
    el.btnViewGlobe.setAttribute("aria-pressed", String(viewName === "globe"));
    if (viewName === "globe") {
      el.btnViewGlobe.classList.add("active");
      el.btnViewMap.classList.remove("active");
      el.globeContainer.style.display = "block";
      el.mapContainer.style.display = "none";
      if (!globe) {
        initGlobe();
        resizeViews();
      } else {
        globe.pointOfView({ lat: state.center.lat, lng: state.center.lon, altitude: 2.2 }, 500);
      }
    } else {
      el.btnViewMap.classList.add("active");
      el.btnViewGlobe.classList.remove("active");
      el.mapContainer.style.display = "block";
      el.globeContainer.style.display = "none";
      map.invalidateSize();
      map.fitBounds([[state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]], {padding: [60, 60], maxZoom: 12, animate: false});
    }
  }

  el.btnViewGlobe.addEventListener("click", () => switchView("globe"));
  el.btnViewMap.addEventListener("click", () => switchView("map"));

  // ---------------------------------------------------------------- Mode Switcher
  function switchMode(modeName) {
    if (state.isDrawing) stopDrawMode();
    state.mode = modeName;
    $("map-draw-area").hidden = modeName === "alerts";
    (window.EquiFire && window.EquiFire._modeHooks || []).forEach((fn) => { try { fn(modeName); } catch (e) { console.error(e); } });
    firesSeq++;
    pointSeq++;
    firesLayer.clearLayers();predLayer.clearLayers();actualLayer.clearLayers();
    state.loadedFires=[];state.globePoints=[];state.selectedPoint=null;el.btnForecast.disabled=true;
    if(pointMarker){map.removeLayer(pointMarker);pointMarker=null;}
    if(globe){globe.pointsData([]);globe.ringsData([]);}
    const labels = {live: ["Area overview", "Latest satellite observations", "Last 24 hours · MODIS & VIIRS"], date: ["Fire spread prediction", "Pick a fire, set a horizon, press Run", "Click a fire or a point to select it"], history: ["Historical activity", "Compare activity across years", "Selected historical area"], alerts: ["Activity alerts", "Regions above their seasonal baseline", "Recent activity against historical baseline"], replay: ["Historical replay", "Issued forecast first, then what actually happened", "Replay of a past forecast"]};
    const copy = labels[modeName] || labels.live;
    $("workspace-eyebrow").textContent = ({live: "Monitor", date: "Forecast", history: "History", alerts: "Alerts", replay: "Replay"})[modeName];
    $("workspace-title").textContent = copy[0];
    $("workspace-description").textContent = copy[1];
    $("map-context-description").textContent = copy[2];
    document.querySelector("#map-context .context-label").textContent = ({live:"Observation map",date:"Forecast map",history:"Historical area",alerts:"Regional activity",replay:"Replay map"})[modeName];
    const legend=document.querySelector(".map-legend");legend.hidden=modeName === "history" || modeName === "replay";
    legend.querySelectorAll(".lg-fires").forEach(item => {item.hidden = modeName === "history";});
    legend.querySelector(".lg-area").hidden = modeName === "history";
    sim.reset();
    if (modeName !== "date") sim.hidePlayback();
    else sim.showPlayback();
    sim.updateLegend();
    if (modeName === "date") sim.checkService();
    document.querySelectorAll(".mode-tab").forEach(button => {
      if (button.id === `tab-${modeName === "date" ? "date" : modeName}`) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    const isHistory = modeName === "history";
    const isAlerts = modeName === "alerts";
    $("tab-alerts").classList.toggle("active", isAlerts);
    $("alerts").hidden = !isAlerts;
    el.tabLive.classList.toggle("active", modeName === "live");
    el.tabDate.classList.toggle("active", modeName === "date");
    $("tab-history").classList.toggle("active", isHistory);
    el.liveResults.hidden = modeName !== "live";
    el.dateControls.hidden = modeName !== "date";
    if (modeName !== "date") el.results.hidden = true;
    const isReplay = modeName === "replay";
    $("area-section").hidden = isHistory || isAlerts || isReplay;
    $("history").hidden = !isHistory;
    el.areaBadge.hidden = isHistory || isAlerts || isReplay;
    if (bboxRectangle) {
      if (isHistory || isReplay) bboxRectangle.remove();
      else if (!map.hasLayer(bboxRectangle)) bboxRectangle.addTo(map);
    }
    if (!isHistory && hist.drawing) endAoiDraw();
    updateGlobeBbox();
    // bigger charts on desktop while in history mode
    const panel = document.getElementById("panel");
    const wide = isHistory && window.innerWidth > 1100;
    if (panel.classList.contains("wide") !== wide) {
      panel.classList.toggle("wide", wide);
      $("hist-wide").setAttribute("aria-pressed", wide);
      setTimeout(resizeViews, 50);
    }
    if (!isAlerts) leaveAlerts();
    if (isHistory) enterHistory();
    else if (isAlerts) enterAlerts();
    else if (!isReplay) loadFires();
    if (showsGlobalDetections()) loadMonitorDetections();
  }

  el.tabLive.addEventListener("click", () => switchMode("live"));
  el.tabDate.addEventListener("click", () => switchMode("date"));
  $("tab-history").addEventListener("click", () => switchMode("history"));
  $("tab-alerts").addEventListener("click", () => switchMode("alerts"));

  // ---------------------------------------------------------------- Helpers & API
  function showError(msg) {
    el.error.textContent = msg;
    el.error.hidden = !msg;
  }

  async function getJSON(path, query, options) {
    const url = API + path + (query ? "?" + new URLSearchParams(query) : "");
    let res;
    try {
      res = await fetch(url, options);
    } catch (e) {
      throw new Error("The observation service is unavailable. Please try loading the data again.");
    }
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch (e) {}
    if (!res.ok) {
      let detail = body && (body.detail || body.error || body.message);
      if (detail && typeof detail !== "string") detail = JSON.stringify(detail);
      throw new Error(detail || text.slice(0, 200) || `HTTP ${res.status}`);
    }
    if (body === null) throw new Error(`${path}: response is not JSON`);
    return body;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------------------------------------------------------------- Load Fires
  let firesSeq = 0;
  let monitorDetections = [];
  let monitorRequest = null;
  let monitorLoadedAt = 0;

  function showsGlobalDetections() { return ["live", "date", "alerts"].includes(state.mode); }
  // Shared current FIRMS context for Monitor, Forecast and Alerts.
  // Its 250 highest-FRP detections are context, not an exhaustive global count.
  async function loadMonitorDetections() {
    if (monitorRequest) return monitorRequest;
    if (Date.now() - monitorLoadedAt < 60000) {
      if (showsGlobalDetections()) renderOnGlobeAndMap(state.loadedFires);
      return;
    }
    const note = $("monitor-feed-status");
    $("btn-global-fires").disabled = true;
    note.textContent = "Loading global satellite detections…";
    monitorRequest = (async () => {
      try {
        const data = await getJSON("/api/fires", { satellite: "ALL" });
        if (data.warning) throw new Error(data.warning);
        monitorDetections = (data.fires || []).filter(f => Number.isFinite(f.latitude) && Number.isFinite(f.longitude)).map(f => {
          const time = String(f.acq_time || "0000").padStart(4, "0");
          const observed = `${f.acq_date}T${time.slice(0,2)}:${time.slice(2,4)}:00Z`;
          return {type: "Feature", geometry: {type: "Point", coordinates: [f.longitude, f.latitude]},
            properties: {kind: "live_fire", global_context: true, lat: f.latitude, lon: f.longitude, frp_mw: Number(f.frp || 0),
              satellite: f.satellite, confidence: f.confidence, last_seen_time: observed,
              hours_ago: Math.max(0, (Date.now() - Date.parse(observed)) / 3600000)}};
        });
        monitorLoadedAt = Date.now();
        note.textContent = `${monitorDetections.length} strongest global detections (24 h) plus your area. Not a full global count.${data.note ? " " + data.note : ""}`;
      } catch (err) {
        note.textContent = `Global detections unavailable${monitorDetections.length ? "; showing earlier data" : ""}. Use Load detections to retry.`;
      } finally {
        monitorRequest = null;
        $("btn-global-fires").disabled = !monitorDetections.length;
        if (showsGlobalDetections()) renderOnGlobeAndMap(state.loadedFires);
      }
    })();
    return monitorRequest;
  }


  $("btn-global-fires").addEventListener("click", () => {
    if (!monitorDetections.length) return;
    if (state.view === "globe" && globe) globe.pointOfView({lat: 0, lng: 0, altitude: 2.2}, 800);
    else {
      map.stop();
      map.fitBounds(monitorDetections.map(f => [f.properties.lat, f.properties.lon]), {padding: [36, 36], maxZoom: 5, animate: false});
    }
  });

  async function loadFires() {
    if (showsGlobalDetections()) loadMonitorDetections();
    if (!state.isValidArea) {
      showError(`Area is ${state.areaKm2.toFixed(1)} km². Maximum allowed is 25,000 km².`);
      return;
    }

    const seq = ++firesSeq;
    el.btnShowFires.disabled = true;
    el.spotsContainer.setAttribute("aria-busy", "true");
    const [w, s, e, n] = state.bbox;
    const bboxStr = `${w.toFixed(4)},${s.toFixed(4)},${e.toFixed(4)},${n.toFixed(4)}`;

    el.firesInfo.textContent = "Loading satellite observations…";
    showError("");

    try {
      let gj;
      const liveFeed = state.mode === "live" || (state.mode === "date" && sim.usesLiveFires());
      if (liveFeed) {
        // NASA FIRMS near-real-time global feed via EquiFire (last 24 h, no API key)
        gj = await getJSON("/live/fires", { bbox: bboxStr, hours: 24, limit: 5000 });
      } else {
        // Query archive / specific date
        gj = await getJSON("/fires", { date: el.date.value, bbox: bboxStr, max_area: MAX_AREA_KM2 });
      }

      if (seq !== firesSeq) return;
      gj.features = normaliseDetections(gj.features || []);
      state.loadedFires = gj.features;

      if (state.mode === "live") {
        renderLiveSpots(gj);
      } else if (liveFeed) {
        el.firesInfo.textContent = `${gj.features.length} detections in the last 24 h. Click one to select it.`;
      } else {
        renderDateFires(gj);
      }

      renderOnGlobeAndMap(state.loadedFires);
    } catch (err) {
      if (seq !== firesSeq) return;
      el.firesInfo.textContent = "";
      showError(err.message);
    } finally {
      if (seq === firesSeq) {
        el.btnShowFires.disabled = !state.isValidArea;
        el.spotsContainer.setAttribute("aria-busy", "false");
      }
    }
  }

  // Live fires for the whole visible map (public FIRMS NRT feed, no key, up to 40 x 40 deg)
  async function loadLiveFiresView() {
    const seq = ++firesSeq;
    const b = map.getBounds();
    const w = Math.max(b.getWest(), -180), e = Math.min(b.getEast(), 180);
    const s = Math.max(b.getSouth(), -90), n = Math.min(b.getNorth(), 90);
    if (e - w > 40 || n - s > 40) {
      showError("Zoom in: live view covers at most 40 x 40 degrees.");
      return;
    }
    el.firesInfo.textContent = "Loading detections…";
    showError("");
    try {
      const gj = await getJSON("/live/fires", { bbox: `${w.toFixed(3)},${s.toFixed(3)},${e.toFixed(3)},${n.toFixed(3)}`, hours: 24, limit: 5000 });
      if (seq !== firesSeq) return;
      gj.features = normaliseDetections(gj.features || []);
      state.loadedFires = gj.features;
      renderLiveSpots({ ...gj, features: state.loadedFires.slice(0, 150), summary: gj.summary });
      el.firesInfo.textContent = `${gj.summary.total_spots} detections in view, last 24 h. List shows the newest 150.`;
      renderOnGlobeAndMap(state.loadedFires);
    } catch (err) {
      if (seq !== firesSeq) return;
      el.firesInfo.textContent = "";
      showError(err.message);
    }
  }

  function normaliseDetections(features) {
    return features.map(feature => {
      const p = feature.properties || {};
      const coords = feature.geometry?.type === "Point" ? feature.geometry.coordinates : [];
      if (!coords.length && p.lat == null && p.lon == null) return feature;
      return {...feature, properties: {...p, lat: Number(p.lat ?? coords[1]), lon: Number(p.lon ?? coords[0]),
        frp_mw: Number(p.frp_mw ?? p.frp ?? p.max_frp_24h ?? 0),
        last_seen_time: p.last_seen_time ?? p.observed_at ?? p.last_seen ?? null}};
    });
  }

  // ---------------------------------------------------------------- Render Live Feed
  function renderLiveSpots(gj) {
    const summary = gj.summary || {};
    const features = normaliseDetections(gj.features || []).filter(f => Number.isFinite(f.properties.lat) && Number.isFinite(f.properties.lon));
    const spotsCount = summary.total_spots || features.length;
    const maxFrp = Math.max(Number(summary.max_frp_mw || 0), ...features.map(f => f.properties.frp_mw || 0));

    el.statActiveSpots.textContent = spotsCount;
    el.statMaxFrp.textContent = `${maxFrp.toFixed(1)} MW`;
    el.liveCountBadge.textContent = `${spotsCount} locations`;

    if (!features.length) {
      el.statLastTime.textContent = "-";
      el.firesInfo.textContent = `No detections in this area in the last 24 h.`;
      el.spotsContainer.innerHTML = `<p class="muted small empty-notice">No detections. Draw another area or pick a quick location.</p>`;
      return;
    }

    // Determine freshest time
    let freshestHours = Infinity;
    features.forEach((f) => {
      const h = f.properties && f.properties.hours_ago;
      if (typeof h === "number" && h < freshestHours) freshestHours = h;
    });

    el.statLastTime.textContent = freshestHours < Infinity ? (freshestHours < 1 ? "Just now" : `${freshestHours}h ago`) : "Today";
    el.firesInfo.textContent = `${spotsCount.toLocaleString()} detection locations · ${Number(summary.area_km2 || state.areaKm2).toLocaleString(undefined, {maximumFractionDigits: 1})} km² · last 24 hours`;

    // Render list of spots (sorted by recency and peak FRP)
    let html = "";
    features.forEach((f, idx) => {
      const p = f.properties || {};
      const frp = p.frp_mw || 0;
      const frpPct = Math.min(100, Math.max(10, (frp / Math.max(maxFrp, 10)) * 100));
      const hoursAgo = p.hours_ago != null ? `${p.hours_ago}h ago` : "Last 24h";
      const timeStr = p.last_seen_time ? String(p.last_seen_time).slice(11, 16) + " UTC" : "";
      const tempC = p.brightness_c != null ? `${p.brightness_c.toFixed(0)} °C` : "";

      html += `
        <button type="button" class="spot-item" data-idx="${idx}" data-lat="${p.lat}" data-lon="${p.lon}">
          <div class="spot-header">
            <span class="spot-coord">${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}</span>
            <span class="spot-time">${timeStr || hoursAgo}</span>
          </div>
          <div class="spot-details">
            <span>${frp.toFixed(1)} MW</span>
            <div class="frp-bar-wrap"><div class="frp-bar" style="width:${frpPct}%"></div></div>
            <span>${tempC}</span>
            <span class="badge-pill">${esc(({N: "Suomi NPP", N20: "NOAA-20", N21: "NOAA-21"})[p.satellite] || p.satellite || "VIIRS")}</span>
          </div>
        </button>
      `;
    });

    el.spotsContainer.innerHTML = html;

    // Click spot to zoom in
    el.spotsContainer.querySelectorAll(".spot-item").forEach((item) => {
      item.addEventListener("click", () => {
        el.spotsContainer.querySelectorAll(".spot-item").forEach(row => {row.classList.remove("selected");row.setAttribute("aria-pressed", "false");});
        item.classList.add("selected");item.setAttribute("aria-pressed", "true");
        const lat = +item.getAttribute("data-lat");
        const lon = +item.getAttribute("data-lon");
        if (state.view === "globe" && globe) {
          globe.pointOfView({ lat, lng: lon, altitude: 0.4 }, 800);
        } else {
          map.setView([lat, lon], 12);
          firesLayer.eachLayer(layer => {if(layer.getLatLng && layer.getLatLng().distanceTo(L.latLng(lat,lon)) < 5) layer.openPopup();});
        }
      });
    });
  }

  // ---------------------------------------------------------------- Render Date Fires
  function renderDateFires(gj) {
    const features = gj.features || [];
    const count = features.length;
    el.firesInfo.textContent = `${count} burning 5 km cells observed on ${el.date.value} in this area.` +
      (el.date.value < VIIRS_START ? " Before 20 Jan 2012 only MODIS (1 km) exists; cells are reconstructed to VIIRS-equivalent." : "");
  }

  // ---------------------------------------------------------------- Visuals Sync (Globe + Map)
  function fireAgeColor(h) {
    if (h == null) return AGE_COLORS.mid;
    return h <= 3 ? AGE_COLORS.fresh : h <= 12 ? AGE_COLORS.mid : AGE_COLORS.old;
  }

  function renderOnGlobeAndMap(features) {
    if (showsGlobalDetections()) {
      const seen = new Set();
      features = [...features, ...monitorDetections].filter(f => {
        const p = f.properties || {}, c = f.geometry?.coordinates || [];
        const key = `${p.lat ?? c[1]},${p.lon ?? c[0]},${p.satellite || ""},${p.last_seen_time || ""}`;
        if (seen.has(key)) return false;
        seen.add(key); return true;
      });
    }
    firesLayer.clearLayers();
    const globePoints = [];

    features.forEach((f) => {
      const geom = f.geometry || {};
      let lat, lon;
      if (geom.type === "Point") {
        lon = geom.coordinates[0];
        lat = geom.coordinates[1];
      } else if (geom.type === "Polygon") {
        const ring = geom.coordinates[0];
        lon = (ring[0][0] + ring[1][0] + ring[2][0] + ring[3][0]) / 4;
        lat = (ring[0][1] + ring[1][1] + ring[2][1] + ring[3][1]) / 4;
      } else {
        return;
      }

      const p = f.properties || {};
      const frp = p.frp_equiv_mw || p.frp_mw || p.frp_viirs_mw || 0;
      const isLive = p.kind === "live_fire";
      const isCell = geom.type === "Polygon";
      const color = isLive || state.mode === "live" ? "#c43c32" : AGE_COLORS.mid;
      const radius = Math.min(4 + Math.sqrt(frp) / 2.5, 10);
      const satName = SAT_NAMES[p.satellite] || p.satellite || "";

      let reason;
      if (isCell) {
        reason = "Archive cells are 0.05° (~5 km) grid squares; the marker is the cell centre, not the fire position." +
          (el.date.value < VIIRS_START ? " Before 20 Jan 2012 the record is MODIS-only, reconstructed to VIIRS-equivalent." : "");
      } else {
        reason = pixelReason(p.satellite);
      }

      const marker = isCell
        ? L.geoJSON(f, { bubblingMouseEvents: false, style: { color: "#010658", weight: 1, fillColor: color, fillOpacity: 0.55 } })
        : L.circleMarker([lat, lon], { radius, fillColor: color, color: state.mode === "live" ? "#ffffff" : "#010658", weight: 1.2, fillOpacity: 0.92, bubblingMouseEvents: false, pane: "detectionDots" });
      marker.addTo(firesLayer);
      marker.on("click", () => {
        if (state.isDrawing || Date.now() - lastDrawCompleted < 300) return;
        if (state.mode === "date" && !(p.global_context && !sim.usesLiveFires())) analyzePoint(lat, lon);
      });

      const popupHtml = `
        <div class="popup-title">${isCell ? "Burning cell (archive)" : "Satellite fire detection"}</div>
        <div class="popup-badges"><span class="popup-badge">${isLive ? "Live · last 24 h" : "Archive " + esc(el.date.value)}</span>${estBadge(reason)}</div>
        ${p.global_context && state.mode === "date" && !sim.usesLiveFires() ? '<p class="muted small">Current detection context; separate from the selected archive date. Use Predict spread to start a live forecast.</p>' : ""}
        <table class="popup-table">
          <tr><td>${isCell ? "Cell centre" : "Pixel centre"}</td><td><b>${lat.toFixed(4)}, ${lon.toFixed(4)}</b></td></tr>
          <tr><td>Fire power</td><td><b>${frp.toFixed(1)} MW</b></td></tr>
          ${p.last_seen_time ? `<tr><td>Last seen</td><td>${esc(String(p.last_seen_time).replace("T", " ").replace("Z", " UTC"))}${p.hours_ago != null ? ` (${p.hours_ago} h ago)` : ""}</td></tr>` : ""}
          ${p.brightness_c ? `<tr><td>Temperature</td><td>${p.brightness_c.toFixed(1)} °C</td></tr>` : ""}
          ${satName ? `<tr><td>Satellite</td><td>${esc(satName)}</td></tr>` : ""}
          ${p.confidence ? `<tr><td>Detection confidence</td><td>${esc(p.confidence)}</td></tr>` : ""}
          ${p.detections_viirs != null ? `<tr><td>Detections</td><td>VIIRS ${p.detections_viirs} · MODIS ${p.detections_modis}</td></tr>` : ""}
        </table>
        <div class="popup-actions"><button type="button" class="js-predict" data-lat="${lat}" data-lon="${lon}" data-live="${isLive ? 1 : 0}">${icon("trending-up")}Predict spread</button></div>`;
      marker.bindPopup(popupHtml, { maxWidth: 300 });

      const tooltip = `<div class="fire-tip"><b>${isCell ? "Burning cell" : "Fire detection"}</b> (estimated location)<br>${lat.toFixed(3)}, ${lon.toFixed(3)}<br>FRP ${frp.toFixed(1)} MW${p.last_seen_time ? "<br>Seen " + esc(String(p.last_seen_time).slice(11, 16)) + " UTC" : ""}${satName ? "<br>" + esc(satName) : ""}</div>`;
      globePoints.push({ lat, lon, color, altitude: 0.01 + Math.min(frp / 250, 0.08), radius: Math.min(0.25 + Math.sqrt(frp) / 12, 0.7), tooltip, live: isLive });
    });

    if (globe) globe.ringsData([]);
    state.globePoints = globePoints;
    syncFiresVisible();
  }

  // "Predict spread" from any fire popup: switch to the Forecast tab with this fire selected
  map.on("popupopen", (e) => {
    const btn = e.popup.getElement() && e.popup.getElement().querySelector(".js-predict");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const lat = +btn.dataset.lat, lon = +btn.dataset.lon;
      map.closePopup();
      startPrediction(lat, lon, btn.dataset.live === "1" ? "live" : null);
    });
  });

  function startPrediction(lat, lon, ignition) {
    if (ignition) { $("sim-ignition").value = ignition; sim.syncControls(true); }
    if (state.mode !== "date") switchMode("date");
    if (state.view === "map") map.setView([lat, lon], Math.max(map.getZoom(), 11));
    analyzePoint(lat, lon);
    $("date-controls").scrollIntoView({ block: "nearest" });
  }

  // ---------------------------------------------------------------- Point Analysis (Forecast, Anomaly, Overpass)
  let pointSeq = 0;
  let geojsonUrl = null;

  async function analyzePoint(lat, lon) {
    state.selectedPoint = { lat, lon };
    el.btnForecast.disabled = false;
    const seq = ++pointSeq;
    const days = Math.ceil(+$("sim-hours").value / 24);
    const date = sim.usesLiveFires() ? new Date().toISOString().slice(0, 10) : el.date.value;
    const q = { lat: lat.toFixed(4), lon: lon.toFixed(4), date, days };

    if (pointMarker) pointMarker.setLatLng([lat, lon]);
    else pointMarker = L.circleMarker([lat, lon], { radius: 8, color: "#fff", weight: 3, fillColor: "#514cf1", fillOpacity: 1 }).addTo(map);

    showError("");
    el.results.hidden = false;
    el.point.textContent = `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
    sim.onPointSelected(lat, lon);
    el.anomaly.textContent = "Comparing historical activity…";
    el.overpass.innerHTML = '<li class="muted">Loading passes…</li>';
    $("ml-result").hidden = true;
    $("forecast-est").innerHTML = "";
    predLayer.clearLayers();
    actualLayer.clearLayers();

    getJSON("/classify", { lat: q.lat, lon: q.lon, date })
      .then((c) => { if (seq === pointSeq) renderClassify(c); })
      .catch(() => getJSON("/anomaly", { lat: q.lat, lon: q.lon, date })   // fallback: plain z-score
        .then((a) => { if (seq === pointSeq) renderAnomaly(a); })
        .catch(() => { if (seq === pointSeq) el.anomaly.textContent = "No historical baseline for this point and date."; }));

    getJSON("/overpass", { lat: q.lat, lon: q.lon })
      .then((o) => { if (seq === pointSeq) renderOverpass(o); })
      .catch(() => { el.overpass.innerHTML = '<li class="muted">Pass predictions unavailable.</li>'; });

  }

  function renderClassify(c) {
    const label = c.label || "-";
    const hot = /anomal/i.test(label);
    const pct = (v) => (v == null ? "-" : Math.round(v * 100) + "%");
    const parts = [
      `<span class="anomaly-label${hot ? " hot" : ""}">${esc(label)}</span>`,
      `routine ${pct(c.routine_score)} &bull; anomaly ${pct(c.anomaly_score)}`,
      `this week: ${Math.round(c.week_cell_days)} burning cell-days vs usual ${c.baseline_mean.toFixed(1)}` +
        (c.z != null ? ` (z ${c.z.toFixed(1)})` : ""),
      `in season in ${pct(c.season_freq)} of other years`,
    ];
    const reasons = (c.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
    parts.unshift(`<span class="muted small">On ${esc(c.date || el.date.value)}, 0.25° region</span>`);
    el.anomaly.innerHTML = parts.join("<br>") + (reasons ? `<ul class="reasons">${reasons}</ul>` : "") +
      (c.date && c.date < VIIRS_START ? estLine("Before 20 Jan 2012 only MODIS (1 km) data exist; counts are reconstructed to VIIRS-equivalent and are less certain.", "MODIS-era data, reconstructed.", "Reconstructed data") : "");
  }

  function renderAnomaly(a) {
    const z = a.z != null ? a.z : a.zscore;
    const label = a.label || "-";
    const isAnom = a.anomaly === true || /anomal/i.test(String(label));
    const parts = [`<span class="anomaly-label${isAnom ? " hot" : ""}">${esc(label)}</span>`];
    if (z != null) parts.push(`z-score: <b>${z.toFixed(2)}</b>`);
    if (a.observed != null && a.baseline_mean != null) {
      parts.push(`observed: ${a.observed} vs baseline: ${a.baseline_mean.toFixed(1)}`);
    }
    el.anomaly.innerHTML = parts.join(" &bull; ");
  }

  function renderOverpass(o) {
    let list = Array.isArray(o) ? o : o.passes || [];
    if (!list.length) {
      el.overpass.innerHTML = '<li class="muted">No passes found for next 24h.</li>';
      return;
    }
    el.overpass.innerHTML = list.slice(0, 6).map((p) => {
      const sat = p.satellite || p.name || "?";
      const min = p.minutes_until != null ? p.minutes_until : 0;
      const m = min >= 60 ? `${Math.floor(min / 60)}h ${Math.round(min % 60)}m` : `${Math.round(min)} min`;
      const time = p.best ? String(p.best).replace("T", " ").slice(11, 16) + " UTC" : "";
      return `<li><span>${icon("satellite")} <b>${esc(sat)}</b>${p.instrument ? ` <span class="muted">${esc(p.instrument)}</span>` : ""}</span><span class="muted">${time} (in ${m})${p.pixel_km ? ` · pixel ${p.pixel_km[0]}×${p.pixel_km[1]} km` : ""}</span></li>`;
    }).join("");
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(h));
  }

  function renderForecast(fc, days, date, q) {
    $("ml-result").querySelector("h3").textContent = "ML forecast";
    const features = fc.features || [];
    const pred = [], act = [], burning = [], risk = [];
    features.forEach((f) => {
      const kind = String((f.properties || {}).kind || "").toLowerCase();
      if (kind === "burning" || kind === "start") burning.push(f);
      else if (kind === "actual" || kind === "observed") act.push(f);
      else if (kind === "risk_area") risk.push(f);
      else pred.push(f);
    });
    const dayOf = (f) => +(f.properties || {}).day || 1;
    risk.forEach((f) => {
      L.geoJSON(f, { interactive: false, style: { color: dayColor(dayOf(f)), weight: 1.5, dashArray: "2 4", fill: false } }).addTo(predLayer);
    });
    pred.slice().sort((x, y) => dayOf(y) - dayOf(x)).forEach((f) => {
      const d = dayOf(f);
      L.geoJSON(f, { style: { color: "#010658", weight: 0.4, fillColor: dayColor(d), fillOpacity: 0.7 } })
        .bindTooltip(`Day ${d} predicted cell (~5 km)`).addTo(predLayer);
    });
    burning.forEach((f) => {
      L.geoJSON(f, { style: { color: "#ffffff", weight: 0.8, fillColor: "#1b1b2f", fillOpacity: 0.8 } }).bindTooltip("Burning at start").addTo(predLayer);
    });
    act.forEach((f) => {
      L.geoJSON(f, { interactive: false, style: { color: "#010658", weight: 1.8, dashArray: "4 3", fill: false } }).addTo(actualLayer);
    });

    const summary = fc.summary || {};
    const perDay = {};
    (summary.per_day || []).forEach((r) => { perDay[r.day] = r; });
    let rows = "";
    for (let d = 1; d <= days; d++) {
      const r = perDay[d] || {};
      rows += `<tr>
          <td><span class="swatch" style="background:${dayColor(d)}"></span></td>
          <td><b>${d}</b></td>
          <td>${esc(r.date || "")}</td>
          <td class="num">${r.predicted_cells != null ? r.predicted_cells : "-"}</td>
          <td class="num">${r.observed_cells != null ? r.observed_cells : "-"}</td>
          <td class="num"><b>${r.iou != null ? Math.round(r.iou * 100) + "%" : "-"}</b></td>
        </tr>`;
    }
    el.forecastBody.innerHTML = rows;
    try { const bb = predLayer.getBounds(); if (bb.isValid() && state.view === "map") map.fitBounds(bb, { padding: [60, 60], maxZoom: 11 }); } catch (e) {}
    el.forecastMsg.textContent = `${pred.length} cells (~5 km) predicted to newly burn over ${days} day${days > 1 ? "s" : ""}, starting from ${burning.length} burning cells on ${date}.`;

    // the model starts from the nearest burning cell: say so when it moved the point
    const est = [];
    const c = summary.centre;
    if (c && q) {
      const km = haversineKm(+q.lat, +q.lon, c.lat, c.lon);
      est.push(estLine(`The fire centre was snapped to the nearest burning 0.05° cell (centre ${c.lat.toFixed(3)}, ${c.lon.toFixed(3)}), ${km.toFixed(1)} km from the point you selected. All cells are ~5 km squares, so positions are approximate.`,
        `Fire centre snapped ${km.toFixed(1)} km to the nearest burning 5 km cell.`));
    }
    if (date < VIIRS_START) est.push(estLine("Before 20 Jan 2012 the starting fire comes from MODIS (1 km) detections reconstructed to VIIRS-equivalent cells.", "Starting cells reconstructed from MODIS-era data.", "Reconstructed data"));
    $("forecast-est").innerHTML = est.join("");

    if (geojsonUrl) URL.revokeObjectURL(geojsonUrl);
    geojsonUrl = URL.createObjectURL(new Blob([JSON.stringify(fc)], { type: "application/geo+json" }));
    el.dlGeojson.href = geojsonUrl;
    el.dlGeojson.setAttribute("download", `firespot-forecast-${date}.geojson`);
    el.dlCap.href = `${API}/forecast.cap?${new URLSearchParams(q)}`;
    el.dlCap.setAttribute("download", `firespot-${date}.cap.xml`);
  }

  function renderCombined(fc, days, date, q) {
    const selected = (fc.features || []).filter(f => f.properties && f.properties.selected);
    renderForecast({features: selected, summary: {per_day: (fc.per_day || []).map(r => ({
      day: r.day, predicted_cells: r.selected_cells,
      date: new Date(Date.parse(date + "T00:00:00Z") + r.day * 86400000).toISOString().slice(0, 10)
    }))}}, days, date, q);
    el.forecastMsg.textContent = "Daily ML + physics risk per ~5 km cell. Local accuracy is unknown.";
    $("ml-result").querySelector("h3").textContent = "ML + physics forecast";
    $("forecast-est").innerHTML = (fc.warnings || []).map(w => estLine(w, w, "Model note")).join("");
    if (geojsonUrl) URL.revokeObjectURL(geojsonUrl);
    geojsonUrl = URL.createObjectURL(new Blob([JSON.stringify(fc)], {type: "application/geo+json"}));
    el.dlGeojson.href = geojsonUrl;
    el.dlGeojson.setAttribute("download", `firespot-combined-${date}.geojson`);
    el.dlCap.hidden = true;
  }

  // ---------------------------------------------------------------- Quickbars & Preset Actions
  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const lat = +chip.getAttribute("data-lat");
      const lon = +chip.getAttribute("data-lon");
      createBoxAt(lat, lon, MAX_AREA_KM2 * 0.98);

      if (state.view === "globe" && globe) {
        globe.pointOfView({ lat, lng: lon, altitude: 0.9 }, 900);
      } else {
        map.fitBounds([[state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]], {padding: [48, 48], maxZoom: 11});
      }
      loadFires();
    });
  });

  el.btnCenterBox.addEventListener("click", () => {
    let lat, lon;
    if (state.view === "globe" && globe) {
      const pov = globe.pointOfView();
      lat = pov.lat;
      lon = pov.lng;
    } else {
      const c = map.getCenter();
      lat = c.lat;
      lon = c.lng;
    }
    createBoxAt(lat, lon, MAX_AREA_KM2 * 0.98);
    loadFires();
  });

  el.btnDrawBox.addEventListener("click", () => {
    if (state.isDrawing) {stopDrawMode();setBbox(...state.bbox);el.firesInfo.textContent = "Area selection cancelled.";return;}
    if (state.view !== "map") {
      switchView("map");
    }
    startDrawMode();
  });

  $("location-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const lat = Number($("location-lat").value), lon = Number($("location-lon").value);
    createBoxAt(lat, lon);
    if (state.view === "map") map.fitBounds([[state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]], {padding: [48, 48]});
    else if (globe) globe.pointOfView({lat, lng: lon, altitude: .5}, 450);
    loadFires();
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && state.isDrawing) {
      stopDrawMode();el.firesInfo.textContent = "Area selection cancelled.";mapDrawButton.focus();
    }
  });
  el.btnShowFires.addEventListener("click", loadFires);
  el.btnForecast.addEventListener("click", () => {
    if (!state.selectedPoint) return;
    sim.run();
  });
  function syncFiresVisible() {
    const on = $("lyr-fires").checked && (state.mode !== "date" || el.chkFires.checked);
    if (on) firesLayer.addTo(map); else firesLayer.remove();
    if (globe) globe.pointsData(on ? (state.globePoints || []) : []);
  }
  el.chkFires.addEventListener("change", syncFiresVisible);
  $("lyr-fires").addEventListener("change", syncFiresVisible);
  el.chkActual.addEventListener("change", () => (el.chkActual.checked ? actualLayer.addTo(map) : actualLayer.remove()));
  el.date.addEventListener("change", () => {
    if (state.mode === "date" && !sim.usesLiveFires()) sim.syncControls(false);
  });

  // ---------------------------------------------------------------- Fire history: area of interest
  // Colorblind-safe palette (Okabe-Ito orange / sky blue; viridis for counts; blue-orange diverging for z)
  const CB = { orange: "#C26A00", bar: "#E69F00", sky: "#2F6DB5", text: "#20246c", muted: "#4a4e78", grid: "#e3e3f1", empty: "#ececf6", sel: "#010658" };
  const VIRIDIS = ["#440154", "#482878", "#3e4a89", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"];
  // blue-orange diverging with a light neutral centre (readable on white)
  const DIVERGING = ["#2166ac", "#67a2d3", "#bcd7ec", "#f2f2f5", "#f9c98b", "#e88f2c", "#a14d00"];
  const AOI_MAX_SPAN = 40;
  const Z_THR = 2;
  const MONTHS = "JFMAMJJASOND";
  const METRIC_UNIT = {
    viirs_cell_days: "VIIRS burning cell-days",
    viirs_detections: "VIIRS detections",
    modis_cell_days: "MODIS burning cell-days",
    modis_detections: "MODIS detections",
    any_cell_days: "burning cell-days (any sensor)",
    frp_equiv_mw: "MW harmonized FRP",
  };

  // optional History honesty hooks (frontend/ui-history-fix.js); every call is a no-op if that file is missing
  const HF = (fn, arg) => (window.EqHistFix && typeof window.EqHistFix[fn] === "function" ? window.EqHistFix[fn](arg) : "");

  const aoiLayer = L.layerGroup().addTo(map);
  const draftLayer = L.layerGroup().addTo(map);
  const AOI_STYLE = { color: CB.sky, weight: 2, fillColor: CB.sky, fillOpacity: 0.08, dashArray: "6 4" };

  function bboxGeometry(b) {
    const [w, s, e, n] = b;
    return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
  }

  function hexRgb(h) {
    const v = parseInt(h.slice(1), 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }
  function ramp(stops, t) {
    t = Math.max(0, Math.min(1, isFinite(t) ? t : 0));
    const x = t * (stops.length - 1);
    const i = Math.min(Math.floor(x), stops.length - 2);
    const f = x - i;
    const a = hexRgb(stops[i]), b = hexRgb(stops[i + 1]);
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
  }
  const fmtN = (v) => {
    if (v == null || !isFinite(v)) return "-";
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(1) + "k";
    if (a >= 100) return Math.round(v).toLocaleString("en-US");
    return String(Math.round(v * 10) / 10);
  };
  function isoWeekStart(y, w) {
    const jan4 = new Date(Date.UTC(y, 0, 4));
    const dow = jan4.getUTCDay() || 7;
    return new Date(Date.UTC(y, 0, 4 - dow + 1 + (w - 1) * 7));
  }
  function weekRange(y, w) {
    const a = isoWeekStart(y, w), b = new Date(a.getTime() + 6 * 864e5);
    const f = (d) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
    return `${f(a)} - ${f(b)}`;
  }
  // ISO week (fractional) where each month starts, for axis ticks
  const MONTH_WEEK = [1, 5.4, 9.4, 13.7, 18, 22.4, 26.7, 31.1, 35.6, 39.9, 44.3, 48.6];

  function setHistStatus(msg, isError) {
    const p = $("hist-status");
    p.textContent = msg || "";
    p.classList.toggle("error-text", !!isError);
  }

  // ---- area of interest
  function setAoi(aoi, opts = {}) {
    const [w, s, e, n] = aoi.bbox;
    if (!(e > w && n > s)) {
      setHistStatus("The area is empty: max lon/lat must be greater than min lon/lat.", true);
      return;
    }
    if (e - w > AOI_MAX_SPAN || n - s > AOI_MAX_SPAN) {
      setHistStatus(`Area too large (${(e - w).toFixed(1)}° × ${(n - s).toFixed(1)}°): at most ${AOI_MAX_SPAN}° × ${AOI_MAX_SPAN}°.`, true);
      return;
    }
    hist.aoi = aoi;
    aoiLayer.clearLayers();
    const shape = aoi.type === "polygon"
      ? L.polygon(aoi.geometry.coordinates[0].map(([x, y]) => [y, x]), AOI_STYLE)
      : L.rectangle([[s, w], [n, e]], AOI_STYLE);
    shape.addTo(aoiLayer);
    updateGlobeBbox();
    const midLat = ((s + n) / 2) * Math.PI / 180;
    const km2 = (e - w) * (n - s) * 111.32 * 111.13 * Math.cos(midLat);
    $("aoi-summary").textContent = `${aoi.type === "polygon" ? "Polygon in " : ""}${w.toFixed(2)}, ${s.toFixed(2)} to ${e.toFixed(2)}, ${n.toFixed(2)}` +
      ` (${aoi.type === "polygon" ? "box " : ""}~${fmtN(km2)} km²)`;
    $("aoi-bbox").value = [w, s, e, n].map((v) => +v.toFixed(3)).join(",");
    $("aoi-lat").value = ((s + n) / 2).toFixed(3);
    $("aoi-lon").value = ((w + e) / 2).toFixed(3);
    if (opts.fit !== false) {
      if (state.view === "map") map.fitBounds([[s, w], [n, e]], { padding: [20, 20], maxZoom: 9 });
      else if (globe) globe.pointOfView({ lat: (s + n) / 2, lng: (w + e) / 2, altitude: Math.min(2.2, Math.max(0.35, (n - s) / 12)) }, 700);
    }
    loadStats();
  }

  function setAoiSquare(lat, lon, size) {
    const h = (size || +$("aoi-size").value || 1) / 2;
    setAoi({ type: "bbox", bbox: [Math.max(-180, lon - h), Math.max(-90, lat - h), Math.min(180, lon + h), Math.min(90, lat + h)] }, { fit: false });
  }

  function enterHistory() {
    if (!hist.aoi) {
      setAoi({ type: "bbox", bbox: [state.center.lon - 1, state.center.lat - 1, state.center.lon + 1, state.center.lat + 1] });
    } else if (hist.data) {
      renderHistory(hist.data);
    }
  }

  // ---- drawing (click-based: works with mouse and touch)
  function startAoiDraw(kind) {
    if (state.view !== "map") {
      switchView("map");
      if (hist.aoi) {
        const [w, s, e, n] = hist.aoi.bbox;
        map.fitBounds([[s, w], [n, e]], { padding: [20, 20], maxZoom: 9 });
      }
    }
    if (state.isDrawing) stopDrawMode();
    endAoiDraw();
    hist.drawing = { kind, pts: [] };
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = "crosshair";
    $(kind === "rect" ? "aoi-rect" : "aoi-poly").classList.add("active");
    $("aoi-draw-hint").hidden = false;
    $("aoi-finish").hidden = kind === "rect";
    $("aoi-draw-text").textContent = kind === "rect"
      ? "Click (or tap) one corner, then the opposite corner."
      : "Click to add points; click the first point, double-click or Finish to close.";
  }

  function endAoiDraw() {
    hist.drawing = null;
    draftLayer.clearLayers();
    map.doubleClickZoom.enable();
    map.getContainer().style.cursor = "";
    $("aoi-rect").classList.remove("active");
    $("aoi-poly").classList.remove("active");
    $("aoi-draw-hint").hidden = true;
  }

  const clampLL = (ll) => L.latLng(Math.max(-90, Math.min(90, ll.lat)), Math.max(-180, Math.min(180, ll.lng)));

  function finishPolygon() {
    const d = hist.drawing;
    if (!d || d.pts.length < 3) {
      $("aoi-draw-text").textContent = "A polygon needs at least 3 points.";
      return;
    }
    const ring = d.pts.map((p) => [+p.lng.toFixed(5), +p.lat.toFixed(5)]);
    ring.push(ring[0]);
    const xs = ring.map((p) => p[0]), ys = ring.map((p) => p[1]);
    endAoiDraw();
    setAoi({ type: "polygon", geometry: { type: "Polygon", coordinates: [ring] },
      bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] }, { fit: false });
  }

  function drawDraft(cursor) {
    const d = hist.drawing;
    draftLayer.clearLayers();
    if (!d || !d.pts.length) return;
    if (d.kind === "rect") {
      if (cursor) L.rectangle(L.latLngBounds(d.pts[0], cursor), { ...AOI_STYLE, interactive: false }).addTo(draftLayer);
    } else {
      const line = cursor ? d.pts.concat([cursor]) : d.pts;
      L.polyline(line, { color: CB.sky, weight: 2, dashArray: "4 4", interactive: false }).addTo(draftLayer);
    }
    d.pts.forEach((p, i) => L.circleMarker(p, { radius: i === 0 ? 6 : 4, color: "#fff", weight: 1.5, fillColor: CB.sky, fillOpacity: 1, interactive: false }).addTo(draftLayer));
  }

  map.on("click", (e) => {
    const d = hist.drawing;
    if (!d) return;
    const ll = clampLL(e.latlng);
    if (d.kind === "rect") {
      if (!d.pts.length) {
        d.pts.push(ll);
        drawDraft(ll);
        return;
      }
      const b = L.latLngBounds(d.pts[0], ll);
      endAoiDraw();
      setAoi({ type: "bbox", bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] }, { fit: false });
      return;
    }
    const pt = map.latLngToContainerPoint(ll);
    if (d.pts.length >= 3 && pt.distanceTo(map.latLngToContainerPoint(d.pts[0])) < 14) {
      finishPolygon();
      return;
    }
    const last = d.pts[d.pts.length - 1];
    if (last && pt.distanceTo(map.latLngToContainerPoint(last)) < 4) return; // double-click duplicate
    d.pts.push(ll);
    drawDraft(ll);
    $("aoi-draw-text").textContent = `${d.pts.length} point${d.pts.length > 1 ? "s" : ""}. Click the first point, double-click or Finish to close.`;
  });
  map.on("mousemove", (e) => { if (hist.drawing && hist.drawing.pts.length) drawDraft(clampLL(e.latlng)); });
  map.on("dblclick", () => { if (hist.drawing && hist.drawing.kind === "poly") finishPolygon(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && hist.drawing) endAoiDraw(); });

  $("aoi-rect").addEventListener("click", () => startAoiDraw("rect"));
  $("aoi-poly").addEventListener("click", () => startAoiDraw("poly"));
  $("aoi-finish").addEventListener("click", finishPolygon);
  $("aoi-cancel").addEventListener("click", endAoiDraw);
  $("aoi-view").addEventListener("click", () => {
    if (state.view === "map") {
      const b = map.getBounds();
      const w = Math.max(-180, b.getWest()), e = Math.min(180, b.getEast());
      const s = Math.max(-90, b.getSouth()), n = Math.min(90, b.getNorth());
      const cx = (w + e) / 2, cy = (s + n) / 2;
      const hx = Math.min((e - w) / 2, AOI_MAX_SPAN / 2), hy = Math.min((n - s) / 2, AOI_MAX_SPAN / 2);
      setAoi({ type: "bbox", bbox: [cx - hx, cy - hy, cx + hx, cy + hy] }, { fit: false });
    } else if (globe) {
      const pov = globe.pointOfView();
      setAoiSquare(pov.lat, pov.lng);
    }
  });
  $("aoi-point-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const lat = parseFloat($("aoi-lat").value), lon = parseFloat($("aoi-lon").value);
    if (!(Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) {
      setHistStatus("Latitude must be -90..90 and longitude -180..180.", true);
      return;
    }
    const h = +$("aoi-size").value / 2;
    setAoi({ type: "bbox", bbox: [Math.max(-180, lon - h), Math.max(-90, lat - h), Math.min(180, lon + h), Math.min(90, lat + h)] });
  });
  $("aoi-bbox-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("aoi-bbox").value.split(/[\s,;]+/).filter(Boolean).map(Number);
    if (v.length !== 4 || v.some((x) => !isFinite(x)) || Math.abs(v[0]) > 180 || Math.abs(v[2]) > 180 || Math.abs(v[1]) > 90 || Math.abs(v[3]) > 90) {
      setHistStatus("Enter 4 numbers: min lon, min lat, max lon, max lat.", true);
      return;
    }
    setAoi({ type: "bbox", bbox: [Math.min(v[0], v[2]), Math.min(v[1], v[3]), Math.max(v[0], v[2]), Math.max(v[1], v[3])] });
  });
  $("hist-year").addEventListener("change", (e) => { hist.year = +e.target.value || null; loadStats(); });
  $("hist-metric").addEventListener("change", (e) => {
    hist.metric = e.target.value;
    if (hist.data && hist.year) {
      const py = hist.data.per_year.find((r) => r.year === hist.year);
      if (!py || py[hist.metric] == null) hist.year = null;
    }
    loadStats();
  });
  function setCalMode(m) {
    hist.calMode = m;
    $("cal-count").classList.toggle("active", m === "count");
    $("cal-z").classList.toggle("active", m === "z");
    $("cal-count").setAttribute("aria-pressed", m === "count");
    $("cal-z").setAttribute("aria-pressed", m === "z");
    if (hist.data) renderCalendar(hist.data);
  }
  $("cal-count").addEventListener("click", () => setCalMode("count"));
  $("cal-z").addEventListener("click", () => setCalMode("z"));
  $("hist-wide").addEventListener("click", () => {
    const wide = document.getElementById("panel").classList.toggle("wide");
    $("hist-wide").setAttribute("aria-pressed", wide);
    setTimeout(resizeViews, 50);
  });
  function resizeViews() {
    map.invalidateSize();
    constrainMapViewport();
    if (globe) globe.width(el.globeContainer.clientWidth).height(el.globeContainer.clientHeight);
  }
  window.addEventListener("resize", () => { clearTimeout(resizeViews.t); resizeViews.t = setTimeout(resizeViews, 150); });

  // ---- data
  async function postJSON(path, body) {
    let res;
    try {
      res = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (e) {
      throw new Error("The observation service is unavailable. Please try loading the data again.");
    }
    const text = await res.text();
    let j = null;
    try { j = text ? JSON.parse(text) : null; } catch (e) {}
    if (!res.ok) {
      let detail = j && j.detail;
      if (detail && typeof detail !== "string") detail = JSON.stringify(detail);
      throw new Error(detail || text.slice(0, 200) || `HTTP ${res.status}`);
    }
    return j;
  }

  async function loadStats() {
    if (!hist.aoi) return;
    const seq = ++hist.seq;
    const q = { metric: hist.metric, z_threshold: Z_THR };
    if (hist.year) q.year = hist.year;
    let key, run;
    if (hist.aoi.type === "polygon") {
      const body = { geometry: hist.aoi.geometry, ...q };
      key = "POST " + JSON.stringify(body);
      run = () => postJSON("/stats", body);
    } else {
      q.bbox = hist.aoi.bbox.map((v) => v.toFixed(4)).join(",");
      key = "GET " + new URLSearchParams(q);
      run = () => getJSON("/stats", q);
    }
    setHistStatus("Loading fire history for this area...");
    try {
      let d = hist.cache.get(key);
      if (!d) {
        d = await run();
        if (hist.cache.size > 30) hist.cache.clear();
        hist.cache.set(key, d);
      }
      if (seq !== hist.seq) return;
      hist.data = d;
      hist.year = d.selected.year;
      setHistStatus(d.note || "");
      renderHistory(d);
    } catch (err) {
      if (seq !== hist.seq) return;
      const loading = /loading/i.test(err.message);
      setHistStatus(loading ? "Archive is still loading on the server; retrying in 10 s..." : err.message, !loading);
      if (loading) setTimeout(() => { if (seq === hist.seq) loadStats(); }, 10000);
    }
  }

  // leave-one-year-out mean / std / z for every cell of the calendar (rows = years with data)
  function calendarStats(rows, grp) {
    const out = rows.map(() => new Array(53).fill(null));
    for (let w = 0; w < 53; w++) {
      const vals = rows.map((r) => r.v[w]);
      rows.forEach((r, i) => {
        const x = vals[i];
        const base = vals.filter((v, j) => j !== i && v != null && (!grp || grp(rows[j].y) === grp(r.y)));
        if (base.length < 2) {
          out[i][w] = { mean: null, z: null };
          return;
        }
        const mean = base.reduce((a, b) => a + b, 0) / base.length;
        const sd = Math.sqrt(base.reduce((a, b) => a + (b - mean) ** 2, 0) / (base.length - 1));
        const z = x == null ? null : (x - mean) / Math.max(sd, Math.sqrt(Math.max(mean, 1)));
        out[i][w] = { mean, z };
      });
    }
    return out;
  }

  function renderHistory(d) {
    $("hist-body").hidden = false;
    // year selector: years with data for this metric
    const sel = $("hist-year");
    const yrs = d.per_year.filter((r) => r[d.metric] != null).map((r) => r.year).reverse();
    sel.innerHTML = yrs.map((y) => `<option value="${y}"${y === d.selected.year ? " selected" : ""}>${y}</option>`).join("");
    $("hist-metric").value = d.metric;
    renderKpis(d);
    renderCalendar(d);
    renderWeekly(d);
    renderYearly(d);
    const blob = new Blob([JSON.stringify(d)], { type: "application/json" });
    if (renderHistory.url) URL.revokeObjectURL(renderHistory.url);
    renderHistory.url = URL.createObjectURL(blob);
    $("hist-dl").href = renderHistory.url;
    const csv = ["year," + Array.from({ length: 53 }, (_, i) => "w" + (i + 1)).join(",")]
      .concat(d.years.map((y, i) => y + "," + d.calendar[d.metric][i].map((v) => (v == null ? "" : v)).join(","))).join("\n");
    if (renderHistory.csv) URL.revokeObjectURL(renderHistory.csv);
    renderHistory.csv = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    $("hist-csv").href = renderHistory.csv;
    $("hist-csv").setAttribute("download", `firespot-calendar-${d.metric}.csv`);
    HF("after", d);
  }

  function renderKpis(d) {
    const k = d.selected.key;
    const unit = METRIC_UNIT[d.metric] || d.metric;
    const set = (id, val, lbl, cls) => {
      const box = $(id);
      box.className = "kpi" + (cls ? " " + cls : "");
      box.querySelector(".kpi-val").innerHTML = val;
      box.querySelector(".kpi-lbl").textContent = lbl;
    };
    const period = k.weeks_with_data < 52 ? ` (${k.weeks_with_data} weeks)` : "";
    set("kpi-total", fmtN(k.value), `${unit} in ${k.year}${period}`);
    const pct = k.pct_vs_mean;
    set("kpi-pct", pct == null ? "-" : `${pct > 0 ? icon("arrow-up") + "+" : pct < 0 ? icon("arrow-down") : ""}${Math.round(pct)}%`,
      `vs ${k.baseline_years.length}-year average (${fmtN(k.baseline_mean)})`, pct == null ? "" : pct > 0 ? "warm" : pct < 0 ? "cool" : "");
    const z = k.z;
    set("kpi-z", z == null ? "-" : (z > 0 ? "+" : "") + z.toFixed(1), "z-score of the year total",
      z == null ? "" : z >= Z_THR ? "hot" : z <= -Z_THR ? "cold" : "");
    const lbl = k.label || "-";
    const hot = /^anomaly/.test(lbl), low = /low/.test(lbl);
    const nA = k.anomalous_weeks.length;
    set("kpi-label", hot ? "ANOMALY" : low ? "LOW" : /routine/.test(lbl) ? "ROUTINE" : lbl.toUpperCase(),
      `${lbl.replace(/^[a-z]+: /, "")}; ${nA} anomalous week${nA === 1 ? "" : "s"}`, hot ? "hot" : low ? "cold" : "ok");
  }

  // ---- burning calendar (weeks x years heatmap)
  function renderCalendar(d) {
    const cal = d.calendar[d.metric];
    const rows = d.years.map((y, i) => ({ y, v: cal[i] })).filter((r) => r.v.some((x) => x != null));
    const box = $("cal-chart");
    if (!rows.length) {
      box.innerHTML = '<p class="muted small">No data for this measure.</p>';
      return;
    }
    const st = calendarStats(rows, HF("calendarGroup", d) || null);
    $("cal-info").textContent = "Tap a cell for details, a row to compare years.";
    const selWeeks = {};
    d.selected.weeks.forEach((w) => { if (w.flag) selWeeks[w.week - 1] = w.flag; });
    const W = 360, L = 30, R = 46, T = 12, ch = 10, cw = (W - L - R) / 53;
    const H = T + rows.length * ch + 14;
    let max = 0;
    rows.forEach((r) => r.v.forEach((v) => { if (v != null && v > max) max = v; }));
    const lmax = Math.log1p(max || 1);
    const zMode = hist.calMode === "z";
    let s = "";
    MONTH_WEEK.forEach((mw, i) => {
      s += `<text x="${(L + (mw - 1) * cw).toFixed(1)}" y="8" class="ax">${MONTHS[i]}</text>`;
    });
    rows.forEach((r, ri) => {
      const y = T + ri * ch;
      const isSel = r.y === d.selected.year;
      s += `<text x="${L - 3}" y="${y + ch - 2}" text-anchor="end" class="ax${isSel ? " sel" : ""}" data-i="${ri}" data-w="-1">${r.y}</text>`;
      for (let w = 0; w < 53; w++) {
        const v = r.v[w];
        if (v == null) continue;
        let fill;
        if (zMode) {
          const z = st[ri][w].z;
          fill = z == null ? CB.empty : v === 0 && (st[ri][w].mean || 0) === 0 ? CB.empty : ramp(DIVERGING, (Math.max(-3, Math.min(3, z)) + 3) / 6);
        } else {
          fill = v === 0 ? CB.empty : ramp(VIRIDIS, Math.log1p(v) / lmax);
        }
        s += `<rect x="${(L + w * cw).toFixed(2)}" y="${y}" width="${(cw - 0.5).toFixed(2)}" height="${ch - 1}" fill="${fill}" data-i="${ri}" data-w="${w}"/>`;
      }
      if (isSel) {
        s += `<rect x="${L - 1}" y="${y - 1}" width="${(53 * cw + 1.5).toFixed(1)}" height="${ch + 1}" fill="none" stroke="${CB.sel}" stroke-width="1.2" pointer-events="none"/>`;
        Object.keys(selWeeks).forEach((w) => {
          s += `<rect x="${(L + w * cw).toFixed(2)}" y="${y}" width="${(cw - 0.5).toFixed(2)}" height="${ch - 1}" fill="none" stroke="${selWeeks[w] === "high" ? CB.orange : CB.sky}" stroke-width="1.4" pointer-events="none"/>`;
        });
      }
    });
    s += HF("calendar", { rows, L, cw, T, ch, W, R, d });
    // anomaly strip under the heatmap: triangles for the selected year's flagged weeks
    const ys = T + rows.length * ch + 3;
    Object.keys(selWeeks).forEach((w) => {
      const cx = L + (+w + 0.5) * cw;
      s += selWeeks[w] === "high"
        ? `<path d="M${(cx - 3).toFixed(1)},${ys + 6} L${(cx + 3).toFixed(1)},${ys + 6} L${cx.toFixed(1)},${ys} Z" fill="${CB.orange}"/>`
        : `<path d="M${(cx - 3).toFixed(1)},${ys} L${(cx + 3).toFixed(1)},${ys} L${cx.toFixed(1)},${ys + 6} Z" fill="${CB.sky}"/>`;
    });
    const nA = Object.values(selWeeks).filter((f) => f === "high").length;
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Burning calendar, ${esc(METRIC_UNIT[d.metric])} per ISO week for ${rows[0].y} to ${rows[rows.length - 1].y}; ${d.selected.year} has ${nA} anomalous weeks">${s}</svg>`;
    const svg = box.querySelector("svg");
    const info = (t) => {
      const i = +t.getAttribute("data-i"), w = +t.getAttribute("data-w");
      const r = rows[i];
      if (!r) return;
      if (w < 0) {
        const tot = r.v.reduce((a, b) => a + (b || 0), 0);
        $("cal-info").textContent = `${r.y}: ${fmtN(tot)} ${METRIC_UNIT[d.metric]} in total. Tap to compare this year.`;
        return;
      }
      const c = st[i][w];
      $("cal-info").textContent = `${r.y}, week ${w + 1} (${weekRange(r.y, w + 1)}): ${fmtN(r.v[w])} ${METRIC_UNIT[d.metric]}` +
        (c.mean != null ? `; other years average ${fmtN(c.mean)}` : "") + (c.z != null ? `, z ${c.z >= 0 ? "+" : ""}${c.z.toFixed(1)}` : "") +
        (c.z != null && c.z >= Z_THR && r.v[w] > 0 ? " (anomaly)" : "");
    };
    svg.addEventListener("pointermove", (e) => { if (e.target.hasAttribute("data-i")) info(e.target); });
    svg.addEventListener("click", (e) => {
      const t = e.target;
      if (!t.hasAttribute("data-i")) return;
      info(t);
      const y = rows[+t.getAttribute("data-i")].y;
      if (y !== hist.year) {
        hist.year = y;
        loadStats();
      }
    });
    // legend
    let lg = "";
    if (zMode) {
      lg += '<span class="muted">quieter</span>';
      [-3, -2, -1, 0, 1, 2, 3].forEach((z) => {
        lg += `<span><i class="sw" style="background:${ramp(DIVERGING, (z + 3) / 6)}"></i>${z > 0 ? "+" : ""}${z}${Math.abs(z) === 3 ? (z > 0 ? "+" : "") : ""}</span>`;
      });
      lg += '<span class="muted">more fire (z)</span>';
    } else {
      lg += `<span><i class="sw" style="background:${CB.empty}"></i>0</span>`;
      for (let k = 1; k <= 5; k++) {
        const v = Math.expm1((lmax * k) / 5);
        lg += `<span><i class="sw" style="background:${ramp(VIRIDIS, k / 5)}"></i>${fmtN(v < 10 ? v : Math.round(v))}</span>`;
      }
    }
    lg += `<span><i class="mk-high"></i>anomalous week ${d.selected.year}</span><span class="muted">blank = no ${d.metric.startsWith("modis") ? "MODIS" : d.metric.startsWith("viirs") ? "VIIRS" : ""} data</span>`;
    $("cal-legend").innerHTML = lg;
  }

  // ---- this year vs multi-year mean +- 1 std
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
  }
  function renderWeekly(d) {
    const wk = d.selected.weeks;
    $("wk-title").textContent = `${d.selected.year} vs other years, by week`;
    const W = 360, H = 182, L = 34, R = 6, T = 20, B = 18;
    let max = 0;
    wk.forEach((w) => {
      if (w.value != null) max = Math.max(max, w.value);
      if (w.mean != null) max = Math.max(max, w.mean + (w.std || 0));
    });
    max = niceMax(max);
    const x = (w) => L + ((w - 1) / 52) * (W - L - R);
    const y = (v) => T + (1 - v / max) * (H - T - B);
    let s = "";
    [0, 0.5, 1].forEach((f) => {
      const yy = y(max * f);
      s += `<line x1="${L}" x2="${W - R}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" stroke="${CB.grid}"/>`;
      s += `<text x="${L - 4}" y="${(yy + 3).toFixed(1)}" text-anchor="end" class="ax">${fmtN(max * f)}</text>`;
    });
    MONTH_WEEK.forEach((mw, i) => { s += `<text x="${x(mw).toFixed(1)}" y="${H - 5}" class="ax">${MONTHS[i]}</text>`; });
    const withBase = wk.filter((w) => w.mean != null);
    if (withBase.length) {
      const up = withBase.map((w) => `${x(w.week).toFixed(1)},${y(w.mean + w.std).toFixed(1)}`);
      const lo = withBase.slice().reverse().map((w) => `${x(w.week).toFixed(1)},${y(Math.max(0, w.mean - w.std)).toFixed(1)}`);
      s += `<polygon points="${up.concat(lo).join(" ")}" fill="rgba(47,109,181,0.18)"/>`;
      s += `<polyline points="${withBase.map((w) => `${x(w.week).toFixed(1)},${y(w.mean).toFixed(1)}`).join(" ")}" fill="none" stroke="${CB.sky}" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    }
    // selected year (break the line where there is no data)
    let seg = [];
    const flush = () => {
      if (seg.length > 1) s += `<polyline points="${seg.join(" ")}" fill="none" stroke="${CB.orange}" stroke-width="2"/>`;
      seg = [];
    };
    wk.forEach((w) => {
      if (w.value == null) flush();
      else seg.push(`${x(w.week).toFixed(1)},${y(w.value).toFixed(1)}`);
    });
    flush();
    wk.forEach((w) => {
      if (!w.flag || w.value == null) return;
      const cx = x(w.week), cy = y(w.value);
      s += w.flag === "high"
        ? `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3.6" fill="${CB.orange}" stroke="#fff" stroke-width="1.2"><title>Week ${w.week}: ${fmtN(w.value)} (mean ${fmtN(w.mean)}, z ${w.z})</title></circle>`
        : `<path d="M${(cx - 3.5).toFixed(1)},${(cy + 3).toFixed(1)} L${(cx + 3.5).toFixed(1)},${(cy + 3).toFixed(1)} L${cx.toFixed(1)},${(cy - 3.5).toFixed(1)} Z" fill="none" stroke="${CB.sky}" stroke-width="1.5"><title>Week ${w.week}: ${fmtN(w.value)} (mean ${fmtN(w.mean)}, z ${w.z})</title></path>`;
    });
    s += HF("weekly", { x, L, R, W, T, H, B, year: d.selected.year, d });
    const nA = wk.filter((w) => w.flag === "high").length;
    $("wk-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly ${esc(METRIC_UNIT[d.metric])} in ${d.selected.year} against the mean and one standard deviation of other years; ${nA} anomalous weeks">${s}</svg>`;
  }

  // ---- fires per year, MODIS vs VIIRS (or, for harmonized FRP / mixed-sensor counts, the selected measure)
  function renderYearly(d) {
    const det = /detections/.test(d.metric);
    const single = HF("yearlyMode", d) === "measure";
    const km = single ? [d.metric] : det ? ["modis_detections", "viirs_detections"] : ["modis_cell_days", "viirs_cell_days"];
    $("yr-title").textContent = single
      ? (d.metric === "frp_equiv_mw" ? "Harmonized fire power per year (VIIRS-equivalent)" : "Burning cell-days per year, both sensors combined")
      : det ? "Fire detections per year by sensor" : "Burning cell-days per year by sensor";
    const legendEl = $("yr-chart").parentNode.querySelector(".legend");
    if (legendEl) {
      if (legendEl.dataset.orig == null) legendEl.dataset.orig = legendEl.innerHTML;
      legendEl.innerHTML = single ? HF("yearlyLegend", d) : legendEl.dataset.orig;
    }
    const py = d.per_year;
    const W = 360, H = 172, L = 34, R = 4, T = 18, B = 18;
    const max = niceMax(Math.max(0, ...py.map((r) => Math.max(...km.map((k) => r[k] || 0)))));
    const gw = (W - L - R) / py.length, bw = single ? gw * 0.62 : Math.max(1.5, gw * 0.38);
    const y = (v) => T + (1 - v / max) * (H - T - B);
    let s = HF("yearlyBand", { py, L, gw, T, H, d });
    [0, 0.5, 1].forEach((f) => {
      const yy = y(max * f);
      s += `<line x1="${L}" x2="${W - R}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" stroke="${CB.grid}"/>`;
      s += `<text x="${L - 4}" y="${(yy + 3).toFixed(1)}" text-anchor="end" class="ax">${fmtN(max * f)}</text>`;
    });
    py.forEach((r, i) => {
      const x0 = L + i * gw;
      const isSel = r.year === d.selected.year;
      s += `<rect x="${x0.toFixed(1)}" y="${T}" width="${gw.toFixed(1)}" height="${H - T}" fill="${isSel ? "#f1f1fe" : "transparent"}" data-year="${r.year}" style="cursor:pointer"/>`;
      km.forEach((k, j) => {
        const v = r[k];
        if (v == null) return;
        let partial, fill, label, op = "";
        if (single) {
          partial = !(r.modis_coverage_days >= 360);
          fill = d.metric === "frp_equiv_mw" ? "#145c64" : "#7a4b00";
          label = "";
          if (partial) op = ' fill-opacity="0.45"';
          else if (!(r.viirs_coverage_days > 0)) op = ' fill-opacity="0.6"';
        } else {
          const cov = r[(j === 0 ? "modis" : "viirs") + "_coverage_days"];
          partial = cov < 360;
          fill = j === 0 ? CB.sky : CB.bar;
          label = j === 0 ? "MODIS " : "VIIRS ";
          if (partial) op = ' fill-opacity="0.45"';
        }
        const bx = single ? x0 + (gw - bw) / 2 : x0 + gw * 0.1 + j * bw;
        s += `<rect data-year="${r.year}" style="cursor:pointer" x="${bx.toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(y(0) - y(v)).toFixed(1)}" fill="${fill}"${op}><title>${r.year} ${label}${single ? esc(METRIC_UNIT[d.metric]) + ": " : ": "}${fmtN(v)}${partial ? " (partial year)" : ""}</title></rect>`;
      });
      const partialAny = (r.viirs_coverage_days > 0 && r.viirs_coverage_days < 360) || (r.modis_coverage_days > 0 && r.modis_coverage_days < 360);
      if ((py.length - 1 - i) % 2 === 0 || isSel) {
        s += `<text x="${(x0 + gw / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle" class="ax${isSel ? " sel" : ""}">'${String(r.year).slice(2)}${partialAny ? "*" : ""}</text>`;
      }
    });
    s += HF("yearlyLines", { py, L, gw, T, H, B, d });
    const what = single ? (d.metric === "frp_equiv_mw" ? "Harmonized FRP" : "Burning cell-days, any sensor") : det ? "Detections" : "Burning cell-days";
    $("yr-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${what} per year${single ? "" : ", MODIS and VIIRS"}, ${py[0].year} to ${py[py.length - 1].year}. Sensor eras: MODIS only before 2012, S-NPP VIIRS from 20 January 2012, NOAA-20 from 2018.">${s}</svg>`;
    $("yr-chart").querySelector("svg").addEventListener("click", (e) => {
      const yv = +(e.target.getAttribute && e.target.getAttribute("data-year"));
      if (!yv || yv === hist.year) return;
      const r = py.find((p) => p.year === yv);
      if (r && r[hist.metric] != null) {
        hist.year = yv;
        loadStats();
      }
    });
  }
  window.EqHistApp = { rerenderYearly: () => { if (hist.data) renderYearly(hist.data); } };

  // ---------------------------------------------------------------- Health check & bootstrap
  async function pollHealth() {
    try {
      const h = await getJSON("/health");
      state.backendReady = true;
      el.statusPill.textContent = "Ready";
      el.statusPill.className = "status-badge ready";

      if (h.data) {
        const dMin = h.data.viirs_date_min || h.data.date_min;
        const dMax = h.data.viirs_date_max || h.data.date_max;
        if (dMin && dMax) {
          state.dateRange = [dMin, dMax];
          el.date.min = dMin;
          el.date.max = dMax;
        }
      }
    } catch (e) {
      el.statusPill.textContent = "offline";
      setTimeout(pollHealth, 4000);
    }
  }

  // ---------------------------------------------------------------- Live anomaly alerts (goal 3)
  // Colorblind-safe ordinal ramp (light yellow -> orange -> dark purple), no red/green.
  const LEVEL_COLORS = { watch: "#e8b631", warning: "#c66a28", critical: "#4b1d6b", none: "#89949e" };
  const alerts = { layer: null, features: [], timer: null, selected: null };

  function alertsQuery() {
    return { res: $("alerts-res").value, level: $("alerts-level").value };
  }

  function alertStyle(f) {
    const lv = f.properties.level;
    const sel = alerts.selected === f.properties.rid;
    return { color: sel ? "#010658" : lv === "critical" ? "#1b0430" : "#5a4a00", weight: sel ? 3 : 1,
             fillColor: LEVEL_COLORS[lv] || LEVEL_COLORS.none, fillOpacity: lv === "none" ? 0.25 : 0.6 };
  }

  function timesNormal(p) {
    if (p.ratio) return `${p.ratio}× normal for this week`;
    return "normally no fire this week";
  }

  function alertPopup(p) {
    const hist = p.history ? Object.values(p.history) : [];
    const hmax = Math.max(1, p.obs, ...hist);
    const bars = hist.map((v, i) => `<span title="${2008 + i}: ${v}" style="height:${Math.max(1, (24 * v) / hmax)}px;` +
      `background:${2008 + i < 2012 ? "#b9a6cf" : "#8892b0"}"></span>`).join("") +
      `<span title="now: ${p.obs}" style="height:${(24 * p.obs) / hmax}px;background:${LEVEL_COLORS[p.level]}"></span>`;
    return `<b>${p.level.toUpperCase()}</b> · z = ${p.z}<br>${p.obs} burning cells in 7 days vs ${p.mean} ± ${p.std}` +
      ` (${p.years_used} yrs, ${p.years_reconstructed} reconstructed)<br>${timesNormal(p)}` +
      (p.out_of_season ? "<br><b>Out of season</b> (burned in " + p.years_burned + " of " + p.years_used + " years)" : "") +
      `<div class="alert-spark">${bars}</div>` +
      `Top FRP ${p.top_frp} MW at ${p.top_frp_lat}, ${p.top_frp_lon} ${estBadge("Strongest detection in this region; the coordinates are the centre of the VIIRS pixel (~375 m) that saw it, not an exact fire position.")}<br>Latest ${fmtUtc(p.latest)}<br>` +
      (p.years_reconstructed ? `<span class="muted small">Baseline includes ${p.years_reconstructed} years reconstructed from MODIS (2008-2011).</span><br>` : "") +
      `<span class="muted">${(p.satellites || []).join(", ")}</span>`;
  }

  function fmtUtc(iso) {
    return iso ? iso.replace("T", " ").replace(/:\d\dZ$/, " UTC").replace(/Z$/, " UTC") : "-";
  }

  function renderAlerts(fc) {
    alerts.features = fc.features || [];
    if (alerts.layer) alerts.layer.remove();
    alerts.layer = L.geoJSON(fc, {
      style: alertStyle,
      onEachFeature: (f, lyr) => {
        lyr.bindPopup(alertPopup(f.properties));
        lyr.on("click", () => selectAlert(f.properties.rid, false));
      },
    }).addTo(map);
    const nAlert = alerts.features.filter((f) => f.properties.level !== "none").length;
    $("alerts-count").textContent = `${nAlert} alert${nAlert === 1 ? "" : "s"}`;
    const m = fc.meta || {};
    const feeds = (m.feeds || []).filter((f) => f.detections > 0)
      .map((f) => `${f.feed} ${fmtUtc(f.latest)}${f.stale ? " (stale)" : ""}`).join(" · ");
    $("alerts-feed").textContent = m.window
      ? `Window ${fmtUtc(m.window.start)} to ${fmtUtc(m.window.end)} · week ${m.week + 1} · feeds: ${feeds}`
      : "No NRT detections.";
    const list = $("alerts-list");
    list.innerHTML = "";
    const shown = alerts.features.filter((f) => f.properties.level !== "none").slice(0, 100);
    if (!shown.length) list.innerHTML = '<li class="muted small">No region is above its norm at this level.</li>';
    for (const f of shown) {
      const p = f.properties;
      const li = document.createElement("li");
      li.className = "alert-item";
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      li.setAttribute("aria-label", `Inspect ${p.level} activity near ${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}`);
      li.innerHTML = `<span class="lv-badge lv-${p.level}">${p.level}</span>` +
        `<span class="alert-main"><span class="mono">${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}</span>` +
        `<span class="small muted">${timesNormal(p)} · ${p.obs} vs ${p.mean}${p.out_of_season ? " · out of season" : ""}</span></span>` +
        `<span class="alert-z">z ${p.z}</span>`;
      li.addEventListener("click", () => selectAlert(p.rid, true));
      li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") {e.preventDefault();selectAlert(p.rid, true);} });
      list.appendChild(li);
    }
    const q = new URLSearchParams({ res: alertsQuery().res });
    $("alerts-cap").href = `${API}/live/alerts.cap?${q}`;
    $("alerts-geojson").href = `${API}/live/alerts?${new URLSearchParams(alertsQuery())}`;
  }

  function selectAlert(rid, zoom) {
    alerts.selected = rid;
    if (!alerts.layer) return;
    alerts.layer.setStyle(alertStyle);
    alerts.layer.eachLayer((lyr) => {
      if (lyr.feature.properties.rid !== rid) return;
      if (zoom) {
        if (state.view !== "map") switchView("map");
        map.fitBounds(lyr.getBounds(), { padding: [30, 30], maxZoom: 10 });
      }
      lyr.openPopup();
      lyr.bringToFront();
    });
  }

  async function loadAlerts() {
    $("alerts-list").innerHTML = '<li class="muted small">Loading NRT feed…</li>';
    try {
      const fc = await getJSON("/live/alerts", alertsQuery());
      renderAlerts(fc);
    } catch (e) {
      const msg = String(e.message || e);
      $("alerts-list").innerHTML = `<li class="muted small">${msg.replace(/</g, "&lt;")}</li>`;
      if (/503|download|retry/i.test(msg) && state.mode === "alerts") {
        clearTimeout(alerts.timer);
        alerts.timer = setTimeout(loadAlerts, 15000);
      }
    }
  }

  function enterAlerts() {
    if (state.view !== "map") switchView("map");
    if (alerts.layer && !map.hasLayer(alerts.layer)) alerts.layer.addTo(map);
    map.setView([0, 0], 2);
    loadAlerts();
    clearInterval(alerts.poll);
    alerts.poll = setInterval(() => { if (state.mode === "alerts") loadAlerts(); }, 15 * 60 * 1000);
  }

  function leaveAlerts() {
    clearTimeout(alerts.timer);
    clearInterval(alerts.poll);
    if (alerts.layer && map.hasLayer(alerts.layer)) alerts.layer.remove();
  }

  $("alerts-res").addEventListener("change", loadAlerts);
  $("alerts-level").addEventListener("change", loadAlerts);
  $("alerts-refresh").addEventListener("click", loadAlerts);

  // ---------------------------------------------------------------- Layers menu
  {
    const btn = $("layers-btn"), menu = $("layers-menu");
    const setOpen = (open) => { menu.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(menu.hidden); });
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { setOpen(false); btn.focus(); } });
    L.DomEvent.disableClickPropagation(menu);
    L.DomEvent.disableScrollPropagation(menu);
  }

  // ---------------------------------------------------------------- Fire-monitoring satellites (globe + map)
  // Okabe-Ito colours (colourblind-safe), every marker is also labelled with its name.
  const SAT_STYLE = {
    25994: { color: "#E69F00" },  // Terra (MODIS)
    27424: { color: "#56B4E9" },  // Aqua (MODIS)
    37849: { color: "#009E73" },  // Suomi NPP (VIIRS)
    43013: { color: "#F0E442" },  // NOAA-20 (VIIRS)
    54234: { color: "#CC79A7" },  // NOAA-21 (VIIRS)
  };
  const sats = (() => {
    // Default on; keep the user's layer choices across tabs and reloads.
    for (const id of ["lyr-sats", "lyr-tracks"]) {
      try {
        const saved = localStorage.getItem(`equifire:${id}`);
        if (saved !== null) $(id).checked = saved === "true";
      } catch (_) { /* Layer controls still work without browser storage. */ }
    }
    const st = { fleet: [], tracks: {}, off: new Set(), globe: null, mapLayer: L.layerGroup().addTo(map), trackLayer: L.layerGroup().addTo(map),
                 markers: {}, globeObjs: [], tracksAt: 0, error: "" };
    const on = () => $("lyr-sats").checked;
    const tracksOn = () => $("lyr-tracks").checked;
    const colorOf = (s) => (SAT_STYLE[s.norad_id] || {}).color || s.color || "#ffffff";
    const rgba = (hex, a) => { const [r, g, b] = hexRgb(hex); return `rgba(${r},${g},${b},${a})`; };

    function markerHtml(s, dark) {
      return `<span class="sat-marker"><span class="sat-icon" style="background:${colorOf(s)}">${icon("satellite")}</span>` +
        `<span class="sat-label"${dark ? "" : ' style="background:#010658"'}>${esc(s.name)}</span></span>`;
    }

    async function loadFleet() {
      try {
        const d = await getJSON("/api/satellites/live");
        const prev = new Map(st.fleet.map((s) => [s.norad_id, s]));
        st.fleet = (d.fleet || []).filter(s => Object.prototype.hasOwnProperty.call(SAT_STYLE, s.norad_id)).map((s) => ({ ...(prev.get(s.norad_id) || {}), ...s, fixAt: Date.parse(d.timestamp) || Date.now() }));
        st.error = "";
        if (!st.globeObjs.length) buildObjects();
        renderList();
        if (!Object.keys(st.tracks).length) loadTracks();
      } catch (e) {
        st.error = "Satellite positions unavailable: " + e.message;
        $("fleet-list").innerHTML = `<li class="muted small">${esc(st.error)}</li>`;
      }
    }

    // ~2 h of ground track per satellite (3 x 40 min windows) for interpolation and orbit lines
    async function loadTracks() {
      const now = Date.now();
      const centres = [-30, 10, 50].map((m) => new Date(now + m * 60000).toISOString());
      await Promise.all(st.fleet.map(async (s) => {
        try {
          const parts = await Promise.all(centres.map((c) => getJSON("/api/satellite-track", { norad_id: s.norad_id, center_time: c, span_minutes: 40 })));
          const seen = new Set();
          const pts = [];
          parts.forEach((p) => (p.ground_track || []).forEach(([lat, lon, alt, t]) => {
            const ms = Date.parse(t);
            if (seen.has(ms)) return;
            seen.add(ms);
            pts.push({ t: ms, lat, lon, alt });
          }));
          pts.sort((a, b) => a.t - b.t);
          if (pts.length > 2) st.tracks[s.norad_id] = pts;
        } catch (e) { /* keep last known track */ }
      }));
      st.tracksAt = Date.now();
      renderTracks();
      tick();
    }

    function posAt(s, t) {
      const tr = st.tracks[s.norad_id];
      if (!tr || t < tr[0].t || t > tr[tr.length - 1].t) return { lat: s.latitude, lon: s.longitude, alt: s.altitude_km, approx: true };
      let lo = 0, hi = tr.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (tr[mid].t <= t) lo = mid; else hi = mid; }
      const a = tr[lo], b = tr[hi], f = (t - a.t) / Math.max(1, b.t - a.t);
      const dl = ((b.lon - a.lon + 540) % 360) - 180;
      let lon = a.lon + dl * f;
      lon = ((lon + 540) % 360) - 180;
      return { lat: a.lat + (b.lat - a.lat) * f, lon, alt: a.alt + (b.alt - a.alt) * f };
    }

    function buildObjects() {
      st.globeObjs = st.fleet.map((s) => {
        const div = document.createElement("div");
        div.innerHTML = markerHtml(s, true);
        const elx = div.firstElementChild;
        elx.title = `${s.name} (${s.instrument}), swath ${s.swath_width_km} km`;
        return { id: s.norad_id, el: elx, lat: s.latitude, lng: s.longitude, alt: s.altitude_km / EARTH_RADIUS_KM };
      });
      applyGlobe();
    }

    function visibleFleet() { return st.fleet.filter((s) => !st.off.has(s.norad_id)); }

    function applyGlobe() {
      if (!st.globe) return;
      const vis = on() ? st.globeObjs.filter((o) => !st.off.has(o.id)) : [];
      st.globe.htmlElementsData(vis);
      renderGlobePaths();
    }

    function renderGlobePaths() {
      if (!st.globe) return;
      const paths = on() && tracksOn() ? visibleFleet().filter((s) => st.tracks[s.norad_id]).map((s) => ({
        color: [rgba(colorOf(s), 0.15), rgba(colorOf(s), 0.95)],
        pts: st.tracks[s.norad_id].map((p) => [p.lat, p.lon, p.alt / EARTH_RADIUS_KM]),
        name: s.name,
      })) : [];
      st.globe.pathsData(paths);
    }

    function attachGlobe(g) {
      st.globe = g;
      g.pathPoints("pts").pathPointLat((p) => p[0]).pathPointLng((p) => p[1]).pathPointAlt((p) => p[2])
        .pathColor((d) => d.color).pathStroke(1.4).pathTransitionDuration(0).pathLabel((d) => `<div class="fire-tip">${esc(d.name)} orbit</div>`)
        .htmlLat("lat").htmlLng("lng").htmlAltitude("alt").htmlElement((d) => d.el).htmlTransitionDuration(0);
      applyGlobe();
      tick();
    }

    // Leaflet: ground tracks split at the antimeridian; past part faint, next part solid
    function renderTracks() {
      renderGlobePaths();
      st.trackLayer.clearLayers();
      if (!on() || !tracksOn()) return;
      const now = Date.now();
      visibleFleet().forEach((s) => {
        const tr = st.tracks[s.norad_id];
        if (!tr) return;
        const segs = [[]];
        tr.forEach((p, i) => {
          if (i && Math.abs(p.lon - tr[i - 1].lon) > 180) segs.push([]);
          segs[segs.length - 1].push(p);
        });
        segs.forEach((seg) => {
          const past = seg.filter((p) => p.t <= now).map((p) => [p.lat, p.lon]);
          const next = seg.filter((p) => p.t >= now).map((p) => [p.lat, p.lon]);
          const o = { interactive: false };
          if (past.length > 1) L.polyline(past, { ...o, color: colorOf(s), weight: 2, opacity: 0.45, dashArray: "4 6" }).addTo(st.trackLayer);
          if (next.length > 1) {
            L.polyline(next, { ...o, color: "#010658", weight: 4.5, opacity: 0.5 }).addTo(st.trackLayer);
            L.polyline(next, { ...o, color: colorOf(s), weight: 2.5, opacity: 0.95 }).addTo(st.trackLayer);
          }
        });
      });
    }

    function tick() {
      if (!st.fleet.length) return;
      const t = Date.now();
      const show = on();
      st.fleet.forEach((s) => {
        const p = posAt(s, t);
        s.cur = p;
        const o = st.globeObjs.find((x) => x.id === s.norad_id);
        if (o) { o.lat = p.lat; o.lng = p.lon; o.alt = p.alt / EARTH_RADIUS_KM; }
        const visible = show && !st.off.has(s.norad_id);
        let m = st.markers[s.norad_id];
        if (visible) {
          if (!m) {
            m = st.markers[s.norad_id] = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "leaflet-sat", html: markerHtml(s, false), iconSize: [20, 20], iconAnchor: [10, 10] }), keyboard: false, zIndexOffset: 900 })
              .bindTooltip(`${esc(s.name)} · ${esc(s.instrument)} · swath ${s.swath_width_km} km`);
          }
          m.setLatLng([p.lat, p.lon]);
          if (!st.mapLayer.hasLayer(m)) m.addTo(st.mapLayer);
        } else if (m && st.mapLayer.hasLayer(m)) st.mapLayer.removeLayer(m);
        const meta = document.querySelector(`#fleet-list [data-norad="${s.norad_id}"] .fleet-meta`);
        if (meta) meta.textContent = `${s.instrument} · ${fmtLat(p.lat)} ${fmtLon(p.lon)} · ${Math.round(p.alt)} km`;
      });
      if (st.globe && show) st.globe.htmlElementsData(st.globeObjs.filter((o) => !st.off.has(o.id)));
      $("fleet-time").textContent = new Date(t).toISOString().slice(11, 19) + " UTC";
      if (t - st.tracksAt > 10 * 60000 && st.tracksAt) { st.tracksAt = t; loadTracks(); }
      else if (t - (st.trackDrawn || 0) > 60000) { st.trackDrawn = t; renderTracks(); }
    }

    function fmtLat(v) { return `${Math.abs(v).toFixed(1)}°${v >= 0 ? "N" : "S"}`; }
    function fmtLon(v) { return `${Math.abs(v).toFixed(1)}°${v >= 0 ? "E" : "W"}`; }

    function renderList() {
      const list = $("fleet-list");
      list.innerHTML = st.fleet.map((s) => `
        <li class="fleet-item" data-norad="${s.norad_id}">
          <button type="button" class="fleet-toggle" aria-pressed="${!st.off.has(s.norad_id)}" aria-label="Show ${esc(s.name)}">
            <span class="fleet-swatch" style="background:${colorOf(s)}"></span>
            <span><span class="fleet-name">${esc(s.name)}</span><span class="fleet-meta">${esc(s.instrument)}</span></span>
          </button>
          <span class="fleet-eye">${icon(st.off.has(s.norad_id) ? "eye-off" : "eye")}</span>
        </li>`).join("");
      list.querySelectorAll(".fleet-toggle").forEach((b) => b.addEventListener("click", () => {
        const id = +b.closest("[data-norad]").dataset.norad;
        if (st.off.has(id)) st.off.delete(id); else st.off.add(id);
        renderList();
        applyGlobe();
        renderTracks();
        tick();
      }));
      tick();
    }

    function refreshVisibility() {
      for (const id of ["lyr-sats", "lyr-tracks"]) {
        try { localStorage.setItem(`equifire:${id}`, String($(id).checked)); } catch (_) {}
      }
      $("lyr-tracks").disabled = !on();
      $("fleet-panel").hidden = !on();
      applyGlobe();
      renderTracks();
      tick();
    }
    $("lyr-sats").addEventListener("change", refreshVisibility);
    $("lyr-tracks").addEventListener("change", refreshVisibility);

    function start() {
      refreshVisibility();
      loadFleet();
      setInterval(tick, 1000);
      setInterval(loadFleet, 60000);   // re-anchor on the backend's live fix every minute
    }
    return { start, attachGlobe, posAt };
  })();

  // ---------------------------------------------------------------- Weather (keyless: RainViewer radar, Open-Meteo wind)
  const wx = (() => {
    const st = { radar: null, radarTimer: null, wind: L.layerGroup(), windSeq: 0, cache: new Map(), lastReq: 0, debounce: null };
    const note = (id, msg) => { const p = $(id); p.textContent = msg || ""; p.hidden = !msg; };

    async function loadRadar() {
      try {
        const res = await fetch("https://api.rainviewer.com/public/weather-maps.json");
        const d = await res.json();
        const frames = (d.radar && d.radar.past) || [];
        const fr = frames[frames.length - 1];
        if (!fr) throw new Error("no radar frames");
        const url = `${d.host}${fr.path}/256/{z}/{x}/{y}/2/1_1.png`;
        if (st.radar) map.removeLayer(st.radar);
        st.radar = L.tileLayer(url, { opacity: 0.7, maxNativeZoom: 7, maxZoom: 19, zIndex: 350,
          attribution: 'Radar &copy; <a href="https://www.rainviewer.com/">RainViewer</a>' });
        if ($("lyr-radar").checked) st.radar.addTo(map);
        note("radar-status", `Radar mosaic ${new Date(fr.time * 1000).toISOString().slice(11, 16)} UTC (past 10 min frame). Radar covers land areas with ground radar only.`);
      } catch (e) {
        note("radar-status", "Radar unavailable right now (RainViewer).");
      }
    }
    function setRadar(onn) {
      $("legend-radar").hidden = !onn;
      clearInterval(st.radarTimer);
      if (onn) {
        if (st.radar) st.radar.addTo(map);
        loadRadar();
        st.radarTimer = setInterval(loadRadar, 10 * 60000);
      } else {
        if (st.radar) map.removeLayer(st.radar);
        note("radar-status", "");
      }
    }

    function compass(deg) {
      return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
    }
    function arrowSvg(dirFrom) {
      // wind_direction is where the wind comes FROM; the arrow points where it blows TO
      return `<svg viewBox="0 0 24 24" style="transform:rotate(${(dirFrom + 180) % 360}deg)" aria-hidden="true"><path d="M12 2l6 10h-4v10h-4V12H6z"/></svg>`;
    }

    // one Open-Meteo request per view: 5 x 4 grid, cached, at most one call every 4 s
    async function loadWind() {
      if (!$("lyr-wind").checked || state.view !== "map") return;
      const b = map.getBounds();
      const s = Math.max(-85, b.getSouth()), n = Math.min(85, b.getNorth());
      let w = b.getWest(), e = b.getEast();
      if (e - w > 360) { w = -180; e = 180; }
      const cols = 5, rows = 4;
      const pts = [];
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const lat = s + (n - s) * (r + 0.5) / rows, lon = w + (e - w) * (c + 0.5) / cols;
        pts.push({ lat, lon, qlon: ((lon + 540) % 360) - 180 });
      }
      const key = pts.map((p) => `${p.lat.toFixed(2)},${p.qlon.toFixed(2)}`).join(";");
      const seq = ++st.windSeq;
      let data = st.cache.get(key);
      if (!data) {
        const wait = 4000 - (Date.now() - st.lastReq);
        if (wait > 0) { clearTimeout(st.debounce); st.debounce = setTimeout(loadWind, wait); return; }
        st.lastReq = Date.now();
        note("wind-status", "Loading wind…");
        try {
          const url = "https://api.open-meteo.com/v1/forecast?" + new URLSearchParams({
            latitude: pts.map((p) => p.lat.toFixed(3)).join(","), longitude: pts.map((p) => p.qlon.toFixed(3)).join(","),
            current: "wind_speed_10m,wind_direction_10m", wind_speed_unit: "kmh" });
          const res = await fetch(url);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const j = await res.json();
          data = (Array.isArray(j) ? j : [j]).map((r) => r.current || {});
          if (st.cache.size > 40) st.cache.clear();
          st.cache.set(key, data);
        } catch (err) {
          note("wind-status", "Wind unavailable (Open-Meteo): " + err.message);
          return;
        }
      }
      if (seq !== st.windSeq) return;
      st.wind.clearLayers();
      pts.forEach((p, i) => {
        const c = data[i] || {};
        if (c.wind_speed_10m == null) return;
        L.marker([p.lat, p.lon], { interactive: false, keyboard: false, icon: L.divIcon({ className: "", iconSize: [60, 44], iconAnchor: [30, 16],
          html: `<div class="wind-marker">${arrowSvg(c.wind_direction_10m)}<span>${Math.round(c.wind_speed_10m)} km/h ${compass(c.wind_direction_10m)}</span></div>` }) }).addTo(st.wind);
      });
      note("wind-status", `Open-Meteo 10 m wind, ${data[0] && data[0].time ? data[0].time.replace("T", " ") + " UTC" : "now"}. Labels give speed and the direction it comes from.`);
    }
    function setWind(onn) {
      $("legend-wind").hidden = !onn;
      if (onn) { st.wind.addTo(map); loadWind(); }
      else { map.removeLayer(st.wind); note("wind-status", ""); }
    }
    map.on("moveend", () => { if ($("lyr-wind").checked) { clearTimeout(st.debounce); st.debounce = setTimeout(loadWind, 600); } });
    $("lyr-radar").addEventListener("change", (e) => setRadar(e.target.checked));
    $("lyr-wind").addEventListener("change", (e) => setWind(e.target.checked));
    return { compass, loadWind };
  })();

  // ---------------------------------------------------------------- Combined ML + physics (/predict/simulate) with persistent playback
  const sim = (() => {
    const st = { available: null, params: [], data: null, frames: [], idx: 0, playing: false, timer: null, seq: 0,
                 guidanceLayers: {}, guide: L.layerGroup().addTo(map), controller: null, running: false,
                 layer: L.layerGroup().addTo(map), top: L.layerGroup().addTo(map), frameLayers: [], checkTimer: null, units: {} };
    const pb = (id) => $("pb-" + id);
    const ign = () => $("sim-ignition").value;
    const frameColor = (i) => ramp(VIRIDIS, st.frames.length > 1 ? i / (st.frames.length - 1) : 1);

    async function checkService() {
      clearTimeout(st.checkTimer);
      try {
        const spec = await getJSON("/openapi.json");
        const path = spec.paths && spec.paths["/predict/simulate"];
        st.available = !!path;
        st.params = path && path.get ? (path.get.parameters || []).map((p) => p.name) : [];
      } catch (e) {
        st.available = false;
      }
      syncService();
      if (!st.available) st.checkTimer = setTimeout(() => { if (state.mode === "date") checkService(); else st.available = null; }, 30000);
    }
    function syncService(msg, cls) {
      const p = $("sim-service");
      p.className = "service-note" + (cls ? " " + cls : "");
      if (msg) { p.textContent = msg; return; }
      if (st.available === false) {
        p.className = "service-note wait";
        p.textContent = "Forecast service unavailable. Try again later.";
      } else if (st.available) {
        p.textContent = state.selectedPoint ? "Ready. Press Run prediction." : "Ready. Select a fire on the map to start.";
      } else p.textContent = "Checking forecast service…";
    }

    function stepMin() {
      const v = $("sim-step").value;
      if (v !== "auto") return +v;
      const h = +$("sim-hours").value;
      return h <= 12 ? 15 : h <= 24 ? 30 : 60;
    }
    function toLocalInput(d) { return new Date(d.getTime()).toISOString().slice(0, 16); }

    function initialize() {
      $("method-badge").textContent = "ML + physics";
      el.btnForecast.querySelector("span").textContent = "Run ML + physics";
      $("chk-actual-wrap").hidden = true;
      pb("ramp").style.background = `linear-gradient(90deg,${VIRIDIS.join(",")})`;
      syncControls(true);
      reset();
    }

    function syncControls(silent) {
      const archive = ign() === "archive";
      $("date-row").hidden = !archive;
      if (archive) $("sim-start").value = `${el.date.value}T12:00`;
      else {
        const d = new Date(); d.setUTCMinutes(0, 0, 0);
        $("sim-start").value = toLocalInput(d);
      }
      reset();
      if (!silent && state.mode === "date") loadFires();
      if (state.selectedPoint) onPointSelected(state.selectedPoint.lat, state.selectedPoint.lon);
      syncService();
    }

    function ignitionReason() {
      return ({
        live: "The simulation starts from nearby satellite detections and groups them into separate fires. Locations are pixel centres (VIIRS ~375 m, MODIS ~1 km), so the true fire edge can be several hundred metres away.",
        point: "The simulation ignites exactly where you clicked. Without a detection there, the start location is an assumption you chose.",
        archive: "The simulation starts from archive burning cells for the chosen date. Cells are 0.05° (~5 km) squares, so the start shape is coarse." +
          (el.date.value < VIIRS_START ? " Before 20 Jan 2012 these cells are reconstructed from MODIS (1 km)." : ""),
      })[ign()];
    }

    function onPointSelected(lat, lon) {
      $("sel-point").hidden = false;
      $("sel-point-text").textContent = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      $("sel-point-est").innerHTML = estBadge(ignitionReason(), "Satellite ignition");
      reset("Location selected. Run ML + physics to load the timeline.");
      syncService();
      if (window.EquiFire.emit) window.EquiFire.emit("point", lat, lon);
    }

    function usesLiveFires() { return ign() !== "archive"; }

    // ---- run
    async function run(opts) {
      opts = opts && !opts.type ? opts : {};  // optional {hours, tag} from add-on modules (ui-forecast-extras.js)
      if (!state.selectedPoint || st.running) return;
      if (!st.available) await checkService();
      if (!st.available) {
        syncService("The combined service is unavailable. Please retry once it is ready.", "wait");
        return;
      }
      reset("Running ML + physics…");
      const seq = ++st.seq;
      st.controller = new AbortController(); st.running = true;
      const { lat, lon } = state.selectedPoint;
      const startVal = $("sim-start").value;
      const q = { lat: lat.toFixed(4), lon: lon.toFixed(4), hours: opts.hours || $("sim-hours").value, step_min: opts.hours && $("sim-step").value === "auto" ? (opts.hours <= 12 ? 15 : opts.hours <= 24 ? 30 : 60) : stepMin(), ignition: ign() };
      st.runOpts = opts;
      if (startVal) q.start = startVal.slice(0, 16);
      if (st.params.includes("fuel")) q.fuel = $("sim-fuel").value;
      if (st.params.includes("envelope")) q.envelope = "true";
      if (st.params.includes("weather")) q.weather = "grid";
      if (st.params.includes("landscape")) q.landscape = "on";
      if (st.params.includes("calibration")) q.calibration = (window.EquiFire.options || {}).calibration || "off";
      if (ign() === "archive" && st.params.includes("date")) q.date = el.date.value;
      el.btnForecast.disabled = true;
      syncService(`Running ML + physics for ${q.hours} h in ${q.step_min}-minute steps…`);
      $("sim-result").hidden = false;
      $("sim-msg").textContent = "Calculating combined daily guidance and hourly spread…";
      $("sim-est").innerHTML = "";
      $("sim-kpis").hidden = true;
      $("sim-downloads").hidden = true;
      try {
        const d = await getJSON("/predict/simulate", q, {signal: st.controller.signal});
        if (seq !== st.seq) return;
        load(d, q);
        syncService(`Done: ${st.frames.length} frames.`);
      } catch (e) {
        if (seq !== st.seq) return;
        $("sim-msg").textContent = "Combined prediction unavailable: " + e.message;
        pb("status").textContent = "Prediction unavailable. Adjust the location or retry.";
        syncService("Combined prediction failed: " + e.message, "wait");
      } finally {
        if (seq === st.seq) { st.running = false; el.btnForecast.disabled = false; }
      }
    }

    function asGeo(g) {
      if (!g) return null;
      if (g.type === "FeatureCollection" || g.type === "Feature") return g;
      if (g.type && g.coordinates) return { type: "Feature", properties: {}, geometry: g };
      if (Array.isArray(g)) return { type: "FeatureCollection", features: g.map(asGeo).filter(Boolean) };
      return null;
    }
    function envelopeOf(f, i, d) {
      const env = f.envelope || f.envelopes || {};
      const top = d.envelopes || {};
      return {
        low: asGeo(f.low || f.burned_low || env.low || (Array.isArray(top.low) ? top.low[i] : null)),
        high: asGeo(f.high || f.burned_high || env.high || (Array.isArray(top.high) ? top.high[i] : null)),
      };
    }

    function load(d, q) {
      if (d.prediction_mode !== "ml_physics" || !d.guidance || !Array.isArray(d.frames) || !d.frames.length)
        throw new Error("Both ML + physics guidance and simulation frames are required.");
      stop();
      st.data = d;
      st.units = d.units || {};
      const sm0 = d.summary || {};
      st.frames = (d.frames || []).map((f, i) => {
        const w = f.weather || {};
        return { ...f, geo: asGeo(f.burned || f.burned_area || f.perimeter || f.geometry), env: envelopeOf(f, i, d),
          t: Date.parse(f.time_utc || f.time), ros: f.max_ros_m_min != null ? f.max_ros_m_min : f.max_ros,
          wx: { speed: w.wind_kmh != null ? w.wind_kmh : w.wind_speed, dir: w.wind_dir_deg != null ? w.wind_dir_deg : w.wind_dir, gust: w.gust_kmh,
                temp: w.temp_c != null ? w.temp_c : w.temp, rh: w.rh, rain: w.rain_mm != null ? w.rain_mm : w.rain, fm: w.fuel_moisture_1h_pct } };
      });
      // final low/high envelope perimeters belong to the last frame
      const ep = sm0.envelope_perimeters;
      if (ep && st.frames.length) {
        const lf = st.frames[st.frames.length - 1];
        lf.env = { low: lf.env.low || asGeo(ep.low), high: lf.env.high || asGeo(ep.high) };
      }
      clearLayers();
      st.frameLayers = st.frames.map((f, i) => f.geo ? L.geoJSON(f.geo, { interactive: false,
        style: { stroke: false, weight: 0, fillColor: frameColor(i), fillOpacity: 0.55 } }) : null);
      renderResult(d, q);
      const n = st.frames.length;
      pb("slider").max = Math.max(0, n - 1);
      pb("ramp").style.background = `linear-gradient(90deg,${VIRIDIS.join(",")})`;
      pb("method").textContent = "ML + physics";
      const conf = pb("conf");
      conf.textContent = "Confidence not calibrated";
      conf.hidden = false;
      const confidenceNotes = ["No validated confidence percentage is available for this individual fire. Colours show spread timing, not confidence."];
      if (d.guidance.history && d.guidance.history.reduced)
        confidenceNotes.push("Live predictions use reduced seven-day history and have not been validated as a live operational forecast.");
      if (d.weather && d.weather.fallback)
        confidenceNotes.push("Weather is an illustrative fallback scenario rather than a retrieved forecast.");
      if (sm0.n_fires > 1)
        confidenceNotes.push(`This run includes ${sm0.n_fires} fire groups; displayed areas are totals across those groups.`);
      const scenarios = (d.summary || {}).envelope_km2 || {};
      if (scenarios.low != null && scenarios.high != null)
        confidenceNotes.push(`Area sensitivity: ${num(scenarios.low)}–${num(scenarios.high)} km² across weather scenarios. This is not a statistical confidence interval.`);
      conf.dataset.tip = confidenceNotes.join(" ");
      conf.title = conf.dataset.tip;
      conf.setAttribute("aria-label", conf.textContent + ". " + conf.dataset.tip);
      st.guidanceLayers = {};
      (d.guidance.features || []).filter(f => f.properties && f.properties.selected).forEach(f => {
        const day = +f.properties.day;
        if (!st.guidanceLayers[day]) st.guidanceLayers[day] = L.layerGroup();
        L.geoJSON(f, {style: {color: "#514cf1", weight: 1, dashArray: "3 3", fillOpacity: .12}})
          .bindTooltip(`Day ${day} combined model score: ${num(f.properties.combined_probability, 2)} (uncalibrated) · daily guidance`)
          .addTo(st.guidanceLayers[day]);
      });
      $("ml-result").hidden = false;
      renderCombined(d.guidance, Math.ceil(+q.hours / 24), d.guidance.query.date, q);
      predLayer.clearLayers();
      setTimelineReady(true);
      pb("status").textContent = `${n} frames ready. Drag the slider or press Play.`;
      showPlayback();
      show(0);
      if (window.EquiFire.emit) window.EquiFire.emit("load", st.data, q, st.runOpts || {});
      if (mq.matches) window.scrollTo({top: 0, behavior: "auto"});
      const all = st.frameLayers.filter(Boolean);
      if (all.length) { try { map.fitBounds(all[all.length - 1].getBounds(), { padding: [60, 60], maxZoom: 13 }); } catch (e) {} }
    }

    function fmtTime(ms) { return isFinite(ms) ? new Date(ms).toISOString().slice(5, 16).replace("T", " ") + " UTC" : "—"; }
    function num(v, dgt) { return v == null || !isFinite(v) ? "—" : (+v).toFixed(dgt == null ? 1 : dgt); }

    function renderResult(d, q) {
      const sm = d.summary || {};
      const last = st.frames[st.frames.length - 1] || {};
      const method = typeof d.method === "string" ? d.method
        : (d.method && (d.method.name || d.method.model || (d.method.rate_of_spread ? "Rothermel surface spread with hourly forecast weather" : ""))) || "Physics simulation";
      $("sim-result-tag").textContent = "ML + physics";
      $("sim-msg").innerHTML = `<b>ML + physics</b>. ${st.frames.length} frames over ${esc(q.hours)} h (${esc(q.step_min)}-minute steps), ignition: ${esc(({ live: "nearby live detections", point: "selected point", archive: "archive fire cells" })[q.ignition])}.` +
        (sm.n_fires > 1 ? ` Areas total ${sm.n_fires} nearby fire groups.` : "") +
        (sm.note ? ` <span class="muted">${esc(sm.note)}</span>` : "");
      const k = $("sim-kpis");
      const area = sm.burned_km2 != null ? sm.burned_km2 : last.burned_km2;
      const ros = sm.max_ros_m_min != null ? sm.max_ros_m_min : sm.max_ros != null ? sm.max_ros : Math.max(...st.frames.map((f) => +f.ros || 0));
      const envK = sm.envelope_km2 || {};
      k.innerHTML = `<div class="kpi"><span class="kpi-val">${num(area)}</span><span class="kpi-lbl">km² simulated${sm.n_fires > 1 ? ` across ${sm.n_fires} fire groups` : " at end"}${envK.low != null ? ` (weather scenarios ${num(envK.low)}–${num(envK.high)})` : ""}</span></div>` +
        `<div class="kpi"><span class="kpi-val">${num(ros, 2)}</span><span class="kpi-lbl">max spread rate (m/min)</span></div>` +
        `<div class="kpi"><span class="kpi-val">${esc(q.hours)} h</span><span class="kpi-lbl">horizon</span></div>`;
      k.hidden = false;
      // estimate flags from the response, plus our own ignition explanation
      const est = [];
      const ig = d.ignition || sm.ignition || {};
      const isEst = d.estimate === true || ig.estimate === true || sm.estimate === true || q.ignition !== "point";
      if ((isEst || q.ignition === "point") && !ig.note) {
        const where = ig.lat != null ? ` Ignition used: ${(+ig.lat).toFixed(4)}, ${(+ig.lon).toFixed(4)}${ig.source ? " (" + ig.source + ")" : ""}.` : "";
        const snapKm = ig.lat != null ? haversineKm(+q.lat, +q.lon, +ig.lat, +ig.lon) : null;
        est.push(estLine(ignitionReason() + where, snapKm != null && snapKm > 0.05 ? `Ignition snapped ${snapKm.toFixed(2)} km from your point.` : "Ignition location is approximate.",
          q.ignition === "point" ? "Assumed ignition" : "Estimated location"));
      }
      if (ig.note) est.push(estLine(ig.note, ig.cells ? `Ignition from ${ig.points ? ig.points.length : ig.cells} detection${(ig.points || []).length === 1 ? "" : "s"} (${ig.cells} grid cells).` : "Ignition note.", "Ignition estimate"));
      if (ig.fallback_reason) est.push(estLine(ig.fallback_reason, `Requested "${ig.requested_mode}", used "${ig.mode}" instead.`, "Fallback"));
      const fu = d.fuel || {};
      if (fu.class) est.push(estLine(fu.why || "Fuel class assumed, no fuel map used.", `Fuel: ${fu.class}${fu.model ? " · " + fu.model : ""}${fu.assumption ? " (assumed)" : ""}.`, fu.assumption ? "Fuel assumption" : "Fuel"));
      const wxs = d.weather || {};
      if (wxs.mode) est.push(estLine(wxs.note_time || "Weather updates each simulation hour.",
        wxs.mode === "historical_reanalysis_replay" ? "Historical weather replay; observed future weather." :
        wxs.mode === "fallback_scenario" ? "Weather unavailable: illustrative fallback scenario." :
        "Hourly forecast weather · " + (wxs.spatial_mode === "grid" ? "spatial grid" : "single point"), "Weather source"));
      if (wxs.fallback || (wxs.errors && wxs.errors.length)) est.push(estLine((wxs.errors || []).join("; ") || "Weather service unavailable; fallback weather used.", wxs.fallback ? "Weather fallback in use." : "Weather coverage or source is reduced.", "Weather estimate"));
      if (d.landscape) est.push(estLine(d.landscape.error || d.landscape.barrier_note || "Landscape layers loaded.",
        d.landscape.applied ? "Mapped fuel, terrain and vegetation conditions used where available." :
        "Landscape unavailable; assumed fuel and fallback terrain used.", "Landscape"));
      if (sm.reached_domain_edge) est.push(estLine("The fire reached the edge of the simulation grid, so the final area is a lower bound.", "Fire reached the simulation domain edge.", "Truncated"));
      if (typeof d.uncertainty === "string") est.push(`<p class="sim-uncert">${esc(d.uncertainty)}</p>`);
      const reasons = [].concat(d.estimate_reasons || d.estimates || sm.estimate_reasons || []).filter((r) => typeof r === "string");
      if (typeof d.estimate_reason === "string") reasons.push(d.estimate_reason);
      reasons.forEach((r) => est.push(estLine(r, r.length > 90 ? r.slice(0, 88) + "…" : r, "Estimate")));
      if (st.frames.some((f) => f.env.low || f.env.high)) est.push(`<p class="est-line muted">Dashed outlines show the low and high envelopes for the current frame.</p>`);
      const osm = (d.landscape && d.landscape.layers && d.landscape.layers.osm) || {};
      if (osm.optional && osm.coverage_pct < 100) est.push(estLine(
        osm.reason || "Uncached OpenStreetMap barriers were not awaited.",
        `Road and waterway barriers: ${num(osm.coverage_pct || 0, 0)}% cached coverage. Spread may cross unmapped barriers.`,
        "Optional barriers"));
      $("sim-est").innerHTML = est.join("");
      (ig.points || []).slice(0, 300).forEach(([x, y]) => {
        L.circleMarker([y, x], { radius: 3, color: "#fff", weight: 1, fillColor: "#010658", fillOpacity: 1, interactive: false }).addTo(st.top);
      });
      if (ig.lat != null) {
        L.circleMarker([+ig.lat, +ig.lon], { radius: 6, color: "#fff", weight: 2.5, fillColor: "#010658", fillOpacity: 1 })
          .bindTooltip(`Ignition (estimated) ${(+ig.lat).toFixed(4)}, ${(+ig.lon).toFixed(4)}`).addTo(st.top);
      }
      if (renderResult.url) URL.revokeObjectURL(renderResult.url);
      renderResult.url = URL.createObjectURL(new Blob([JSON.stringify(d)], { type: "application/json" }));
      $("sim-dl").href = renderResult.url;
      $("sim-downloads").hidden = false;
    }

    function show(i) {
      const n = st.frames.length;
      if (!n) return;
      i = Math.max(0, Math.min(n - 1, i));
      st.idx = i;
      const f = st.frames[i];
      st.layer.clearLayers();
      for (let k = i; k >= 0; k--) if (st.frameLayers[k]) st.frameLayers[k].addTo(st.layer);  // newest (largest) at the bottom
      st.top.eachLayer((l) => { if (l.options && l.options.envelope) st.top.removeLayer(l); });
      if (f.geo) L.geoJSON(f.geo, { interactive: false, envelope: true, style: { color: "#010658", weight: 2.2, fill: false } }).eachLayer((l) => { l.options.envelope = true; l.addTo(st.top); });
      ["low", "high"].forEach((kind) => {
        if (f.env[kind]) L.geoJSON(f.env[kind], { interactive: false, style: { color: kind === "low" ? "#2F6DB5" : "#a14d00", weight: 1.6, dashArray: "5 5", fill: false } })
          .eachLayer((l) => { l.options.envelope = true; l.addTo(st.top); });
      });
      st.guide.clearLayers();
      if (st.guidanceLayers[f.guidance_day]) st.guidanceLayers[f.guidance_day].addTo(st.guide);
      pb("guidance").textContent = `Risk guidance: day ${f.guidance_day}, valid through ${String(f.guidance_valid_until_utc || "").replace("T", " ").replace("Z", " UTC")}.`;
      pb("slider").value = i;
      pb("slider").setAttribute("aria-valuetext", `Frame ${i + 1} of ${n}, ${fmtTime(f.t)}`);
      const h = (f.t - st.frames[0].t) / 3600000;
      pb("time").textContent = `T+${isFinite(h) ? (h % 1 ? h.toFixed(1) : h) : i} h · ${fmtTime(f.t)}`;
      const ek = f.envelope_km2 || {};
      const fireGroups = (st.data.summary || {}).n_fires;
      pb("area").textContent = `${num(f.burned_km2, 2)} km² simulated${fireGroups > 1 ? ` across ${fireGroups} fire groups` : ""}${ek.low != null ? ` (weather scenarios ${num(ek.low, 1)}–${num(ek.high, 1)})` : ""}${f.new_km2 ? ` · +${num(f.new_km2, 2)} this step` : ""}`;
      const w = f.wx;
      if (w.dir != null) pb("wind-arrow").style.transform = `rotate(${(+w.dir + 180) % 360}deg)`;
      pb("wind").textContent = w.speed != null ? `${num(w.speed, 0)} km/h from ${w.dir != null ? wx.compass(+w.dir) : "?"}${w.gust != null ? `, gusts ${num(w.gust, 0)}` : ""}` : "wind —";
      pb("temp").textContent = w.temp != null ? `${num(w.temp, 0)} °C` : "—";
      pb("rh").textContent = w.rh != null ? `${num(w.rh, 0)}% RH${w.fm != null ? ` · fuel moisture ${num(w.fm, 0)}%` : ""}` : "—";
      pb("rain").textContent = w.rain != null ? `${num(w.rain, 1)} mm` : "—";
      pb("ros").textContent = f.ros != null ? `ROS ${num(f.ros, 2)} m/min${f.fire_type ? " · " + f.fire_type : ""}${f.max_intensity_kw_m != null ? ` · ${Math.round(f.max_intensity_kw_m)} kW/m` : ""}` : "—";
      pb("back").disabled = pb("start").disabled = i === 0;
      pb("fwd").disabled = pb("end").disabled = i === n - 1;
      if (window.EquiFire.emit) window.EquiFire.emit("frame", i, f);
      updateGlobeBbox();
    }

    function setPlayIcon() {
      const b = pb("play");
      b.innerHTML = icon(st.playing ? "pause" : "play") + `<span>${st.playing ? "Pause" : "Play"}</span>`;
      b.setAttribute("aria-label", st.playing ? "Pause" : "Play");
    }
    function play() {
      if (st.frames.length < 2) return;
      if (st.idx >= st.frames.length - 1) show(0);
      st.playing = true;
      setPlayIcon();
      clearInterval(st.timer);
      st.timer = setInterval(() => {
        if (st.idx >= st.frames.length - 1) { stop(); return; }
        show(st.idx + 1);
      }, 600 / (+pb("speed").value || 1));
    }
    function stop() {
      st.playing = false;
      clearInterval(st.timer);
      setPlayIcon();
    }
    pb("play").addEventListener("click", () => (st.playing ? stop() : play()));
    pb("back").addEventListener("click", () => { stop(); show(st.idx - 1); });
    pb("fwd").addEventListener("click", () => { stop(); show(st.idx + 1); });
    pb("start").addEventListener("click", () => { stop(); show(0); });
    pb("end").addEventListener("click", () => { stop(); show(st.frames.length - 1); });
    pb("slider").addEventListener("input", (e) => { stop(); show(+e.target.value); });
    pb("speed").addEventListener("change", () => { if (st.playing) play(); });
    $("playback").addEventListener("keydown", (e) => {
      if (e.key === " " && !/BUTTON|SELECT/.test(e.target.tagName)) { e.preventDefault(); st.playing ? stop() : play(); }
    });
    L.DomEvent.disableClickPropagation($("playback"));
    L.DomEvent.disableScrollPropagation($("playback"));
    $("sim-show").addEventListener("click", () => {
      if (!st.frames.length) return;
      showPlayback(); show(st.idx);
      if (mq.matches) window.scrollTo({top: 0, behavior: "auto"});
      pb("slider").focus({preventScroll: true});
    });

    // phones: dock the player in the panel, right under the map, so it does not cover the fire
    const mq = window.matchMedia("(max-width: 800px)");
    function placePlayback() {
      const node = $("playback");
      if (mq.matches) { node.classList.add("docked"); $("panel").prepend(node); }
      else { node.classList.remove("docked"); $("viewport-container").appendChild(node); }
    }
    (mq.addEventListener ? mq.addEventListener("change", placePlayback) : mq.addListener(placePlayback));
    placePlayback();
    const playerSize = new ResizeObserver(() => {
      $("viewport-container").style.setProperty("--playback-height", `${$("playback").getBoundingClientRect().height}px`);
    });
    playerSize.observe($("playback"));
    function setTimelineReady(ready) {
      ["play", "slider", "speed", "start", "back", "fwd", "end"].forEach(id => { pb(id).disabled = !ready; });
    }
    function reset(message) {
      st.seq++; st.running = false;
      if (st.controller) st.controller.abort();
      stop(); clearLayers(); predLayer.clearLayers(); actualLayer.clearLayers();
      st.data = null; st.frames = []; st.frameLayers = []; st.guidanceLayers = {}; st.idx = 0;
      pb("slider").value = "0"; pb("slider").max = "0";
      pb("slider").setAttribute("aria-valuetext", "No prediction loaded");
      pb("time").textContent = "Timeline"; pb("area").textContent = "";
      pb("guidance").textContent = ""; pb("conf").hidden = true;
      ["wind", "temp", "rh", "rain", "ros"].forEach(id => { pb(id).textContent = "—"; });
      pb("status").textContent = message || "Select a fire and run a prediction to use the timeline.";
      setTimelineReady(false);
      $("sim-result").hidden = true; $("ml-result").hidden = true;
      el.btnForecast.disabled = !state.selectedPoint;
      if (state.mode === "date") showPlayback();
      if (window.EquiFire && window.EquiFire.emit) window.EquiFire.emit("reset");
    }
    function showPlayback() { $("playback").hidden = false; }
    function hidePlayback() { stop(); $("playback").hidden = true; }
    function clearLayers() { st.layer.clearLayers(); st.top.clearLayers(); st.guide.clearLayers(); }
    function updateLegend() {
      const items = document.querySelectorAll(".forecast-legend");
      const inDate = state.mode === "date";
      items[0].hidden = true; items[1].hidden = !inDate; items[2].hidden = true;
      $("legend-guidance").hidden = !inDate;
      if (inDate) $("map-context-description").textContent = "Hourly spread · daily risk";
    }

    function globeFeatures() {
      if (!st.frames.length || $("playback").hidden) return [];
      const f = st.frames[st.idx];
      if (!f || !f.geo) return [];
      const feats = f.geo.type === "FeatureCollection" ? f.geo.features : [f.geo];
      const [r, g, b] = (frameColor(st.idx).match(/\d+/g) || [0, 0, 0]).map(Number);
      const physical = feats.filter((x) => x && x.geometry).map((x) => ({ type: "Feature", properties: { sim: true, fill: `rgba(${r},${g},${b},0.6)` }, geometry: x.geometry }));
      const guidance = ((st.data && st.data.guidance.features) || []).filter(x => x.properties.selected && +x.properties.day === f.guidance_day)
        .map(x => ({...x, properties: {...x.properties, sim: true, fill: "rgba(81,76,241,0.2)"}}));
      return [...guidance, ...physical];
    }

    $("sim-ignition").addEventListener("change", () => syncControls(false));
    ["sim-hours", "sim-step", "sim-start", "sim-fuel"].forEach(id => $(id).addEventListener("change", () => {
      if (id === "sim-start" && ign() === "archive") {
        const date = $(id).value.slice(0, 10);
        if (date && date !== el.date.value) { el.date.value = date; loadFires(); }
      }
      reset("Settings changed. Run ML + physics to update the timeline."); syncService();
    }));

    return {
      initialize, reset, showPlayback, syncControls, usesLiveFires, onPointSelected, run, load,
      hidePlayback, clearLayers, globeFeatures, checkService, updateLegend,
      // read-only access for add-on modules
      get frames() { return st.frames; }, get data() { return st.data; }, get idx() { return st.idx; },
      get running() { return st.running; }, get params() { return st.params; }, show, stop,
    };
  })();

  // Bridge for add-on modules (ui-*.js): shared map, API helpers and workspace switching.
  window.EquiFire = {
    map, API, state, getJSON, showError, esc, icon, switchMode, hydrateIcons,
    onModeChange: (fn) => { (window.EquiFire._modeHooks = window.EquiFire._modeHooks || []).push(fn); },
    // events from the forecast player: "load"(data, query, runOpts), "frame"(index, frame), "reset", "point"(lat, lon)
    on: (ev, fn) => { const h = window.EquiFire._h = window.EquiFire._h || {}; (h[ev] = h[ev] || []).push(fn); },
    emit: (ev, ...a) => { ((window.EquiFire._h || {})[ev] || []).forEach((fn) => { try { fn(...a); } catch (e) { console.error(e); } }); },
    sim, options: { calibration: "off" },
  };

  // Initialize
  setBbox(-51.17, -6.67, -51.03, -6.53);
  sim.initialize();
  switchView("map");
  loadFires();
  pollHealth();
  sats.start();
  hydrateIcons();
})();
