(function () {
  "use strict";

  var STORAGE_KEY = "equifire:accessibility";
  var root = document.documentElement;
  var dialog, toggle, fontSizeControl, contrastControl, motionControl, controlsControl, textFirstControl, status;
  var targets = new Set();
  var originalFontSizes = new WeakMap();
  var osMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");

  function readSettings() {
    var stored = {};
    try { stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch (_) {}
    return {
      textScale: ["1", "1.15", "1.3"].indexOf(String(stored.textScale)) >= 0 ? String(stored.textScale) : "1",
      highContrast: stored.highContrast === true,
      reduceMotion: typeof stored.reduceMotion === "boolean" ? stored.reduceMotion : !!(osMotion && osMotion.matches),
      largeControls: stored.largeControls === true,
      textFirst: stored.textFirst === true
    };
  }

  var settings = readSettings();

  function restoreFontSize(element) {
    var original = originalFontSizes.get(element);
    if (!original) return;
    if (original.value) element.style.setProperty("font-size", original.value, original.priority);
    else element.style.removeProperty("font-size");
  }

  function rememberTextElements(container) {
    if (!container || container.nodeType !== Node.ELEMENT_NODE) return;
    var elements = [container].concat(Array.prototype.slice.call(container.querySelectorAll("*")));
    elements.forEach(function (element) {
      if (element.matches("script,style,code,pre,noscript")) return;
      var hasText = Array.prototype.some.call(element.childNodes, function (node) {
        return node.nodeType === Node.TEXT_NODE && node.nodeValue.trim();
      });
      var isControl = element.matches("button,input,select,textarea,summary,[role=button],svg text");
      if (!hasText && !isControl) return;
      if (!originalFontSizes.has(element)) {
        originalFontSizes.set(element, {
          value: element.style.getPropertyValue("font-size"),
          priority: element.style.getPropertyPriority("font-size")
        });
      }
      targets.add(element);
    });
  }

  function applyTextSize() {
    var scaled = [];
    targets.forEach(function (element) {
      if (!element.isConnected) {
        targets.delete(element);
        return;
      }
      restoreFontSize(element);
    });
    targets.forEach(function (element) {
      if (!element.isConnected) return;
      var baseSize = parseFloat(window.getComputedStyle(element).fontSize);
      if (settings.textScale !== "1" && Number.isFinite(baseSize)) scaled.push([element, baseSize]);
    });
    scaled.forEach(function (entry) {
      entry[0].style.setProperty("font-size", (entry[1] * Number(settings.textScale)) + "px", "important");
    });
    root.dataset.a11yTextScale = settings.textScale;
  }

  function persistSettings(message, shouldSave) {
    root.dataset.a11yContrast = settings.highContrast ? "high" : "normal";
    root.dataset.a11yMotion = settings.reduceMotion ? "reduce" : "full";
    root.dataset.a11yControls = settings.largeControls ? "large" : "normal";
    root.dataset.a11yLayout = settings.textFirst ? "text" : "map";
    applyTextSize();
    if (shouldSave !== false) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (_) {}
    }
    if (message) status.textContent = message;
  }

  function syncControls() {
    fontSizeControl.value = settings.textScale;
    contrastControl.checked = settings.highContrast;
    motionControl.checked = settings.reduceMotion;
    controlsControl.checked = settings.largeControls;
    textFirstControl.checked = settings.textFirst;
  }

  function openSettings() {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    toggle.setAttribute("aria-expanded", "true");
  }

  function closeSettings() {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.focus();
  }

  function initialize() {
    dialog = document.getElementById("accessibility-dialog");
    toggle = document.getElementById("accessibility-toggle");
    fontSizeControl = document.getElementById("accessibility-text-size");
    contrastControl = document.getElementById("accessibility-contrast");
    motionControl = document.getElementById("accessibility-motion");
    controlsControl = document.getElementById("accessibility-controls");
    textFirstControl = document.getElementById("accessibility-text-first");
    status = document.getElementById("accessibility-status");
    if (!dialog || !toggle || !fontSizeControl) return;

    rememberTextElements(document.body);
    syncControls();
    persistSettings("", false);

    toggle.addEventListener("click", openSettings);
    document.getElementById("accessibility-close").addEventListener("click", closeSettings);
    dialog.addEventListener("close", function () { toggle.setAttribute("aria-expanded", "false"); });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeSettings();
    });

    fontSizeControl.addEventListener("change", function () {
      settings.textScale = fontSizeControl.value;
      persistSettings("Accessibility settings saved.");
    });
    contrastControl.addEventListener("change", function () {
      settings.highContrast = contrastControl.checked;
      persistSettings("Accessibility settings saved.");
    });
    motionControl.addEventListener("change", function () {
      settings.reduceMotion = motionControl.checked;
      persistSettings("Accessibility settings saved.");
    });
    controlsControl.addEventListener("change", function () {
      settings.largeControls = controlsControl.checked;
      persistSettings("Accessibility settings saved.");
    });
    textFirstControl.addEventListener("change", function () {
      settings.textFirst = textFirstControl.checked;
      persistSettings("Accessibility settings saved.");
    });
    document.getElementById("accessibility-reset").addEventListener("click", function () {
      settings = { textScale: "1", highContrast: false, reduceMotion: !!(osMotion && osMotion.matches), largeControls: false, textFirst: false };
      try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
      syncControls();
      persistSettings("Accessibility settings reset.", false);
    });

    var contentObserver = new MutationObserver(function (mutations) {
      var added = [];
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === Node.ELEMENT_NODE) added.push(node);
        });
      });
      if (added.length) {
        added.forEach(rememberTextElements);
        applyTextSize();
      }
    });
    contentObserver.observe(document.body, { childList: true, subtree: true });

    var themeObserver = new MutationObserver(function () { applyTextSize(); });
    themeObserver.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    window.addEventListener("resize", applyTextSize, { passive: true });
    if (osMotion && typeof osMotion.addEventListener === "function") {
      osMotion.addEventListener("change", function (event) {
        var hasSavedPreference = false;
        try { hasSavedPreference = localStorage.getItem(STORAGE_KEY) !== null; } catch (_) {}
        if (!hasSavedPreference) {
          settings.reduceMotion = event.matches;
          motionControl.checked = event.matches;
          persistSettings("", false);
        }
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})();