// Run: node --test ui/tests/identity.test.mjs
// Decision 25.11.2026: subject identity is computed in the UI, deterministically, from the stable subject id.
import test from "node:test";
import assert from "node:assert/strict";
import { assignIdentities, identityFor, knownSlot, PALETTE, SHAPES } from "../js/identity.js";

const keyOf = (map) => Object.fromEntries([...map].map(([id, v]) => [id, `${v.key}/${v.shape}`]));

test("the same subjects always get the same identity (also after a reload)", () => {
  const list = [{ id: "subj_1", name: "Python" }, { id: "subj_12", name: "C#" }, { id: "subj_20", name: "אלגברה לינארית" }];
  assert.deepEqual(keyOf(assignIdentities(list)), keyOf(assignIdentities([...list].reverse())));
});

test("subjects from the approved initial mapping get their color and shape", () => {
  const m = assignIdentities([{ id: "subj_1", name: "Python" }, { id: "subj_2", name: "C#" }, { id: "subj_3", name: "בינה מלאכותית" }]);
  assert.equal(m.get("subj_1").shape, "hexagon");
  assert.equal(m.get("subj_2").shape, "diamond");
  assert.equal(m.get("subj_3").shape, "star");
  assert.equal(knownSlot("  python "), 0);
  assert.equal(knownSlot("Rust"), null);
});

test("a new subject never changes the identity of existing subjects", () => {
  const before = [{ id: "subj_1", name: "Python" }, { id: "subj_5", name: "Rust" }, { id: "subj_9", name: "Go" }];
  const after = [...before, { id: "subj_30", name: "Kotlin" }];
  const a = keyOf(assignIdentities(before)), b = keyOf(assignIdentities(after));
  for (const id of Object.keys(a)) assert.equal(b[id], a[id]);
});

test("no two subjects share a color while the palette has free slots", () => {
  const list = Array.from({ length: PALETTE.length }, (_, i) => ({ id: `subj_${i + 1}`, name: `נושא ${i}` }));
  const colors = [...assignIdentities(list).values()].map((v) => v.key);
  assert.equal(new Set(colors).size, PALETTE.length);
});

test("two subjects with the same known name do not collide", () => {
  const m = assignIdentities([{ id: "subj_1", name: "Python" }, { id: "subj_2", name: "python" }]);
  assert.notEqual(m.get("subj_1").key, m.get("subj_2").key);
});

test("beyond the palette the shape still varies, and every shape exists", () => {
  const list = Array.from({ length: 40 }, (_, i) => ({ id: `subj_${i + 1}`, name: `x${i}` }));
  for (const v of assignIdentities(list).values()) assert.ok(SHAPES[v.shape], v.shape);
  assert.ok(identityFor("subj_99", "Haskell").base.startsWith("#"));
});
