"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../plugins/chart/dashboard.js"), "utf8");
const context = {
  window: { addEventListener() {} },
  document: { addEventListener() {}, getElementById() { return null; } },
  console, performance, setTimeout, clearTimeout
};
vm.createContext(context);
vm.runInContext(source.replace("window.omniDashboard = api;",
  "window.omniDashboard = api; window.probe = {state: state, drawnBuffer: drawnBuffer};"), context);
const api = context.window.omniDashboard;
const probe = context.window.probe;

api.setPoints({ limit: 0, visible: 300 });
api.setValues({ history: { A: [[0, 0], [1, 1]] }, values: { A: 1 } });
probe.drawnBuffer("A", probe.state.series.A);
api.setValues({ append: true, history: { A: [[2, 2]] }, values: { A: 2 } });
probe.drawnBuffer("A", probe.state.series.A);
assert.equal(probe.state.series.A.length, 3, "drawing must never append to source history");
assert.equal(probe.state.series.A.at(-1)[1], 2);
console.log("PASS R-321 drawing buffers do not alias retained history");

for (let index = 3; index < 6200; index += 1) {
  api.setValues({ append: true, history: { A: [[index, index]] }, values: { A: index } });
  const first = probe.drawnBuffer("A", probe.state.series.A);
  const second = probe.drawnBuffer("A", probe.state.series.A);
  assert.equal(first.length, Math.min(index + 1, 300));
  assert.equal(second.at(-1)[1], index, "shared-channel cards must see the latest sample");
}
assert.equal(probe.state.series.A.length, 5000, "zero setting must retain 5000, not unlimited history");
console.log("PASS R-321 bounded history and shared curves keep advancing past capacity");

api.setPoints({ limit: 150, visible: 100 });
assert.equal(probe.state.series.A.length, 150);
assert.equal(probe.drawnBuffer("A", probe.state.series.A).length, 100);
assert.equal(probe.drawnBuffer("A", probe.state.series.A).at(-1)[1], 6199);
console.log("PASS R-321 changing retention and drawing limits preserves the newest sample");
