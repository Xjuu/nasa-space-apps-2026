(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const tr = (pl, en) => document.documentElement.lang === "pl" ? pl : en;
  const sec = FX.section({ id: "telegram-alert", title: "Telegram notification", icon: "send", order: 56 });
  sec.body.innerHTML =
    `<p class="fx-note">${tr("Wysyła pojedynczą wiadomość dopiero po wpisaniu obszaru i kliknięciu przycisku. To nie jest subskrypcja ani automatyczny alert.", "Sends one message only after you enter an area and click the button. This is not a subscription or an automatic alert.")}</p>` +
    `<p id="fx-tg-config" class="fx-note" role="status">${tr("Sprawdzanie konfiguracji…", "Checking configuration…")}</p>` +
    `<form id="fx-tg-form">` +
    `<label class="input-field"><span>${tr("Nazwa obszaru", "Area name")}</span><input name="area_name" required maxlength="80" autocomplete="off"></label>` +
    `<label class="input-field"><span>${tr("Szerokość geograficzna", "Latitude")}</span><input name="lat" type="number" min="-90" max="90" step="any" required></label>` +
    `<label class="input-field"><span>${tr("Długość geograficzna", "Longitude")}</span><input name="lon" type="number" min="-180" max="180" step="any" required></label>` +
    `<label class="input-field"><span>${tr("Promień (km)", "Radius (km)")}</span><input name="radius_km" type="number" min="0.1" max="250" step="any" value="10" required></label>` +
    `<div class="fx-actions"><button id="fx-tg-send" type="submit" class="tool-btn" disabled>${tr("Wyślij jedną wiadomość", "Send one notification")}</button></div>` +
    `<p id="fx-tg-status" class="fx-note" role="status" aria-live="polite"></p></form>`;
  const form = sec.body.querySelector("#fx-tg-form");
  const config = sec.body.querySelector("#fx-tg-config");
  const send = sec.body.querySelector("#fx-tg-send");
  const status = sec.body.querySelector("#fx-tg-status");
  let configured = false, checking = false, checked = false;

  async function checkConfig() {
    if (checking || (checked && configured)) return;
    checking = true;
    send.disabled = true;
    config.textContent = tr("Sprawdzanie konfiguracji…", "Checking configuration…");
    try {
      const result = await FX.fetchJSON("/telegram/config");
      configured = result.configured === true;
      checked = true;
      config.textContent = configured
        ? tr("Telegram jest skonfigurowany. Wiadomość zostanie wysłana wyłącznie po wysłaniu tego formularza.", "Telegram is configured. A message is sent only when you submit this form.")
        : tr("Brak konfiguracji. Administrator musi ustawić TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID w środowisku serwera lub pliku .env; bez nich nic nie zostanie wysłane.", "Not configured. The server administrator must set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the server environment or .env file; nothing is sent without them.");
      send.disabled = !configured;
    } catch (error) {
      checked = false;
      config.textContent = error.message;
    } finally {
      checking = false;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "";
    if (!form.reportValidity() || send.disabled || !configured) return;
    send.disabled = true;
    status.textContent = tr("Wysyłanie…", "Sending…");
    const values = new FormData(form);
    const body = {
      area_name: String(values.get("area_name")).trim(),
      lat: Number(values.get("lat")),
      lon: Number(values.get("lon")),
      radius_km: Number(values.get("radius_km")),
    };
    try {
      await FX.fetchJSON("/telegram/send", null, { method: "POST", body });
      status.textContent = tr("Wiadomość została wysłana.", "Notification sent.");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      send.disabled = !configured;
    }
  });
  sec.onOpen(checkConfig);
  checkConfig();
})();
