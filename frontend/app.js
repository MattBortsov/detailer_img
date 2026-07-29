/**
 * Card interaction adapted from Kokonut UI Card Flip by @dorianbaffier.
 * MIT License. Vanilla implementation: native face surfaces and select action.
 */
import {
  activateMode,
  authenticationFailed,
  beginSubmission,
  completeSubmission,
  completeUpload,
  createAppState,
  loadAdminQueue,
  loadCustomCatalog,
  loadOwnerColors,
  loadPalette,
  paletteFailed,
  resetUpload,
  selectChoice,
  setCatalogLoading,
  setFlipped,
  startUpload,
  updateUploadProgress,
} from "./state.js";

const HEX_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const BOT_URL_PATTERN = /^https:\/\/t\.me\/[A-Za-z][A-Za-z0-9_]{4,31}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
const ACCEPTED_UPLOAD_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
]);
const STATUS_COPY = Object.freeze({
  pending: "На проверке",
  needs_review: "Нужна проверка администратора",
  rejected: "Цвет не опубликован",
  approved: "Опубликован",
  hidden: "Скрыт",
});

const elements = {
  loading: document.querySelector("#loading-state"),
  ready: document.querySelector("#ready-state"),
  noSource: document.querySelector("#no-source-state"),
  authFailed: document.querySelector("#auth-failed-state"),
  paletteFailed: document.querySelector("#palette-failed-state"),
  accepted: document.querySelector("#accepted-state"),
  closeMiniApp: document.querySelector("#close-mini-app"),
  form: document.querySelector("#palette-form"),
  colorsGrid: document.querySelector("#colors-grid"),
  userColorsGrid: document.querySelector("#user-colors-grid"),
  userColorsEmpty: document.querySelector("#user-colors-empty"),
  colorsCount: document.querySelector("#colors-count"),
  choiceTemplate: document.querySelector("#choice-template"),
  privacy: document.querySelector("#privacy-copy"),
  announcement: document.querySelector("#selection-status"),
  alert: document.querySelector("#inline-alert"),
  actionHint: document.querySelector("#action-hint"),
  submit: document.querySelector("#submit-button"),
  retry: document.querySelector("#retry-palette"),
  loadMore: document.querySelector("#load-more-colors"),
  mineList: document.querySelector("#mine-list"),
  adminPanel: document.querySelector("#admin-panel"),
  adminList: document.querySelector("#admin-list"),
  surprise: document.querySelector("#select-surprise"),
  addDialog: document.querySelector("#add-color-dialog"),
  addForm: document.querySelector("#add-color-form"),
  openAdd: document.querySelector("#open-add-color"),
  closeAdd: document.querySelector("#close-add-color"),
  imageInput: document.querySelector("#color-image"),
  replaceImage: document.querySelector("#replace-color-image"),
  uploadPreview: document.querySelector("#upload-preview"),
  uploadPreviewImage: document.querySelector("#upload-preview-image"),
  nameInput: document.querySelector("#color-name"),
  nameCounter: document.querySelector("#color-name-counter"),
  uploadProgress: document.querySelector("#upload-progress"),
  uploadMessage: document.querySelector("#upload-message"),
  dialogAlert: document.querySelector("#dialog-alert"),
  uploadSubmit: document.querySelector("#upload-submit"),
};

let state = createAppState();
let sessionExchangeAttempted = false;
let catalogLoaded = false;
let previewObjectUrl = null;
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

function validChoice(choice, allowCustom = false) {
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
  if (choice.kind === "custom") {
    return allowCustom && choice.display_hex === null;
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
      "is_admin",
    ]) ||
    typeof payload.palette_version !== "string" ||
    !Array.isArray(payload.choices) ||
    payload.choices.length === 0 ||
    typeof payload.source_ready !== "boolean" ||
    typeof payload.privacy_text !== "string" ||
    typeof payload.session_expires_at !== "string" ||
    typeof payload.is_admin !== "boolean" ||
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

function validAcceptedResponse(payload) {
  return (
    exactKeys(payload, ["job_id", "status", "accepted", "bot_chat_url"]) &&
    typeof payload.job_id === "string" &&
    UUID_PATTERN.test(payload.job_id) &&
    payload.status === "queued" &&
    payload.accepted === true &&
    BOT_URL_PATTERN.test(payload.bot_chat_url)
  );
}

function showOnly(active) {
  for (const section of [
    elements.loading,
    elements.ready,
    elements.noSource,
    elements.authFailed,
    elements.paletteFailed,
    elements.accepted,
  ]) {
    section.hidden = section !== active;
  }
  const heading = active.querySelector("h1");
  if (heading && active !== elements.loading) {
    heading.focus({preventScroll: true});
  }
}

function publicCard(item, kind) {
  if (kind === "color") {
    return {
      id: item.color_id,
      name: item.name,
      kindLabel: "",
      displayHex: item.display_hex,
      previewUrl: null,
    };
  }
  return {
    id: item.selection_id,
    name: item.name,
    kindLabel: "User Color",
    displayHex: null,
    previewUrl: item.preview_url,
  };
}

function cardButton(root, selector) {
  const button = root.querySelector(selector);
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error("Invalid card template");
  }
  return button;
}

function renderCard(item) {
  const fragment = elements.choiceTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".palette-card");
  const front = fragment.querySelector(".card-front");
  const back = fragment.querySelector(".card-back");
  const swatch = fragment.querySelector(".swatch-field");
  const image = fragment.querySelector(".reference-image");
  const frontSurface = cardButton(
    fragment,
    '.card-flip-surface[data-face="front"]',
  );
  const backSurface = cardButton(
    fragment,
    '.card-flip-surface[data-face="back"]',
  );
  const select = cardButton(fragment, ".select-button");
  const flipped = state.flippedId === item.id;
  const selected = state.selectedId === item.id;

  card.dataset.colorId = item.id;
  card.dataset.flipped = String(flipped);
  card.dataset.selected = String(selected);
  for (const surface of [frontSurface, backSurface]) {
    surface.dataset.action = "flip";
    surface.dataset.colorId = item.id;
    surface.ariaExpanded = String(flipped);
  }
  frontSurface.ariaLabel = `Подробнее о цвете ${item.name}`;
  backSurface.ariaLabel = `Скрыть подробности о цвете ${item.name}`;
  select.dataset.action = "select";
  select.dataset.colorId = item.id;
  select.ariaPressed = String(selected);
  select.textContent = selected ? "Выбрано" : "Выбрать";

  for (const name of fragment.querySelectorAll(".card-name")) {
    name.textContent = item.name;
  }
  for (const kind of fragment.querySelectorAll(".card-kind")) {
    kind.textContent = item.kindLabel;
    kind.hidden = item.kindLabel.length === 0;
  }
  if (item.previewUrl) {
    image.src = item.previewUrl;
    image.alt = `Образец цвета ${item.name}`;
    image.hidden = false;
    swatch.hidden = true;
  } else {
    card.style.setProperty("--swatch-color", item.displayHex);
  }
  front.ariaHidden = String(flipped);
  back.ariaHidden = String(!flipped);
  back.inert = !flipped;
  front.inert = flipped;
  return fragment;
}

function renderCards() {
  elements.colorsGrid.replaceChildren(
    ...state.colors.map((item) => renderCard(publicCard(item, "color"))),
  );
  elements.userColorsGrid.replaceChildren(
    ...state.customColors.map((item) => renderCard(publicCard(item, "custom"))),
  );
  elements.colorsCount.textContent = `${state.colors.length} цветов`;
  elements.userColorsEmpty.hidden =
    state.catalogLoading || state.customColors.length > 0;
  elements.loadMore.hidden =
    state.catalogLoading || state.catalogCursor === null;
  elements.loadMore.disabled = state.catalogLoading;
}

function statusItem(item, admin = false) {
  const row = document.createElement("div");
  row.className = "management-item";
  const copy = document.createElement("div");
  const name = document.createElement("strong");
  const status = document.createElement("p");
  const actions = document.createElement("div");
  name.textContent = item.name;
  status.textContent = STATUS_COPY[item.status] ?? item.status;
  actions.className = "management-actions";
  copy.append(name, status);
  row.append(copy, actions);

  const actionNames = admin
    ? [
        ["view", "Посмотреть", ""],
        ["approve", "Одобрить", ""],
        ["reject", "Отклонить", "danger"],
        ["delete", "Удалить", "danger"],
      ]
    : [
        ["rename", "Переименовать", ""],
        ["delete", "Удалить цвет", "danger"],
      ];
  for (const [action, label, className] of actionNames) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.managementAction = action;
    button.dataset.colorId = item.id;
    if (action === "view") {
      button.dataset.previewUrl = item.preview_url;
    }
    button.textContent = label;
    button.className = className;
    actions.append(button);
  }
  if (admin) {
    const preview = document.createElement("img");
    preview.className = "admin-preview";
    preview.alt = `Образец цвета ${item.name}`;
    preview.hidden = true;
    row.append(preview);
  }
  return row;
}

function renderManagement() {
  elements.mineList.replaceChildren(
    ...state.ownerColors.map((item) => statusItem(item)),
  );
  elements.adminPanel.hidden = !state.isAdmin;
  elements.adminList.replaceChildren(
    ...state.adminQueue.map((item) => statusItem(item, true)),
  );
}

function renderMode() {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const active = tab.dataset.mode === state.mode;
    tab.ariaSelected = String(active);
    tab.tabIndex = active ? 0 : -1;
  }
  for (const panel of document.querySelectorAll('[role="tabpanel"]')) {
    panel.hidden = panel.dataset.panel !== state.mode;
  }
}

function renderUpload() {
  const uploading = state.uploadState === "uploading";
  elements.uploadSubmit.disabled = uploading;
  elements.imageInput.disabled = uploading;
  elements.nameInput.disabled = uploading;
  elements.uploadProgress.hidden =
    !uploading || state.uploadProgress === null;
  if (state.uploadProgress !== null) {
    elements.uploadProgress.value = state.uploadProgress;
  }
  elements.uploadMessage.textContent = state.uploadMessage;
  elements.dialogAlert.hidden = state.uploadState !== "failed";
  elements.dialogAlert.textContent =
    state.uploadState === "failed" ? state.uploadMessage : "";
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
  if (state.view === "accepted") {
    showOnly(elements.accepted);
    return;
  }
  showOnly(elements.ready);
  renderMode();
  renderCards();
  renderManagement();
  renderUpload();
  elements.form.ariaBusy = String(state.inFlight);
  elements.privacy.textContent = state.privacyText;
  elements.announcement.textContent = state.announcement;
  elements.submit.textContent = state.inFlight
    ? "Отправляем запрос…"
    : state.actionLabel;
  elements.submit.disabled = !state.actionEnabled || state.inFlight;
  elements.actionHint.textContent =
    state.selectedId === null ? "Выберите один вариант" : "Цвет выбран";
  elements.alert.hidden = ![
    "selection_stale",
    "submit_failed",
    "submission_limited",
  ].includes(state.view);
  elements.alert.textContent =
    state.view === "selection_stale"
      ? "Этот цвет больше недоступен. Выберите другой."
      : state.submissionError;
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

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {credentials: "include", ...options});
  if (response.status === 401) {
    state = authenticationFailed(state);
    render();
    throw new Error("Unauthorized");
  }
  return response;
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
      headers: {Authorization: `tma ${launchEvidence}`},
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
    const response = await fetchJson("/api/v1/palette-state");
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
      isAdmin: payload.is_admin,
    });
  } catch {
    if (state.view !== "auth_failed") {
      state = paletteFailed(state);
    }
  }
  render();
}

function validCatalog(payload) {
  return (
    exactKeys(payload, ["items", "next_cursor"]) &&
    Array.isArray(payload.items) &&
    payload.items.every(
      (item) =>
        exactKeys(item, [
          "selection_id",
          "name",
          "version",
          "preview_url",
          "approved_at",
        ]) &&
        typeof item.selection_id === "string" &&
        typeof item.name === "string" &&
        Number.isInteger(item.version) &&
        item.version > 0 &&
        typeof item.preview_url === "string" &&
        item.preview_url.startsWith("/api/v1/custom-colors/") &&
        typeof item.approved_at === "string",
    ) &&
    (payload.next_cursor === null || typeof payload.next_cursor === "string")
  );
}

async function fetchCatalog(append = false) {
  state = setCatalogLoading(state, true);
  render();
  const suffix =
    append && state.catalogCursor
      ? `?cursor=${encodeURIComponent(state.catalogCursor)}`
      : "";
  try {
    const response = await fetchJson(`/api/v1/custom-colors${suffix}`);
    const payload = await response.json();
    if (!response.ok || !validCatalog(payload)) {
      throw new Error("Invalid catalog");
    }
    state = loadCustomCatalog(state, {
      items: payload.items,
      nextCursor: payload.next_cursor,
      append,
    });
    catalogLoaded = true;
  } catch {
    state = setCatalogLoading(state, false);
  }
  render();
}

async function fetchOwnerColors() {
  try {
    const response = await fetchJson("/api/v1/custom-colors/mine");
    const payload = await response.json();
    if (response.ok && Array.isArray(payload.items)) {
      state = loadOwnerColors(state, payload.items);
    }
  } catch {
    return;
  }
  render();
}

async function fetchAdminQueue() {
  if (!state.isAdmin) {
    return;
  }
  try {
    const response = await fetchJson("/api/v1/custom-colors/admin/review");
    const payload = await response.json();
    const valid =
      exactKeys(payload, ["items"]) &&
      Array.isArray(payload.items) &&
      payload.items.every(
        (item) =>
          exactKeys(item, [
            "id",
            "name",
            "status",
            "preview_concealed",
            "preview_url",
          ]) &&
          typeof item.id === "string" &&
          typeof item.name === "string" &&
          typeof item.status === "string" &&
          item.preview_concealed === true &&
          typeof item.preview_url === "string" &&
          item.preview_url.startsWith("/api/v1/custom-colors/") &&
          item.preview_url.endsWith("/preview?reveal=true"),
      );
    if (response.ok && valid) {
      state = loadAdminQueue(state, payload.items);
    }
  } catch {
    return;
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

function findCardButton(colorId, selector) {
  return [...document.querySelectorAll(selector)].find(
    (button) => button.dataset.colorId === colorId,
  );
}

function findCardSurface(colorId, face) {
  return findCardButton(
    colorId,
    `.card-flip-surface[data-face="${face}"]`,
  );
}

function handleCardAction(event) {
  const button = event.target.closest("[data-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const colorId = button.dataset.colorId;
  if (!colorId) {
    return;
  }
  if (button.dataset.action === "select") {
    const preserveFlip = state.flippedId === colorId;
    state = selectChoice(state, colorId);
    if (preserveFlip) {
      state = setFlipped(state, colorId);
    }
    telegram?.HapticFeedback?.selectionChanged();
    render();
    findCardButton(colorId, ".select-button")?.focus({preventScroll: true});
    return;
  }
  if (button.dataset.action === "flip") {
    const nextFace = button.dataset.face === "front" ? "back" : "front";
    state = setFlipped(state, colorId);
    render();
    findCardSurface(colorId, nextFace)?.focus({preventScroll: true});
  }
}

for (const grid of [elements.colorsGrid, elements.userColorsGrid]) {
  grid.addEventListener("click", handleCardAction);
}

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", async () => {
    state = activateMode(state, tab.dataset.mode);
    render();
    if (state.mode === "users" && !catalogLoaded) {
      await Promise.all([
        fetchCatalog(),
        fetchOwnerColors(),
        fetchAdminQueue(),
      ]);
    }
  });
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const current = tabs.indexOf(event.currentTarget);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    tabs[next].focus();
    tabs[next].click();
  });
}

elements.surprise.addEventListener("click", () => {
  if (state.surprise) {
    state = selectChoice(state, state.surprise.color_id);
    telegram?.HapticFeedback?.selectionChanged();
    render();
  }
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
    const response = await fetchJson("/api/v1/jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        color_id: state.selectedId,
        client_submission_uuid: state.submissionUuid,
      }),
    });
    if (response.status === 202) {
      const payload = await response.json();
      const accepted = validAcceptedResponse(payload);
      state = completeSubmission(
        state,
        accepted ? "accepted" : "failed",
        accepted ? payload.bot_chat_url : null,
      );
    } else if (response.status === 409 || response.status === 429) {
      const payload = await response.json();
      const code =
        exactKeys(payload, ["detail"]) &&
        exactKeys(payload.detail, ["code", "message"])
          ? payload.detail.code
          : null;
      const outcome = {
        no_source: "no_source",
        invalid_selection: "stale",
        active_limit: "active_limit",
        recent_limit: "recent_limit",
      }[code];
      state = completeSubmission(state, outcome ?? "failed");
    } else {
      state = completeSubmission(state, "failed");
    }
  } catch {
    if (state.view !== "auth_failed") {
      state = completeSubmission(state, "failed");
    }
  }
  render();
});

function revokePreview() {
  if (previewObjectUrl !== null) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }
}

function openAddDialog() {
  revokePreview();
  elements.addForm.reset();
  elements.uploadPreview.hidden = true;
  elements.nameCounter.textContent = "0/40";
  state = resetUpload(state);
  elements.addDialog.showModal();
  telegram?.BackButton?.show();
  elements.nameInput.focus();
  renderUpload();
}

function closeAddDialog() {
  elements.addDialog.close();
  telegram?.BackButton?.hide();
  elements.openAdd.focus({preventScroll: true});
}

elements.openAdd.addEventListener("click", openAddDialog);
elements.closeAdd.addEventListener("click", closeAddDialog);
elements.addDialog.addEventListener("cancel", () => {
  telegram?.BackButton?.hide();
  elements.openAdd.focus({preventScroll: true});
});
telegram?.BackButton?.onClick?.(() => {
  if (elements.addDialog.open) {
    closeAddDialog();
  }
});

elements.imageInput.addEventListener("change", () => {
  revokePreview();
  const file = elements.imageInput.files?.[0];
  if (!file) {
    elements.uploadPreview.hidden = true;
    return;
  }
  if (
    file.size > MAX_UPLOAD_BYTES ||
    (file.type && !ACCEPTED_UPLOAD_TYPES.has(file.type))
  ) {
    elements.imageInput.value = "";
    elements.dialogAlert.hidden = false;
    elements.dialogAlert.textContent =
      "Не удалось обработать изображение. Выберите другое фото в поддерживаемом формате.";
    return;
  }
  previewObjectUrl = URL.createObjectURL(file);
  elements.uploadPreviewImage.src = previewObjectUrl;
  elements.uploadPreview.hidden = false;
  elements.dialogAlert.hidden = true;
});

elements.replaceImage.addEventListener("click", () => elements.imageInput.click());
elements.nameInput.addEventListener("input", () => {
  elements.nameCounter.textContent = `${elements.nameInput.value.length}/40`;
});

function uploadColor(formData) {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/v1/custom-colors");
    request.withCredentials = true;
    request.setRequestHeader("Idempotency-Key", crypto.randomUUID());
    request.upload.addEventListener("progress", (event) => {
      state = updateUploadProgress(
        state,
        event.lengthComputable ? Math.round((event.loaded / event.total) * 100) : null,
      );
      renderUpload();
    });
    request.addEventListener("load", () => resolve(request.status === 202));
    request.addEventListener("error", () => resolve(false));
    request.send(formData);
  });
}

elements.addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.imageInput.files?.[0];
  const name = elements.nameInput.value.trim();
  if (!file || name.length < 1 || name.length > 40) {
    elements.dialogAlert.hidden = false;
    elements.dialogAlert.textContent = "Добавьте одно фото и название цвета.";
    return;
  }
  const formData = new FormData();
  formData.append("name", name);
  formData.append("image", file, file.name);
  state = startUpload(state);
  renderUpload();
  state = completeUpload(state, (await uploadColor(formData)) ? "accepted" : "failed");
  renderUpload();
  if (state.uploadState === "pending") {
    await fetchOwnerColors();
  }
});

elements.mineList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-management-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const colorId = button.dataset.colorId;
  const action = button.dataset.managementAction;
  if (!colorId || !action) {
    return;
  }
  if (action === "rename") {
    const name = window.prompt("Новое название цвета");
    if (!name) {
      return;
    }
    await fetchJson(`/api/v1/custom-colors/${encodeURIComponent(colorId)}`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
  } else if (
    window.confirm(
      "Удалить цвет? Он сразу исчезнет из каталога и станет недоступен для новых запросов.",
    )
  ) {
    await fetchJson(`/api/v1/custom-colors/${encodeURIComponent(colorId)}`, {
      method: "DELETE",
    });
  }
  await Promise.all([fetchOwnerColors(), fetchCatalog()]);
});

elements.adminList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-management-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const colorId = button.dataset.colorId;
  const action = button.dataset.managementAction;
  if (!colorId || !action) {
    return;
  }
  if (action === "view") {
    const previewUrl = button.dataset.previewUrl;
    const preview = button
      .closest(".management-item")
      ?.querySelector(".admin-preview");
    if (
      !(preview instanceof HTMLImageElement) ||
      typeof previewUrl !== "string" ||
      !previewUrl.startsWith("/api/v1/custom-colors/") ||
      !previewUrl.endsWith("/preview?reveal=true")
    ) {
      return;
    }
    if (preview.hidden) {
      preview.src = previewUrl;
      preview.hidden = false;
      button.textContent = "Скрыть";
    } else {
      preview.removeAttribute("src");
      preview.hidden = true;
      button.textContent = "Посмотреть";
    }
    return;
  }
  const reason =
    action === "approve" ? null : window.prompt("Причина действия")?.slice(0, 200);
  if (action !== "approve" && !reason) {
    return;
  }
  await fetchJson(
    `/api/v1/custom-colors/admin/${encodeURIComponent(colorId)}/${action}`,
    {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({reason}),
    },
  );
  await Promise.all([fetchAdminQueue(), fetchCatalog()]);
});

elements.loadMore.addEventListener("click", () => fetchCatalog(true));
elements.retry.addEventListener("click", fetchPalette);
for (const button of document.querySelectorAll("[data-open-chat]")) {
  button.addEventListener("click", openChat);
}
elements.closeMiniApp.addEventListener("click", () => telegram?.close());

if (telegram) {
  telegram.setHeaderColor?.("#07080D");
  telegram.setBottomBarColor?.("#171C29");
  telegram.ready();
  telegram.expand();
  telegram.enableVerticalSwipes?.();
  void bootstrap();
} else {
  state = authenticationFailed(state);
  render();
}

window.addEventListener("pagehide", revokePreview, {once: true});
