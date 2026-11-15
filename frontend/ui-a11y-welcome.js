/* Opens the accessibility settings dialog once on a visitor's first entry (remembered in localStorage).
   After that it only opens from the toolbar button. */
(function () {
  "use strict";
  var KEY = "firespot:a11y-welcomed";
  function seen() { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } }
  function mark() { try { localStorage.setItem(KEY, "1"); } catch (e) { /* storage blocked: shows again next time */ } }
  function init() {
    if (seen()) return;
    var btn = document.getElementById("accessibility-toggle"), dlg = document.getElementById("accessibility-dialog");
    if (!btn || !dlg) return;
    dlg.addEventListener("close", mark);
    setTimeout(function () { if (!seen() && !dlg.open) { btn.click(); mark(); } }, 900);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
