import assert from "node:assert/strict";
import test from "node:test";

import {
  beginSubmission,
  completeSubmission,
  createAppState,
  loadPalette,
  selectChoice,
} from "../../frontend/state.js";

const choices = Object.freeze([
  Object.freeze({
    color_id: "charcoal",
    name: "Графитовый",
    display_hex: "#343A40",
    kind: "color",
  }),
  Object.freeze({
    color_id: "surprise_me",
    name: "Удиви меня",
    display_hex: null,
    kind: "surprise",
  }),
]);

test("starts booting with no protected palette or selection", () => {
  const state = createAppState();
  assert.equal(state.view, "booting");
  assert.deepEqual(state.choices, []);
  assert.equal(state.selectedId, null);
  assert.equal(state.submissionUuid, null);
  assert.equal(state.inFlight, false);
});

test("loads authenticated palette with no default selection", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
  });

  assert.equal(loaded.view, "ready_unselected");
  assert.equal(loaded.selectedId, null);
  assert.equal(loaded.actionLabel, "Оклеить авто в этот цвет");
  assert.equal(loaded.actionEnabled, false);
});

test("named and surprise selection are exclusive and change exact CTA", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
  });
  const named = selectChoice(loaded, "charcoal");
  const surprise = selectChoice(named, "surprise_me");

  assert.equal(named.view, "ready_color");
  assert.equal(named.selectedId, "charcoal");
  assert.equal(named.actionLabel, "Оклеить авто в этот цвет");
  assert.equal(named.announcement, "Выбран цвет: Графитовый.");
  assert.equal(surprise.view, "ready_surprise");
  assert.equal(surprise.selectedId, "surprise_me");
  assert.equal(surprise.actionLabel, "Удивить меня");
  assert.equal(surprise.announcement, "Выбран вариант: Удиви меня.");
});

test("unknown choice cannot create a browser-side fallback", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
  });
  assert.equal(selectChoice(loaded, "unknown"), loaded);
});

test("first submit synchronously guards repeats and reuses retry UUID", () => {
  const selected = selectChoice(
    loadPalette(createAppState(), {
      choices,
      sourceReady: true,
      botChatUrl: "https://t.me/CarWrapBot",
    }),
    "charcoal",
  );
  const first = beginSubmission(selected, () => "uuid-one");
  const repeated = beginSubmission(first.state, () => "uuid-two");
  const failed = completeSubmission(first.state, "failed");
  const retry = beginSubmission(failed, () => "uuid-three");

  assert.equal(first.shouldSubmit, true);
  assert.equal(first.state.view, "submitting");
  assert.equal(first.state.inFlight, true);
  assert.equal(first.state.selectedId, "charcoal");
  assert.equal(first.state.submissionUuid, "uuid-one");
  assert.equal(repeated.shouldSubmit, false);
  assert.equal(repeated.state, first.state);
  assert.equal(failed.view, "submit_failed");
  assert.equal(failed.selectedId, "charcoal");
  assert.equal(retry.state.submissionUuid, "uuid-one");
});

test("successful validation restores selected state without acceptance state", () => {
  const selected = selectChoice(
    loadPalette(createAppState(), {
      choices,
      sourceReady: true,
      botChatUrl: "https://t.me/CarWrapBot",
    }),
    "charcoal",
  );
  const pending = beginSubmission(selected, () => "uuid-one").state;
  const complete = completeSubmission(pending, "validated");

  assert.equal(complete.view, "ready_color");
  assert.equal(complete.inFlight, false);
  assert.equal(complete.selectedId, "charcoal");
});

test("stale selection clears choice while no-source and auth recover safely", () => {
  const selected = selectChoice(
    loadPalette(createAppState(), {
      choices,
      sourceReady: true,
      botChatUrl: "https://t.me/CarWrapBot",
    }),
    "charcoal",
  );
  const pending = beginSubmission(selected, () => "uuid-one").state;
  const stale = completeSubmission(pending, "stale");
  const noSource = loadPalette(createAppState(), {
    choices,
    sourceReady: false,
    botChatUrl: "https://t.me/CarWrapBot",
  });

  assert.equal(stale.view, "selection_stale");
  assert.equal(stale.selectedId, null);
  assert.equal(stale.actionEnabled, false);
  assert.equal(noSource.view, "no_active_source");
  assert.equal(noSource.choices.length, 0);
});
