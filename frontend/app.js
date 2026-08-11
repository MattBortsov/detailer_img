/**
 * Card interaction adapted from Kokonut UI Card Flip by @dorianbaffier.
 * MIT License. Vanilla implementation: native face surfaces and select action.
 */
import {
  activateMode,
  authenticationFailed,
  beginSubmission,
  completeSubmission,
  createAppState,
  loadCustomCatalog,
  loadPalette,
  paletteFailed,
  selectChoice,
  setCatalogFilter,
  setCatalogLoading,
  setFlipped,
} from "./state.js";

const HEX_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const BOT_URL_PATTERN =
  /^https:\/\/t\.me\/[A-Za-z][A-Za-z0-9_]{4,31}[Bb][Oo][Tt]\?start=(?:open_app|billing)$/;
const SOURCE_PREVIEW_URL = "/api/v1/active-source/image";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const CUSTOM_SELECTION_PATTERN =
  /^custom:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):v[1-9]\d*$/i;
const elements = {
  loading: document.querySelector("#loading-state"),
  ready: document.querySelector("#ready-state"),
  noSource: document.querySelector("#no-source-state"),
  authFailed: document.querySelector("#auth-failed-state"),
  paletteFailed: document.querySelector("#palette-failed-state"),
  accepted: document.querySelector("#accepted-state"),
  sourcePhotoThumbnail: document.querySelector("#source-photo-thumbnail"),
  sourcePhotoDialog: document.querySelector("#source-photo-dialog"),
  sourcePhotoFull: document.querySelector("#source-photo-full"),
  openSourcePhoto: document.querySelector("#open-source-photo"),
  closeSourcePhoto: document.querySelector("#close-source-photo"),
  replaceSourcePhoto: document.querySelector("#replace-source-photo"),
  closeMiniApp: document.querySelector("#close-mini-app"),
  form: document.querySelector("#palette-form"),
  colorsGrid: document.querySelector("#colors-grid"),
  userColorsGrid: document.querySelector("#user-colors-grid"),
  userColorsEmpty: document.querySelector("#user-colors-empty"),
  choiceTemplate: document.querySelector("#choice-template"),
  announcement: document.querySelector("#selection-status"),
  alert: document.querySelector("#inline-alert"),
  retry: document.querySelector("#retry-palette"),
  loadMore: document.querySelector("#load-more-colors"),
  surprise: document.querySelector("#select-surprise"),
  confirmDialog: document.querySelector("#confirm-color-dialog"),
  confirmSelection: document.querySelector("#confirm-color-selection"),
  closeConfirm: document.querySelector("#close-confirm-color"),
  editColorDialog: document.querySelector("#edit-color-dialog"),
  editColorForm: document.querySelector("#edit-color-form"),
  editColorName: document.querySelector("#edit-color-name"),
  editColorStructure: document.querySelector("#edit-color-structure"),
  editColorFinish: document.querySelector("#edit-color-finish"),
  editColorAlert: document.querySelector("#edit-color-alert"),
  closeEditColor: document.querySelector("#close-edit-color"),
  openAdd: document.querySelector("#open-add-color"),
  catalogFilters: document.querySelector("#catalog-filters"),
};

let state = createAppState();
let sessionExchangeAttempted = false;
let catalogLoaded = false;
let pendingColorId = null;
let editingColorId = null;
let replacementInFlight = false;
let customColorPromptInFlight = false;
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
      "source_preview_url",
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
        payload.source_message_id <= 0 ||
        payload.source_preview_url !== SOURCE_PREVIEW_URL)) ||
    (!payload.source_ready &&
      (payload.source_message_id !== null ||
        payload.source_preview_url !== null))
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
      colorStructure: null,
      finish: null,
    };
  }
  const structures = {
    solid: "Однотонная",
    multicolor: "Многоцветная",
    unspecified: "Без категории",
  };
  const finishes = {
    matte: "Матовая",
    satin: "Сатин",
    gloss: "Глянцевая",
    unspecified: "Поверхность не указана",
  };
  return {
    id: item.selection_id,
    name: item.name,
    kindLabel: [structures[item.color_structure], finishes[item.finish]].join(
      " · ",
    ),
    displayHex: null,
    previewUrl: item.preview_url,
    colorStructure: item.color_structure,
    finish: item.finish,
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
  const swatches = fragment.querySelectorAll(".swatch-field");
  const images = fragment.querySelectorAll(".reference-image");
  const frontSurface = cardButton(
    fragment,
    '.card-flip-surface[data-face="front"]',
  );
  const backSurface = cardButton(
    fragment,
    '.card-flip-surface[data-face="back"]',
  );
  const select = cardButton(fragment, ".select-button");
  const adminActions = fragment.querySelector(".admin-card-actions");
  const adminEdit = cardButton(fragment, ".admin-card-edit");
  const adminDelete = cardButton(fragment, ".admin-card-delete");
  const flipped = state.flippedId === item.id;
  const selected = state.selectedId === item.id;

  card.dataset.colorId = item.id;
  card.dataset.kind = item.kindLabel ? "user" : "builtin";
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
  const customMatch = CUSTOM_SELECTION_PATTERN.exec(item.id);
  if (state.isAdmin && customMatch && adminActions instanceof HTMLElement) {
    adminActions.hidden = false;
    adminEdit.dataset.action = "admin-edit";
    adminEdit.dataset.colorId = customMatch[1];
    adminEdit.dataset.colorName = item.name;
    adminEdit.dataset.colorStructure = item.colorStructure;
    adminEdit.dataset.colorFinish = item.finish;
    adminEdit.ariaLabel = `Редактировать цвет ${item.name}`;
    adminDelete.dataset.action = "admin-delete";
    adminDelete.dataset.colorId = customMatch[1];
    adminDelete.ariaLabel = `Удалить цвет ${item.name}`;
  }

  for (const name of fragment.querySelectorAll(".card-name")) {
    name.textContent = item.name;
  }
  for (const kind of fragment.querySelectorAll(".card-kind")) {
    kind.textContent = item.kindLabel;
    kind.hidden = item.kindLabel.length === 0;
  }
  if (item.previewUrl) {
    for (const image of images) {
      image.src = item.previewUrl;
      image.alt = `Образец цвета ${item.name}`;
      image.hidden = false;
    }
    for (const swatch of swatches) {
      swatch.hidden = true;
    }
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
  elements.userColorsEmpty.hidden =
    state.catalogLoading || state.customColors.length > 0;
  elements.loadMore.hidden =
    state.catalogLoading || state.catalogCursor === null;
  elements.loadMore.disabled = state.catalogLoading;
}

function renderCatalogFilters() {
  for (const button of elements.catalogFilters.querySelectorAll("button")) {
    const selected =
      button.dataset.filterAxis === "structure"
        ? state.catalogStructure === button.dataset.filterValue
        : state.catalogFinish === button.dataset.filterValue;
    button.ariaPressed = String(selected);
  }
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
  if (
    elements.sourcePhotoThumbnail.getAttribute("src") !==
    state.sourcePreviewUrl
  ) {
    elements.sourcePhotoThumbnail.src = state.sourcePreviewUrl;
  }
  renderMode();
  renderCatalogFilters();
  renderCards();
  elements.form.ariaBusy = String(state.inFlight);
  elements.announcement.textContent = state.announcement;
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
  openTelegramUrl(url);
}

function openTelegramUrl(url) {
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
  let launchEvidence =
    typeof telegram.initData === "string" ? telegram.initData : "";
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
      sourcePreviewUrl: payload.source_preview_url,
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

elements.openSourcePhoto.addEventListener("click", () => {
  if (state.sourcePreviewUrl !== SOURCE_PREVIEW_URL) {
    return;
  }
  elements.sourcePhotoFull.src = state.sourcePreviewUrl;
  elements.sourcePhotoDialog.showModal();
  elements.closeSourcePhoto.focus();
});

function closeSourcePhoto() {
  elements.sourcePhotoDialog.close();
  elements.sourcePhotoFull.removeAttribute("src");
  elements.openSourcePhoto.focus({preventScroll: true});
}

elements.closeSourcePhoto.addEventListener("click", closeSourcePhoto);
elements.sourcePhotoDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeSourcePhoto();
});

elements.replaceSourcePhoto.addEventListener("click", async () => {
  if (replacementInFlight) {
    return;
  }
  replacementInFlight = true;
  elements.replaceSourcePhoto.disabled = true;
  try {
    const response = await fetchJson("/api/v1/active-source/replacement", {
      method: "POST",
    });
    const payload = await response.json();
    if (
      !response.ok ||
      !exactKeys(payload, ["status", "bot_chat_url"]) ||
      payload.status !== "prompt_sent"
    ) {
      throw new Error("Replacement prompt failed");
    }
    const url = trustedBotUrl(payload.bot_chat_url);
    if (!url) {
      throw new Error("Invalid bot URL");
    }
    openTelegramUrl(url);
  } catch {
    if (state.view !== "auth_failed") {
      elements.alert.textContent =
        "Не удалось запросить новое фото. Попробуйте ещё раз.";
      elements.alert.hidden = false;
    }
  } finally {
    replacementInFlight = false;
    elements.replaceSourcePhoto.disabled = false;
  }
});

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
          "color_structure",
          "finish",
          "approved_at",
        ]) &&
        typeof item.selection_id === "string" &&
        typeof item.name === "string" &&
        Number.isInteger(item.version) &&
        item.version > 0 &&
        typeof item.preview_url === "string" &&
        item.preview_url.startsWith("/api/v1/custom-colors/") &&
        ["unspecified", "solid", "multicolor"].includes(
          item.color_structure,
        ) &&
        ["unspecified", "matte", "satin", "gloss"].includes(item.finish) &&
        typeof item.approved_at === "string",
    ) &&
    (payload.next_cursor === null || typeof payload.next_cursor === "string")
  );
}

async function fetchCatalog(append = false) {
  state = setCatalogLoading(state, true);
  render();
  const query = new URLSearchParams();
  if (append && state.catalogCursor) {
    query.set("cursor", state.catalogCursor);
  }
  if (state.catalogStructure !== "all") {
    query.set("structure", state.catalogStructure);
  }
  if (state.catalogFinish !== "all") {
    query.set("finish", state.catalogFinish);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
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

function findPendingChoice(colorId) {
  const paletteChoice = state.colors.find(
    (choice) => choice.color_id === colorId,
  );
  if (paletteChoice) {
    return paletteChoice;
  }
  const customChoice = state.customColors.find(
    (choice) => choice.selection_id === colorId,
  );
  if (customChoice) {
    return customChoice;
  }
  return state.surprise?.color_id === colorId ? state.surprise : null;
}

function openConfirmDialog(colorId) {
  const choice = findPendingChoice(colorId);
  if (!choice || elements.confirmDialog.open) {
    return;
  }
  pendingColorId = colorId;
  elements.confirmDialog.showModal();
  elements.confirmSelection.focus();
}

function closeConfirmDialog() {
  const colorId = pendingColorId;
  pendingColorId = null;
  if (elements.confirmDialog.open) {
    elements.confirmDialog.close();
  }
  findCardButton(colorId, ".select-button")?.focus({preventScroll: true});
}

function syncCardFlip(colorId) {
  const card = [...document.querySelectorAll(".palette-card")].find(
    (candidate) => candidate.dataset.colorId === colorId,
  );
  if (!card) {
    return;
  }
  const flipped = state.flippedId === colorId;
  const front = card.querySelector(".card-front");
  const back = card.querySelector(".card-back");
  card.dataset.flipped = String(flipped);
  front.ariaHidden = String(flipped);
  back.ariaHidden = String(!flipped);
  front.inert = flipped;
  back.inert = !flipped;
  for (const surface of card.querySelectorAll(".card-flip-surface")) {
    surface.ariaExpanded = String(flipped);
  }
}

async function mutateAdminColor(colorId, action, payload) {
  try {
    const response = await fetchJson(
      `/api/v1/custom-colors/admin/${encodeURIComponent(colorId)}/${action}`,
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      },
    );
    if (!response.ok) {
      throw new Error("Admin color mutation failed");
    }
    await fetchCatalog();
    return true;
  } catch {
    if (action === "edit" && elements.editColorDialog.open) {
      elements.editColorAlert.textContent =
        "Не удалось сохранить изменения. Попробуйте ещё раз.";
      elements.editColorAlert.hidden = false;
    }
    if (state.view !== "auth_failed") {
      elements.alert.textContent = "Не удалось изменить цвет. Попробуйте ещё раз.";
      elements.alert.hidden = false;
    }
    return false;
  }
}

function editColorValuesMatch() {
  const structure = elements.editColorStructure.value;
  const finish = elements.editColorFinish.value;
  return (
    (structure === "unspecified" && finish === "unspecified") ||
    (["solid", "multicolor"].includes(structure) &&
      ["matte", "satin", "gloss"].includes(finish))
  );
}

function openAdminEdit(button) {
  if (!state.isAdmin || !(elements.editColorDialog instanceof HTMLDialogElement)) {
    return;
  }
  const colorId = button.dataset.colorId;
  const name = button.dataset.colorName;
  const structure = button.dataset.colorStructure;
  const finish = button.dataset.colorFinish;
  if (!colorId || !name || !structure || !finish) {
    return;
  }
  editingColorId = colorId;
  elements.editColorName.value = name;
  elements.editColorStructure.value = structure;
  elements.editColorFinish.value = finish;
  elements.editColorAlert.hidden = true;
  elements.editColorDialog.showModal();
  elements.editColorName.focus({preventScroll: true});
}

async function handleCardAction(event) {
  const button = event.target.closest("[data-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const colorId = button.dataset.colorId;
  if (!colorId) {
    return;
  }
  if (button.dataset.action === "admin-edit") {
    openAdminEdit(button);
    return;
  }
  if (button.dataset.action === "admin-delete") {
    if (
      !state.isAdmin ||
      !window.confirm(
        "Удалить цвет? Он исчезнет из каталога и станет недоступен для новых генераций.",
      )
    ) {
      return;
    }
    await mutateAdminColor(colorId, "delete", {
      reason: "admin_deleted_from_catalog",
    });
    return;
  }
  if (button.dataset.action === "select") {
    openConfirmDialog(colorId);
    return;
  }
  if (button.dataset.action === "flip") {
    const nextFace = button.dataset.face === "front" ? "back" : "front";
    state = setFlipped(state, colorId);
    syncCardFlip(colorId);
    window.requestAnimationFrame(() => {
      findCardSurface(colorId, nextFace)?.focus({preventScroll: true});
    });
  }
}

elements.closeEditColor.addEventListener("click", () => {
  elements.editColorDialog.close();
});

elements.editColorDialog.addEventListener("close", () => {
  editingColorId = null;
  elements.editColorAlert.hidden = true;
});

elements.editColorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.isAdmin || !editingColorId) {
    return;
  }
  const name = elements.editColorName.value.trim();
  if (!name || !editColorValuesMatch()) {
    elements.editColorAlert.textContent =
      "Для категории выберите подходящую поверхность или не указывайте оба поля.";
    elements.editColorAlert.hidden = false;
    return;
  }
  const saved = await mutateAdminColor(editingColorId, "edit", {
    name,
    color_structure: elements.editColorStructure.value,
    finish: elements.editColorFinish.value,
    reason: "admin_edited_from_catalog",
  });
  if (saved) {
    elements.editColorDialog.close();
  }
});

for (const grid of [elements.colorsGrid, elements.userColorsGrid]) {
  grid.addEventListener("click", handleCardAction);
}

elements.confirmSelection.addEventListener("click", async () => {
  const colorId = pendingColorId;
  if (!colorId || !findPendingChoice(colorId)) {
    closeConfirmDialog();
    return;
  }
  state = selectChoice(state, colorId);
  telegram?.HapticFeedback?.selectionChanged();
  elements.confirmDialog.close();
  pendingColorId = null;
  await submitSelectedChoice();
  if (state.view !== "accepted") {
    findCardButton(colorId, ".select-button")?.focus({preventScroll: true});
  }
});
elements.closeConfirm.addEventListener("click", closeConfirmDialog);
elements.confirmDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeConfirmDialog();
});

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", async () => {
    state = activateMode(state, tab.dataset.mode);
    render();
    if (state.mode === "users" && !catalogLoaded) {
      await fetchCatalog();
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
    openConfirmDialog(state.surprise.color_id);
  }
});

async function submitSelectedChoice() {
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
    } else if (response.status === 402) {
      const billingUrl = trustedBotUrl(
        response.headers.get("X-Billing-Chat-Url"),
      );
      if (billingUrl) {
        openTelegramUrl(billingUrl);
      }
      state = completeSubmission(state, "failed");
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
}

async function requestCustomColorPrompt() {
  if (customColorPromptInFlight) {
    return;
  }
  customColorPromptInFlight = true;
  elements.openAdd.disabled = true;
  try {
    const response = await fetchJson("/api/v1/custom-colors/prompt", {
      method: "POST",
    });
    const payload = await response.json();
    if (
      !response.ok ||
      !exactKeys(payload, ["status", "bot_chat_url"]) ||
      payload.status !== "prompt_sent"
    ) {
      throw new Error("Custom color prompt failed");
    }
    const botUrl = trustedBotUrl(payload.bot_chat_url);
    if (!botUrl) {
      throw new Error("Invalid bot URL");
    }
    openTelegramUrl(botUrl);
  } catch {
    if (state.view !== "auth_failed") {
      elements.alert.textContent =
        "Не удалось начать добавление цвета. Попробуйте ещё раз.";
      elements.alert.hidden = false;
    }
  } finally {
    customColorPromptInFlight = false;
    elements.openAdd.disabled = false;
  }
}

elements.openAdd.addEventListener("click", requestCustomColorPrompt);
elements.catalogFilters.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-filter-axis]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const next = setCatalogFilter(
    state,
    button.dataset.filterAxis,
    button.dataset.filterValue,
  );
  if (next === state) {
    return;
  }
  state = next;
  catalogLoaded = false;
  render();
  await fetchCatalog();
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
