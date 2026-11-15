/* Privacy and cookie notice.
   What the site stores: only preferences in this browser's localStorage (theme, language, collapsed cards, density, panel state, "seen" flags for the guide and settings).
   No tracking, no analytics, no advertising cookies, no profiles. The Inter font is self-hosted (fonts.css, SIL OFL), so nothing is requested from Google.
   Maps load tiles from tile servers (OpenStreetMap / Esri) because the map cannot work without them; these receive the visitor's IP address like any web request.
   The acknowledgement is stored under firespot:consent ("ack"). */
(function () {
  "use strict";
  var KEY = "firespot:consent";
  function get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function set(v) { try { localStorage.setItem(KEY, v); localStorage.setItem(KEY + "-at", new Date().toISOString().slice(0, 10)); } catch (e) { /* storage blocked */ } }
  function pl() { return (document.documentElement.lang || "en").toLowerCase().indexOf("pl") === 0; }
  function T(en, p) { return pl() ? p : en; }

  var box = null;
  function close() { if (box) { box.remove(); box = null; } }
  function choose(v) { set(v); close(); var b = document.getElementById("consent-open"); if (b) b.focus(); }

  function render(detail) {
    close();
    box = document.createElement("section");
    box.id = "consent"; box.className = "consent"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "false");
    box.setAttribute("aria-labelledby", "consent-title"); box.setAttribute("data-i18n-skip", "");
    box.innerHTML =
      '<h2 id="consent-title">' + T("Cookies and privacy", "Pliki cookie i prywatność") + '</h2>' +
      '<p>' + T("FireSpot stores only your preferences (theme, language, layout) in this browser. We use no tracking, analytics or advertising cookies.",
                "FireSpot zapisuje w tej przeglądarce wyłącznie Twoje ustawienia (motyw, język, układ). Nie używamy plików cookie śledzących, analitycznych ani reklamowych.") + '</p>' +
      (detail ? '<ul class="consent-list">' +
        '<li><b>' + T("Essential (always on)", "Niezbędne (zawsze włączone)") + '</b> ' + T("Preferences in localStorage; map tiles from OpenStreetMap or Esri, because the map cannot work without them (these servers see your IP address like any website).", "Ustawienia w localStorage; kafelki mapy z OpenStreetMap lub Esri, bo mapa nie działa bez nich (te serwery widzą Twój adres IP jak każda strona).") + '</li>' +
        '<li><b>' + T("Fonts", "Czcionki") + '</b> ' + T("The Inter font is served from this site (SIL Open Font License); nothing is requested from Google.", "Czcionka Inter jest serwowana z tej strony (licencja SIL OFL); nic nie jest pobierane z Google.") + '</li>' +
        '<li><b>' + T("Not used", "Nie używamy") + '</b> ' + T("Analytics, advertising, social media widgets, or any personal profile.", "Analityki, reklam, wtyczek społecznościowych ani profili użytkowników.") + '</li>' +
        '<li>' + T("Our server and the Cloudflare tunnel keep ordinary request logs (IP address, time, page). Satellite layers (Esri imagery, rain radar) load only if you switch them on.", "Nasz serwer i tunel Cloudflare prowadzą zwykłe logi żądań (adres IP, czas, strona). Warstwy satelitarne (obrazy Esri, radar opadów) ładują się tylko po ich włączeniu.") + '</li></ul>' : '') +
      '<div class="consent-row"><button type="button" class="consent-btn primary" data-c="ack">' + T("Got it", "Rozumiem") + '</button>' +
      '<button type="button" class="consent-link" data-c="more" aria-expanded="' + (detail ? "true" : "false") + '">' + (detail ? T("Hide details", "Ukryj szczegóły") : T("Details", "Szczegóły")) + '</button></div>';
    document.body.appendChild(box);
    box.addEventListener("click", function (e) {
      var b = e.target.closest("[data-c]"); if (!b) return;
      if (b.dataset.c === "more") { render(!detail); var n = document.querySelector('#consent [data-c="more"]'); if (n) n.focus(); return; }
      choose(b.dataset.c);
    });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape" && get()) { close(); } });
    if (!detail) { var f = box.querySelector('[data-c="ack"]'); if (f && !get()) setTimeout(function () { f.focus({ preventScroll: true }); }, 50); }
  }

  // footer link to reopen the choice
  function addOpener() {
    var foot = document.querySelector("#panel .panel-footer"); if (!foot || document.getElementById("consent-open")) return;
    var b = document.createElement("button"); b.type = "button"; b.id = "consent-open"; b.className = "consent-link consent-open";
    b.textContent = T("Cookie and privacy settings", "Ustawienia cookie i prywatności");
    b.addEventListener("click", function () { render(true); });
    foot.appendChild(b);
    document.addEventListener("equifire:langchange", function () { b.textContent = T("Cookie and privacy settings", "Ustawienia cookie i prywatności"); if (box) render(!!box.querySelector(".consent-list")); });
  }

  var c = get();
    addOpener();
  if (!c) render(false);
})();
