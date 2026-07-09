// Settings Mini App. All requests authenticate with the raw initData in the
// Authorization header — the backend trusts nothing else. Keys must match
// prefs.DEFAULTS on the Python side; the PUT is a full replacement, so every
// key is always sent.
"use strict";

const KEYS = ["add_warmup", "add_cooldown", "wu_cd_lap_press", "wu_cd_skip_pace"];

const tg = window.Telegram && window.Telegram.WebApp;
const statusEl = document.getElementById("status");

function setStatus(text, isError) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", Boolean(isError));
}

if (!tg || !tg.initData) {
  // Opened in a plain browser (or from a launch method that carries no
  // initData, e.g. a reply-keyboard button): nothing can be authenticated.
  setStatus("Please open this page from the bot's settings button in Telegram.", true);
} else {
  tg.ready();
  init();
}

function api(method, body) {
  return fetch("/api/prefs", {
    method: method,
    headers: {
      "Authorization": "tma " + tg.initData,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function init() {
  try {
    const res = await api("GET");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const prefs = await res.json();
    for (const key of KEYS) {
      document.getElementById(key).checked = Boolean(prefs[key]);
    }
  } catch (e) {
    setStatus("Couldn't load your settings. Close and try again.", true);
    return;
  }
  document.body.classList.remove("loading");
  setStatus("These apply to every workout you send the bot.");

  tg.MainButton.setText("Save");
  tg.MainButton.onClick(save);
  tg.MainButton.show();
}

let saving = false;

async function save() {
  if (saving) return; // MainButton can fire twice on a quick double-tap
  saving = true;
  tg.MainButton.showProgress();
  const body = {};
  for (const key of KEYS) {
    body[key] = document.getElementById(key).checked;
  }
  try {
    const res = await api("PUT", body);
    if (!res.ok) throw new Error("HTTP " + res.status);
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    tg.close();
  } catch (e) {
    tg.MainButton.hideProgress();
    setStatus("Saving failed. Check your connection and try again.", true);
    saving = false;
  }
}
