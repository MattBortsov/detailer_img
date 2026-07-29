import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const html = await readFile("frontend/index.html", "utf8");
const css = await readFile("frontend/app.css", "utf8");
const app = await readFile("frontend/app.js", "utf8");
const dependencies = await readFile("pyproject.toml", "utf8");

test("accepted sheet has exact copy and exactly two authoritative actions", () => {
  const accepted = html.match(
    /<section(?=[^>]*id="accepted-state")[\s\S]*?<\/section>/,
  )?.[0];

  assert.ok(accepted);
  assert.match(accepted, />Запрос принят</);
  assert.match(accepted, />Результат придёт в чат с ботом\.</);
  assert.match(accepted, />\s*Открыть чат\s*</);
  assert.match(accepted, /aria-label="Закрыть Mini App"/);
  assert.equal((accepted.match(/<button/g) ?? []).length, 2);
});

test("only schema-valid HTTP 202 enters accepted and close only closes Mini App", () => {
  assert.match(app, /fetchJson\("\/api\/v1\/jobs"/);
  assert.match(app, /response\.status === 202/);
  assert.match(app, /accepted \? "accepted" : "failed"/);
  assert.match(
    app,
    /elements\.closeMiniApp\.addEventListener\("click", \(\) => telegram\?\.close\(\)\)/,
  );
  assert.doesNotMatch(app, /\/cancel|AbortController|setInterval/);
});

test("accepted layout follows compact accessible responsive contract", () => {
  assert.match(css, /width: min\(100%, 420px\)/);
  assert.match(css, /border-radius: 20px/);
  assert.match(css, /\.accepted-close[\s\S]*width: 44px;[\s\S]*height: 44px/);
  assert.match(css, /\.accepted-sheet h1[\s\S]*font-size: 24px/);
  assert.match(css, /@media \(max-width: 349px\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test("phase adds no frontend or task framework", () => {
  for (const forbidden of [
    "react",
    "vue",
    "svelte",
    "tailwind",
    "celery",
    "dramatiq",
    "rq==",
  ]) {
    assert.doesNotMatch(dependencies.toLowerCase(), new RegExp(forbidden));
  }
});
