// Run: node --test ui/tests/dom.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { bidiText } from "../js/dom.js";

test("C# keeps its order inside Hebrew text, and the fix is invisible and idempotent", () => {
  const once = bidiText("זוהה מקצוע חדש: 'C#'");
  assert.equal(once, "זוהה מקצוע חדש: 'C#‎'");
  assert.equal(bidiText(once), once);
  assert.equal(bidiText("# כותרת"), "# כותרת");
});
