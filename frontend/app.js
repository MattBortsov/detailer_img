import {
  authenticationFailed,
  beginSubmission,
  completeSubmission,
  createAppState,
  loadPalette,
  paletteFailed,
  selectChoice,
} from "./state.js";

const HEX_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const BOT_URL_PATTERN = /^https:\/\/t\.me\/[A-Za-z][A-Za-z0-9_]{4,31}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const elements = {
  loading: document.querySelector("#loading-state"),
  ready: document.querySelector("#ready-state"),
  noSource: document.querySelector("#no-source-state"),
  authFailed: document.querySelector("#auth-failed-state"),
  paletteFailed: document.querySelector("#palette-failed-state"),
  form: document.querySelector("#palette-form"),
  grid: document.querySelector("#palette-grid"),
  choiceTemplate: document.querySelector("#choice-template"),
  privacy: document.querySelector("#privacy-copy"),
  announcement: document.querySelector("#selection-status"),
  alert: document.querySelector("#inline-alert"),
  actionHint: document.querySelector("#action-hint"),
  submit: document.querySelector("#submit-button"),
  retry: document.querySelector("#retry-palette"),
};

let state = createAppState();
let sessionExchangeAttempted = false;
const telegram = window.Telegram?.WebApp;

function exactKeys(value, expected) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return (
    actual.length === wanted.length &&
    actual.every((key, index) => key === wanted[index])
  );
}

function validChoice(choice) {
  if (
    !exactKeys(choice, ["color_id", "name", "display_hex", "kind"]) ||
    typeof choice.color_id !== "string" ||
    typeof choice.name !== "string" ||
    choice.name.length === 0
  ) {
    return false;
  }
  if (choice.kind === "color") {
    return (
      typeof choice.display_hex === "string" &&
      HEX_PATTERN.test(choice.display_hex)
    );
  }
  return choice.kind === "surprise" && choice.display_hex === null;
}

function validPaletteState(payload) {
  if (
    !exactKeys(payload, [
      "palette_version",
      "choices",
      "source_ready",
      "source_message_id",
      "bot_chat_url",
      "privacy_text",
      "session_expires_at",
    ]) ||
    typeof payload.palette_version !== "string" ||
    !Array.isArray(payload.choices) ||
    payload.choices.length === 0 ||
    typeof payload.source_ready !== "boolean" ||
    typeof payload.privacy_text !== "string" ||
    typeof payload.session_expires_at !== "string" ||
    !BOT_URL_PATTERN.test(payload.bot_chat_url)
  ) {
    return false;
  }
  if (
    (payload.source_ready &&
      (!Number.isInteger(payload.source_message_id) ||
        payload.source_message_id <= 0)) ||
    (!payload.source_ready && payload.source_message_id !== null)
  ) {
    return false;
  }
  const ids = new Set();
  for (const choice of payload.choices) {
    if (!validChoice(choice) || ids.has(choice.color_id)) {
      return false;
    }
    ids.add(choice.color_id);
  }
  return true;
}

function validSelectionResponse(payload, selectedId) {
  return (
    exactKeys(payload, ["status", "palette_version", "choice"]) &&
    payload.status === "validated" &&
    typeof payload.palette_version === "string" &&
    validChoice(payload.choice) &&
    payload.choice.color_id === selectedId
  );
}

function showOnly(active) {
  for (const section of [
    elements.loading,
    elements.ready,
    elements.noSource,
    elements.authFailed,
    elements.paletteFailed,
  ]) {
    section.hidden = section !== active;
  }
  const heading = active.querySelector("h1");
  if (heading && active !== elements.loading) {
    heading.focus({ preventScroll: true });
  }
}

function renderChoices() {
  elements.grid.replaceChildren();
  for (const choice of state.choices) {
    const fragment = elements.choiceTemplate.content.cloneNode(true);
    const input = fragment.querySelector(".choice-input");
    const label = fragment.querySelector(".choice-card");
    const swatch = fragment.querySelector(".swatch");
    const name = fragment.querySelector(".choice-name");
    const inputId = `choice-${choice.color_id}`;
    input.id = inputId;
    input.value = choice.color_id;
    input.checked = choice.color_id === state.selectedId;
    input.disabled = state.inFlight;
    label.htmlFor = inputId;
    name.textContent = choice.name;
    if (choice.kind === "color") {
      swatch.style.setProperty("--swatch-color", choice.display_hex);
    } else {
      swatch.textContent = "✦";
    }
    elements.grid.append(fragment);
  }
}

function render() {
  if (state.view === "booting") {
    showOnly(elements.loading);
    return;
  }
  if (state.view === "no_active_source") {
    showOnly(elements.noSource);
    return;
  }
  if (state.view === "auth_failed") {
    showOnly(elements.authFailed);
    return;
  }
  if (state.view === "palette_failed") {
    showOnly(elements.paletteFailed);
    return;
  }
  showOnly(elements.ready);
  renderChoices();
  elements.form.setAttribute("aria-busy", String(state.inFlight));
  elements.privacy.textContent = state.privacyText;
  elements.announcement.textContent = state.announcement;
  elements.submit.textContent = state.inFlight
    ? "Проверяем выбор…"
    : state.actionLabel;
  elements.submit.disabled = !state.actionEnabled || state.inFlight;
  elements.actionHint.textContent =
    state.selectedId === null ? "Выберите один вариант" : "";
  elements.alert.hidden = ![
    "selection_stale",
    "submit_failed",
  ].includes(state.view);
  elements.alert.textContent =
    state.view === "selection_stale"
      ? "Этот цвет больше недоступен. Палитра обновлена — выберите другой."
      : state.view === "submit_failed"
        ? "Не удалось подтвердить выбор. Попробуйте ещё раз."
        : "";
}

function applyTelegramTheme() {
  const scheme = telegram?.colorScheme === "dark" ? "dark" : "light";
  document.documentElement.style.colorScheme = scheme;
  if (telegram?.setHeaderColor) {
    telegram.setHeaderColor("bg_color");
  }
  if (telegram?.setBottomBarColor) {
    telegram.setBottomBarColor("bottom_bar_bg_color");
  }
}

function trustedBotUrl(candidate) {
  return typeof candidate === "string" && BOT_URL_PATTERN.test(candidate)
    ? candidate
    : null;
}

function openChat() {
  const url = trustedBotUrl(state.botChatUrl);
  if (!url) {
    return;
  }
  if (telegram?.openTelegramLink) {
    telegram.openTelegramLink(url);
  } else {
    window.location.assign(url);
  }
}

async function exchangeSession() {
  if (sessionExchangeAttempted || !telegram) {
    return false;
  }
  sessionExchangeAttempted = true;
  let launchEvidence = telegram.initData;
  if (typeof launchEvidence !== "string" || launchEvidence.length === 0) {
    launchEvidence = "";
    return false;
  }
  try {
    const response = await fetch("/api/v1/tma/session", {
      method: "POST",
      credentials: "include",
      headers: { Authorization: `tma ${launchEvidence}` },
    });
    if (!response.ok) {
      state = authenticationFailed(
        state,
        trustedBotUrl(response.headers.get("X-Bot-Chat-Url")),
      );
      return false;
    }
    return true;
  } finally {
    launchEvidence = "";
  }
}

async function fetchPalette() {
  try {
    const response = await fetch("/api/v1/palette-state", {
      credentials: "include",
    });
    if (response.status === 401) {
      state = authenticationFailed(state);
      render();
      return;
    }
    if (!response.ok) {
      state = paletteFailed(state);
      render();
      return;
    }
    const payload = await response.json();
    if (!validPaletteState(payload)) {
      state = paletteFailed(state);
      render();
      return;
    }
    state = loadPalette(state, {
      choices: payload.choices,
      sourceReady: payload.source_ready,
      botChatUrl: payload.bot_chat_url,
      privacyText: payload.privacy_text,
    });
  } catch {
    state = paletteFailed(state);
  }
  render();
}

async function bootstrap() {
  if (!(await exchangeSession())) {
    state = authenticationFailed(state, state.botChatUrl);
    render();
    return;
  }
  await fetchPalette();
}

elements.form.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || target.name !== "color_id") {
    return;
  }
  state = selectChoice(state, target.value);
  telegram?.HapticFeedback?.selectionChanged();
  render();
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pending = beginSubmission(state, () => crypto.randomUUID());
  state = pending.state;
  render();
  if (
    !pending.shouldSubmit ||
    state.selectedId === null ||
    state.submissionUuid === null ||
    !UUID_PATTERN.test(state.submissionUuid)
  ) {
    return;
  }
  try {
    const response = await fetch("/api/v1/palette-selection/validate", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        color_id: state.selectedId,
        client_submission_uuid: state.submissionUuid,
      }),
    });
    if (response.status === 401) {
      state = completeSubmission(state, "auth_failed");
    } else if (response.status === 409) {
      state = completeSubmission(state, "stale");
      await fetchPalette();
      return;
    } else if (response.ok) {
      const payload = await response.json();
      state = completeSubmission(
        state,
        validSelectionResponse(payload, state.selectedId)
          ? "validated"
          : "failed",
      );
    } else {
      state = completeSubmission(state, "failed");
    }
  } catch {
    state = completeSubmission(state, "failed");
  }
  render();
});

for (const button of document.querySelectorAll("[data-open-chat]")) {
  button.addEventListener("click", openChat);
}
elements.retry.addEventListener("click", fetchPalette);

if (telegram) {
  telegram.ready();
  telegram.expand();
  telegram.enableVerticalSwipes?.();
  applyTelegramTheme();
  for (const eventName of [
    "themeChanged",
    "safeAreaChanged",
    "contentSafeAreaChanged",
    "viewportChanged",
  ]) {
    telegram.onEvent?.(eventName, applyTelegramTheme);
  }
  void bootstrap();
} else {
  state = authenticationFailed(state);
  render();
}
