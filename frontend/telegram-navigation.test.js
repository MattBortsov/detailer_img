import assert from "node:assert/strict";
import test from "node:test";

import {openTelegramUrl} from "./telegram-navigation.js";

test("billing navigation opens the bot and closes the Mini App", () => {
  const calls = [];
  const telegram = {
    openTelegramLink(url) {
      calls.push(["open", url]);
    },
    close() {
      calls.push(["close"]);
    },
  };

  const opened = openTelegramUrl("https://t.me/detailer_img_bot?start=billing", {
    telegram,
    location: {assign() {}},
    closeMiniApp: true,
  });

  assert.equal(opened, true);
  assert.deepEqual(calls, [
    ["open", "https://t.me/detailer_img_bot?start=billing"],
    ["close"],
  ]);
});

test("ordinary chat navigation keeps the Mini App open", () => {
  let closed = false;
  const telegram = {
    openTelegramLink() {},
    close() {
      closed = true;
    },
  };

  openTelegramUrl("https://t.me/detailer_img_bot?start=open_app", {
    telegram,
    location: {assign() {}},
  });

  assert.equal(closed, false);
});
