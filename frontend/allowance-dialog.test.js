import assert from "node:assert/strict";
import test from "node:test";

import {syncAllowanceDialog} from "./allowance-dialog.js";

test("allowance state opens a modal dialog and focuses its action", () => {
  const calls = [];
  const dialog = {
    open: false,
    showModal() {
      calls.push("show-modal");
      this.open = true;
    },
  };
  const message = {textContent: ""};
  const action = {focus() { calls.push("focus-action"); }};

  const changed = syncAllowanceDialog(
    {dialog, message, action},
    {visible: true, text: "Генерации закончились"},
  );

  assert.equal(changed, true);
  assert.equal(message.textContent, "Генерации закончились");
  assert.deepEqual(calls, ["show-modal", "focus-action"]);
});

test("leaving allowance state closes an open modal", () => {
  const calls = [];
  const dialog = {
    open: true,
    close() {
      calls.push("close");
      this.open = false;
    },
  };

  const changed = syncAllowanceDialog(
    {dialog, message: {textContent: ""}, action: {}},
    {visible: false, text: ""},
  );

  assert.equal(changed, true);
  assert.deepEqual(calls, ["close"]);
});
