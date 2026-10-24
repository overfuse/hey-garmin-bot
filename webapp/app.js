// Settings Mini App. All requests authenticate with the raw initData in the
// Authorization header — the backend trusts nothing else. Keys must match
// prefs.DEFAULTS on the Python side. Every key is sent, but the server merges
// over stored prefs, so an older cached copy of this file still saves.
"use strict";

const KEYS = [
  "add_warmup", "add_cooldown", "wu_cd_lap_press", "wu_cd_skip_pace", "reject_non_workouts",
  "show_processing_time",
];

// Page copy. The bot's own messages live in locales/*.ftl; this static page
// can't reach those, so its strings live here. index.html carries the Russian
// text as the pre-JS fallback; elements opt in with data-i18n="<key>".
const STRINGS = {
  ru: {
    title: "Настройки тренировок",
    structure: "Структура тренировки",
    add_warmup: "Всегда добавлять разминку",
    add_warmup_hint: "Добавлять разминку, даже если в плане её нет",
    add_cooldown: "Всегда добавлять заминку",
    add_cooldown_hint: "Добавлять заминку, даже если в плане её нет",
    wu_cd_lap_press: "Завершать разминку/заминку кнопкой круга",
    wu_cd_lap_press_hint: "Без дистанции и времени — закончить нажатием кнопки Lap на часах",
    wu_cd_skip_pace: "Без темпа на разминке/заминке",
    wu_cd_skip_pace_hint: "Не задавать целевой темп для разминки и заминки",
    reject_non_workouts: "Игнорировать сообщения, которые не тренировки",
    reject_non_workouts_hint: "Не отправлять в Garmin болтовню, вопросы и благодарности. Выключите, если настоящая тренировка отклоняется",
    messages: "Сообщения бота",
    show_processing_time: "Показывать время обработки",
    show_processing_time_hint: "Добавлять «⚡ Обработано за N мс» к подтверждению загрузки",
    language: "Язык",
    language_hint: "Язык сообщений бота и этой страницы",
    loading: "Загрузка…",
    applies: "Применяется ко всем тренировкам, которые вы отправляете боту.",
    open_in_telegram: "Откройте эту страницу кнопкой настроек бота в Telegram.",
    load_failed: "Не удалось загрузить настройки. Закройте и попробуйте снова.",
    save_failed: "Не удалось сохранить. Проверьте соединение и попробуйте снова.",
    save: "Сохранить",
  },
  en: {
    title: "Workout settings",
    structure: "Workout structure",
    add_warmup: "Always include warmup",
    add_warmup_hint: "Add a warmup even when the plan doesn't mention one",
    add_cooldown: "Always include cooldown",
    add_cooldown_hint: "Add a cooldown even when the plan doesn't mention one",
    wu_cd_lap_press: "End warmup/cooldown on lap press",
    wu_cd_lap_press_hint: "Skip their distance or time — finish by pressing the lap button on the watch",
    wu_cd_skip_pace: "No pace for warmup/cooldown",
    wu_cd_skip_pace_hint: "Skip pace targets on warmup and cooldown",
    reject_non_workouts: "Ignore messages that aren't workouts",
    reject_non_workouts_hint: "Don't send chat, questions or thanks to Garmin. Turn off if a real workout gets rejected",
    messages: "Bot messages",
    show_processing_time: "Show processing time",
    show_processing_time_hint: "Add \"⚡ Processed in N ms\" to the upload confirmation",
    language: "Language",
    language_hint: "For the bot's messages and this page",
    loading: "Loading…",
    applies: "These apply to every workout you send the bot.",
    open_in_telegram: "Please open this page from the bot's settings button in Telegram.",
    load_failed: "Couldn't load your settings. Close and try again.",
    save_failed: "Saving failed. Check your connection and try again.",
    save: "Save",
  },
};

const tg = window.Telegram && window.Telegram.WebApp;
const statusEl = document.getElementById("status");
// Absent if Telegram served a cached index.html from before the selector.
const langEl = document.getElementById("lang");
let lang = "ru";
let statusKey = "loading";

function t(key) {
  return (STRINGS[lang] || STRINGS.ru)[key];
}

function render() {
  document.documentElement.lang = lang;
  document.title = t("title");
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  statusEl.textContent = t(statusKey);
  if (tg && tg.MainButton) tg.MainButton.setText(t("save"));
}

function setStatus(key, isError) {
  statusKey = key;
  statusEl.textContent = t(key);
  statusEl.classList.toggle("error", Boolean(isError));
}

if (!tg || !tg.initData) {
  // Opened in a plain browser (or from a launch method that carries no
  // initData, e.g. a reply-keyboard button): nothing can be authenticated.
  setStatus("open_in_telegram", true);
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
    if (prefs.lang in STRINGS) lang = prefs.lang;
  } catch (e) {
    setStatus("load_failed", true);
    return;
  }
  if (langEl) {
    langEl.value = lang;
    // Preview the choice right away; it's only stored on Save.
    langEl.addEventListener("change", () => {
      lang = langEl.value;
      render();
    });
  }
  document.body.classList.remove("loading");
  statusKey = "applies";
  render();

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
  if (langEl) body.lang = langEl.value;
  try {
    const res = await api("PUT", body);
    if (!res.ok) throw new Error("HTTP " + res.status);
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    tg.close();
  } catch (e) {
    tg.MainButton.hideProgress();
    setStatus("save_failed", true);
    saving = false;
  }
}
