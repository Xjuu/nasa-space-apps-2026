/* Space savers for the side panel: (1) every card gets a chevron that collapses its body, remembered per browser;
   (2) a bar with "Collapse all / Expand all" and a density switch (compact is the default); (3) a button that hides the whole side panel
   so the map uses the full width (desktop). Only moves existing nodes into a wrapper, ids and event handlers keep working. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  const panel = document.getElementById("panel");
  if (!EF || !panel) return;
  const LS = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } return null; };
  const pl = () => (document.documentElement.lang || "en").startsWith("pl");
  const T = (a, b) => (pl() ? a : b);
  let closed = {};
  try { closed = JSON.parse(LS("firespot:collapsed") || "{}") || {}; } catch (e) { closed = {}; }
  const save = () => LS("firespot:collapsed", JSON.stringify(closed));
  const chev = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  function enhance(card) {
    if (card.dataset.cd || card.classList.contains("section-replay")) return;
    const row = card.querySelector(":scope > .section-title-row") || card.querySelector(":scope > h2, :scope > h3");
    if (!row) return;
    card.dataset.cd = "1";
    const key = card.id || (row.textContent || "").trim().slice(0, 30);
    const body = document.createElement("div");
    body.className = "cd-body";
    let n = row.nextSibling;
    while (n) { const nx = n.nextSibling; body.appendChild(n); n = nx; }
    card.appendChild(body);
    new MutationObserver((ms) => { ms.forEach((m) => m.addedNodes.forEach((x) => { if (x.parentNode === card && x !== body && x !== row) body.appendChild(x); })); }).observe(card, { childList: true });
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "cd-toggle"; btn.innerHTML = chev;
    row.classList.add("cd-row"); row.appendChild(btn);
    const set = (open, persist) => {
      card.classList.toggle("cd-collapsed", !open); body.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      btn.setAttribute("aria-label", (open ? T("Zwiń: ", "Collapse: ") : T("Rozwiń: ", "Expand: ")) + (row.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40));
      if (persist) { if (open) delete closed[key]; else closed[key] = 1; save(); }
    };
    set(!closed[key], false);
    row.addEventListener("click", (e) => { if (e.target.closest("button:not(.cd-toggle), a, select, input, label")) return; set(body.hidden, true); });
    card._cdSet = set;
  }
  const cards = () => [...panel.querySelectorAll(":scope > section.card")];
  function sweep() { cards().forEach(enhance); }

  // toolbar: collapse all / expand all / density
  const bar = document.createElement("div");
  bar.className = "cd-bar";
  bar.innerHTML = `<button type="button" class="cd-btn" data-a="close"></button><button type="button" class="cd-btn" data-a="open"></button><button type="button" class="cd-btn" data-a="dense" aria-pressed="true"></button>`;
  const head = panel.querySelector(".panel-header");
  (head || panel.firstElementChild).insertAdjacentElement("afterend", bar);
  function labels() {
    const dense = document.documentElement.classList.contains("dense");
    bar.querySelector('[data-a="close"]').textContent = T("Zwiń wszystko", "Collapse all");
    bar.querySelector('[data-a="open"]').textContent = T("Rozwiń wszystko", "Expand all");
    const d = bar.querySelector('[data-a="dense"]'); d.textContent = dense ? T("Gęsto", "Compact") : T("Luźno", "Roomy"); d.setAttribute("aria-pressed", String(dense));
  }
  const dense0 = LS("firespot:dense");
  document.documentElement.classList.toggle("dense", dense0 !== "0");
  bar.addEventListener("click", (e) => {
    const a = e.target.closest("[data-a]"); if (!a) return;
    if (a.dataset.a === "dense") { const on = !document.documentElement.classList.contains("dense"); document.documentElement.classList.toggle("dense", on); LS("firespot:dense", on ? "1" : "0"); labels(); return; }
    cards().forEach((c) => c._cdSet && c._cdSet(a.dataset.a === "open", true));
    panel.querySelectorAll(":scope > details").forEach((d) => { d.open = a.dataset.a === "open"; });
  });

  // hide / show the whole side panel (desktop)
  const tg = document.createElement("button");
  tg.type = "button"; tg.id = "panel-toggle"; tg.className = "panel-toggle";
  document.body.appendChild(tg);
  let place = () => {};
  const setPanel = (hidden, persist) => {
    document.documentElement.classList.toggle("panel-hidden", hidden);
    tg.setAttribute("aria-expanded", String(!hidden));
    tg.setAttribute("aria-label", hidden ? T("Pokaż panel", "Show panel") : T("Ukryj panel (więcej miejsca dla mapy)", "Hide panel (more room for the map)"));
    tg.innerHTML = hidden ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>' : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>';
    if (persist) LS("firespot:panel", hidden ? "0" : "1");
    place();
    setTimeout(() => { try { EF.map.invalidateSize(); } catch (e) { /* ignore */ } try { const g = EF.globe; if (g) { g.width(g.renderer().domElement.parentElement.clientWidth); } } catch (e) { /* ignore */ } window.dispatchEvent(new Event("resize")); }, 60);
  };
  place = () => { tg.style.right = document.documentElement.classList.contains("panel-hidden") ? "0px" : Math.max(0, window.innerWidth - panel.getBoundingClientRect().left) + "px"; };
  window.addEventListener("resize", place);
  if (window.ResizeObserver) new ResizeObserver(place).observe(panel);
  tg.addEventListener("click", () => setPanel(!document.documentElement.classList.contains("panel-hidden"), true));
  setPanel(LS("firespot:panel") === "0", false);

  labels();
  sweep();
  new MutationObserver(sweep).observe(panel, { childList: true });
  new MutationObserver(labels).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  setTimeout(sweep, 1200);
})();
