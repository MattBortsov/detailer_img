import assert from "node:assert/strict";
import test from "node:test";

import {
  closeTelegramMiniApp,
  returnToTelegramChat,
} from "./telegram-navigation.js";

test("returning to chat closes the Mini App without opening another link", () => {
  const calls = [];
  const telegram = {
    openTelegramLink(url) {
      calls.push(["open", url]);
    },
    disableClosingConfirmation() {
      calls.push(["disable-confirmation"]);
    },
    close() {
      calls.push(["close"]);
    },
  };

  const opened = returnToTelegramChat(
    "https://t.me/detailer_img_bot?start=billing",
    {
      telegram,
      browserWindow: {},
      location: {assign() {}},
    },
  );

  assert.equal(opened, true);
  assert.deepEqual(calls, [
    ["disable-confirmation"],
    ["close"],
  ]);
});

test("close resolves the current WebApp instance from the browser", () => {
  const calls = [];
  const staleTelegram = {
    close() {
      calls.push(["stale-close"]);
    },
  };
  const currentTelegram = {
    close() {
      calls.push(["current-close"]);
    },
  };

  const closed = closeTelegramMiniApp({
    telegram: staleTelegram,
    browserWindow: {Telegram: {WebApp: currentTelegram}},
  });

  assert.equal(closed, true);
  assert.deepEqual(calls, [["current-close"]]);
});

test("browser fallback opens the bot URL", () => {
  const assigned = [];

  const opened = returnToTelegramChat(
    "https://t.me/detailer_img_bot?start=open_app",
    {
      telegram: undefined,
      browserWindow: {},
      location: {
        assign(url) {
          assigned.push(url);
        },
      },
    },
  );

  assert.equal(opened, true);
  assert.deepEqual(assigned, [
    "https://t.me/detailer_img_bot?start=open_app",
  ]);
});
