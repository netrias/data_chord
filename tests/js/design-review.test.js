import assert from "node:assert/strict";
import test from "node:test";
import { scan } from "../../scripts/design_review_js.mjs";

test("JavaScript review tracks strings and explicit loops within their own functions", () => {
  // Given a function with a one-off string, two loops, and a nested callback.
  const source = [
    'function check(rows) {',
    '  for (const row of rows) { console.log("once"); }',
    '  while (ready) { run(); }',
    '  return rows.map(row => { for (const x of row) { log(x); } });',
    '}',
  ].join("\n");

  // When the review scanner parses the file.
  const signals = scan("review.js", source);

  // Then the callback loop belongs to a separate scope and the string keeps its source line.
  assert.deepEqual(signals.filter(s => s.kind === "loop" && s.scope === "<module>.check@1:1").map(s => s.line), [2, 3]);
  assert.ok(signals.some(s => s.kind === "string" && s.detail === "once" && s.line === 2));
  assert.ok(signals.some(s => s.kind === "loop" && s.scope.includes("<callback>") && s.line === 4));
});

test("a JavaScript parse failure identifies the file and line", () => {
  // Given an incomplete function declaration.
  const source = "function broken( {";

  // When the scanner reads the malformed file.
  const parse = () => scan("broken.js", source);

  // Then the scanner identifies the parse error instead of publishing a partial report.
  assert.throws(parse, /broken\.js:1:/);
});
