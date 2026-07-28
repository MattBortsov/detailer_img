const NAMED_ACTION = "Оклеить авто в этот цвет";
const SURPRISE_ACTION = "Удивить меня";
const MODES = new Set(["colors", "users", "surprise"]);

function freezeState(value) {
  return Object.freeze({
    ...value,
    colors: Object.freeze([...(value.colors ?? [])]),
    customColors: Object.freeze([...(value.customColors ?? [])]),
    ownerColors: Object.freeze([...(value.ownerColors ?? [])]),
    adminQueue: Object.freeze([...(value.adminQueue ?? [])]),
  });
}

export function createAppState() {
  return freezeState({
    view: "booting",
    mode: "colors",
    colors: [],
    surprise: null,
    customColors: [],
    ownerColors: [],
    adminQueue: [],
    catalogCursor: null,
    catalogLoading: false,
    selectedId: null,
    flippedId: null,
    submissionUuid: null,
    inFlight: false,
    actionEnabled: false,
    actionLabel: NAMED_ACTION,
    announcement: "",
    botChatUrl: null,
    privacyText: "",
    isAdmin: false,
    uploadState: "idle",
    uploadProgress: null,
    uploadMessage: "",
  });
}

export function loadPalette(
  state,
  { choices, sourceReady, botChatUrl, privacyText = "", isAdmin = false },
) {
  if (!sourceReady) {
    return freezeState({
      ...createAppState(),
      view: "no_active_source",
      botChatUrl,
      privacyText,
    });
  }
  return freezeState({
    ...state,
    view: "ready_unselected",
    colors: choices.filter((choice) => choice.kind === "color"),
    surprise: choices.find((choice) => choice.kind === "surprise") ?? null,
    selectedId: null,
    flippedId: null,
    submissionUuid: null,
    inFlight: false,
    actionEnabled: false,
    actionLabel: NAMED_ACTION,
    announcement: "",
    botChatUrl,
    privacyText,
    isAdmin,
  });
}

export function activateMode(state, mode) {
  if (!MODES.has(mode) || state.inFlight) {
    return state;
  }
  return freezeState({...state, mode, flippedId: null});
}

export function setFlipped(state, colorId) {
  if (state.inFlight) {
    return state;
  }
  return freezeState({
    ...state,
    flippedId: state.flippedId === colorId ? null : colorId,
  });
}

export function loadCustomCatalog(
  state,
  {items, nextCursor, append = false},
) {
  const combined = append ? [...state.customColors, ...items] : [...items];
  const unique = [];
  const seen = new Set();
  for (const item of combined) {
    if (!seen.has(item.selection_id)) {
      seen.add(item.selection_id);
      unique.push(item);
    }
  }
  return freezeState({
    ...state,
    customColors: unique,
    catalogCursor: nextCursor,
    catalogLoading: false,
  });
}

export function setCatalogLoading(state, loading) {
  return freezeState({...state, catalogLoading: loading});
}

export function loadOwnerColors(state, items) {
  return freezeState({...state, ownerColors: items});
}

export function loadAdminQueue(state, items) {
  return freezeState({...state, adminQueue: items});
}

export function selectChoice(state, colorId) {
  if (state.inFlight) {
    return state;
  }
  const choice = [
    ...state.colors,
    ...state.customColors.map((item) => ({
      color_id: item.selection_id,
      name: item.name,
      kind: "custom",
    })),
    state.surprise,
  ].find((item) => item?.color_id === colorId);
  if (!choice) {
    return state;
  }
  const surprise = choice.kind === "surprise";
  return freezeState({
    ...state,
    view: surprise ? "ready_surprise" : "ready_color",
    selectedId: choice.color_id,
    flippedId: null,
    actionEnabled: true,
    actionLabel: surprise ? SURPRISE_ACTION : NAMED_ACTION,
    announcement: surprise
      ? "Выбран вариант: Surprise."
      : `Выбран цвет: ${choice.name}.`,
  });
}

export function startUpload(state) {
  return freezeState({
    ...state,
    uploadState: "uploading",
    uploadProgress: null,
    uploadMessage: "Загружаем образец…",
  });
}

export function resetUpload(state) {
  return freezeState({
    ...state,
    uploadState: "idle",
    uploadProgress: null,
    uploadMessage: "",
  });
}

export function updateUploadProgress(state, progress) {
  if (state.uploadState !== "uploading") {
    return state;
  }
  return freezeState({
    ...state,
    uploadProgress:
      Number.isFinite(progress) && progress >= 0 && progress <= 100
        ? progress
        : null,
  });
}

export function completeUpload(state, outcome) {
  const accepted = outcome === "accepted";
  return freezeState({
    ...state,
    uploadState: accepted ? "pending" : "failed",
    uploadProgress: accepted ? 100 : null,
    uploadMessage: accepted
      ? "Цвет отправлен на проверку"
      : "Не удалось обработать изображение. Выберите другое фото в поддерживаемом формате.",
  });
}

export function beginSubmission(state, uuidFactory) {
  if (state.inFlight || !state.actionEnabled || state.selectedId === null) {
    return Object.freeze({state, shouldSubmit: false});
  }
  const submissionUuid = state.submissionUuid ?? uuidFactory();
  return Object.freeze({
    state: freezeState({
      ...state,
      view: "submitting",
      submissionUuid,
      inFlight: true,
      actionEnabled: false,
      announcement: "Проверяем выбор.",
    }),
    shouldSubmit: true,
  });
}

export function completeSubmission(state, outcome) {
  if (!state.inFlight) {
    return state;
  }
  if (outcome === "stale") {
    return freezeState({
      ...state,
      view: "selection_stale",
      selectedId: null,
      submissionUuid: null,
      inFlight: false,
      actionEnabled: false,
      actionLabel: NAMED_ACTION,
      announcement: "",
    });
  }
  if (outcome === "no_source") {
    return freezeState({
      ...createAppState(),
      view: "no_active_source",
      botChatUrl: state.botChatUrl,
      privacyText: state.privacyText,
    });
  }
  if (outcome === "auth_failed") {
    return freezeState({
      ...createAppState(),
      view: "auth_failed",
      botChatUrl: state.botChatUrl,
    });
  }
  const isSurprise = state.selectedId === state.surprise?.color_id;
  return freezeState({
    ...state,
    view:
      outcome === "validated"
        ? isSurprise
          ? "ready_surprise"
          : "ready_color"
        : "submit_failed",
    inFlight: false,
    actionEnabled: true,
    announcement: "",
  });
}

export function authenticationFailed(state, botChatUrl = null) {
  return freezeState({
    ...createAppState(),
    view: "auth_failed",
    botChatUrl: botChatUrl ?? state.botChatUrl,
  });
}

export function paletteFailed(state) {
  return freezeState({
    ...createAppState(),
    view: "palette_failed",
    botChatUrl: state.botChatUrl,
  });
}
