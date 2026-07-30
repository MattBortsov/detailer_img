import assert from "node:assert/strict";
import test from "node:test";

import {
  activateMode,
  beginSubmission,
  completeSubmission,
  completeUpload,
  createAppState,
  loadCustomCatalog,
  loadPalette,
  resetUpload,
  selectChoice,
  setFlipped,
  startUpload,
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

test("palette separates colors and Surprise into exact modes", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
    isAdmin: false,
  });

  assert.equal(loaded.mode, "colors");
  assert.deepEqual(loaded.colors.map((item) => item.color_id), ["charcoal"]);
  assert.equal(loaded.surprise.color_id, "surprise_me");
  assert.equal(loaded.isAdmin, false);
  assert.equal(loaded.selectedId, null);
});

test("mode navigation and flip do not silently select a card", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
  });
  const users = activateMode(loaded, "users");
  const flipped = setFlipped(users, "charcoal");

  assert.equal(users.mode, "users");
  assert.equal(flipped.flippedId, "charcoal");
  assert.equal(flipped.selectedId, null);
  assert.equal(setFlipped(flipped, "charcoal").flippedId, null);
  assert.equal(activateMode(loaded, "unknown"), loaded);
});

test("community pagination preserves server order and deduplicates", () => {
  const first = loadCustomCatalog(createAppState(), {
    items: [
      { selection_id: "custom:a:v1", name: "Newest", preview_url: "/one" },
      { selection_id: "custom:b:v1", name: "Older", preview_url: "/two" },
    ],
    nextCursor: "next",
  });
  const second = loadCustomCatalog(first, {
    items: [
      { selection_id: "custom:b:v1", name: "Older", preview_url: "/two" },
      { selection_id: "custom:c:v1", name: "Oldest", preview_url: "/three" },
    ],
    nextCursor: null,
    append: true,
  });

  assert.deepEqual(
    second.customColors.map((item) => item.name),
    ["Newest", "Older", "Oldest"],
  );
  assert.equal(second.catalogCursor, null);
});

test("named and Surprise selections enable submission without global CTA copy", () => {
  const loaded = loadPalette(createAppState(), {
    choices,
    sourceReady: true,
    botChatUrl: "https://t.me/CarWrapBot",
  });
  const named = selectChoice(loaded, "charcoal");
  const surprise = selectChoice(activateMode(named, "surprise"), "surprise_me");

  assert.equal(named.selectedId, "charcoal");
  assert.equal(named.actionEnabled, true);
  assert.equal(surprise.selectedId, "surprise_me");
  assert.equal(surprise.actionEnabled, true);
  assert.equal("actionLabel" in named, false);
});

test("upload states expose progress without moderation ETA", () => {
  const uploading = startUpload(createAppState());
  const pending = completeUpload(uploading, "accepted");
  const failed = completeUpload(uploading, "failed");
  const reset = resetUpload(failed);

  assert.equal(uploading.uploadState, "uploading");
  assert.equal(uploading.uploadProgress, null);
  assert.equal(pending.uploadState, "pending");
  assert.equal(pending.uploadMessage, "Цвет отправлен на проверку");
  assert.equal(failed.uploadState, "failed");
  assert.equal(reset.uploadState, "idle");
  assert.equal(reset.uploadMessage, "");
  assert.equal(Object.isFrozen(reset), true);
  assert.equal(Object.isFrozen(reset.customColors), true);
});

test("submission keeps one UUID and stale selections reset safely", () => {
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
  const stale = completeSubmission(first.state, "stale");

  assert.equal(first.shouldSubmit, true);
  assert.equal(repeated.shouldSubmit, false);
  assert.equal(retry.state.submissionUuid, "uuid-one");
  assert.equal(stale.selectedId, null);
  assert.equal(stale.actionEnabled, false);
});

test("durable acceptance is terminal and keeps the server chat URL", () => {
  const selected = selectChoice(
    loadPalette(createAppState(), {
      choices,
      sourceReady: true,
      botChatUrl: "https://t.me/CarWrapBot",
    }),
    "charcoal",
  );
  const pending = beginSubmission(selected, () => "uuid-one").state;
  const accepted = completeSubmission(
    pending,
    "accepted",
    "https://t.me/ServerOwnedBot",
  );

  assert.equal(accepted.view, "accepted");
  assert.equal(accepted.actionEnabled, false);
  assert.equal(accepted.inFlight, false);
  assert.equal(accepted.submissionUuid, "uuid-one");
  assert.equal(accepted.botChatUrl, "https://t.me/ServerOwnedBot");
  assert.equal(
    accepted.announcement,
    "Запрос принят. Результат придёт в чат с ботом.",
  );
});

test("active and recent limits have stable truthful copy and remain retryable", () => {
  const selected = selectChoice(
    loadPalette(createAppState(), {
      choices,
      sourceReady: true,
      botChatUrl: "https://t.me/CarWrapBot",
    }),
    "charcoal",
  );
  const pending = beginSubmission(selected, () => "uuid-one").state;
  const active = completeSubmission(pending, "active_limit");
  const recent = completeSubmission(pending, "recent_limit");

  assert.equal(active.view, "submission_limited");
  assert.equal(active.actionEnabled, true);
  assert.equal(
    active.submissionError,
    "Дождитесь результата текущего запроса и попробуйте снова.",
  );
  assert.equal(
    recent.submissionError,
    "Слишком много запросов за короткое время. Попробуйте позже.",
  );
});
