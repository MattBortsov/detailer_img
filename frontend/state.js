const NAMED_ACTION = "Оклеить авто в этот цвет";
const SURPRISE_ACTION = "Удивить меня";

function freezeState(value) {
  return Object.freeze({
    ...value,
    choices: Object.freeze([...value.choices]),
  });
}

export function createAppState() {
  return freezeState({
    view: "booting",
    choices: [],
    selectedId: null,
    submissionUuid: null,
    inFlight: false,
    actionEnabled: false,
    actionLabel: NAMED_ACTION,
    announcement: "",
    botChatUrl: null,
    privacyText: "",
  });
}

export function loadPalette(
  state,
  { choices, sourceReady, botChatUrl, privacyText = "" },
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
    choices,
    selectedId: null,
    submissionUuid: null,
    inFlight: false,
    actionEnabled: false,
    actionLabel: NAMED_ACTION,
    announcement: "",
    botChatUrl,
    privacyText,
  });
}

export function selectChoice(state, colorId) {
  if (state.inFlight) {
    return state;
  }
  const choice = state.choices.find((item) => item.color_id === colorId);
  if (!choice) {
    return state;
  }
  const surprise = choice.kind === "surprise";
  return freezeState({
    ...state,
    view: surprise ? "ready_surprise" : "ready_color",
    selectedId: choice.color_id,
    actionEnabled: true,
    actionLabel: surprise ? SURPRISE_ACTION : NAMED_ACTION,
    announcement: surprise
      ? "Выбран вариант: Удиви меня."
      : `Выбран цвет: ${choice.name}.`,
  });
}

export function beginSubmission(state, uuidFactory) {
  if (state.inFlight || !state.actionEnabled || state.selectedId === null) {
    return Object.freeze({ state, shouldSubmit: false });
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
  const choice = state.choices.find(
    (item) => item.color_id === state.selectedId,
  );
  const isSurprise = choice?.kind === "surprise";
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
