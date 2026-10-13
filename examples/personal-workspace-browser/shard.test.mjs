import assert from "node:assert/strict";
import {test} from "node:test";
import {selectScenarioShard} from "./shard.mjs";
test("shards cover each current and newly appended scenario exactly once", () => {
  for (const length of [49, 50, 51]) {
    const catalog = Array.from({length}, (_, id) => ({id}));
    assert.equal(selectScenarioShard(catalog), catalog);
    const shards = [1,2,3].map(index => selectScenarioShard(catalog, `${index}/3`));
    const ids = shards.flat().map(row => row.id);
    assert.equal(new Set(ids).size, length);
    assert.deepEqual(ids.sort((a,b) => a-b), catalog.map(row => row.id));
  }
});
test("invalid partitions fail before running a partial acceptance", () => {
  for (const shard of ["", "0/3", "4/3", "1/0", "1/4", "1/NaN", "1/2/3"]) {
    assert.throws(() => selectScenarioShard([1,2,3], shard));
  }
});
