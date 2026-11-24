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
  "window.omniDashboard = api; window.probe = {state: state, drawnBuffer: drawnBuffer, lineOption: lineOption};"), context);
const api = context.window.omniDashboard;
const probe = context.window.probe;

const echarts = require("../plugins/chart/vendor/echarts.min.js");
const streaming = echarts.init(null, null, { renderer: "svg", ssr: true, width: 920, height: 250 });
const snapshot = echarts.init(null, null, { renderer: "svg", ssr: true, width: 920, height: 250 });
api.setPoints({ limit: 5000, visible: 5000 });
const wave = index => [index / 10, Math.sin(index / 19) + (index % 137 === 0 ? 3 : 0)];
const initial = Array.from({ length: 5000 }, (_, index) => wave(index));
api.setValues({ history: { A: initial }, values: { A: initial.at(-1)[1] } });
streaming.setOption(probe.lineOption({ channel: "A" }, probe.state.series));
for (let offset = 5000; offset < 7000; offset += 100) {
  const tail = Array.from({ length: 100 }, (_, index) => wave(offset + index));
  api.setValues({ append: true, history: { A: tail }, values: { A: tail.at(-1)[1] } });
  streaming.setOption({ series: [{ id: "A", data: probe.drawnBuffer("A", probe.state.series.A) }] });
}
snapshot.setOption(probe.lineOption({ channel: "A" }, probe.state.series));
const extent = chart => chart.getModel().getComponent("xAxis").axis.scale.getExtent();
console.log("R-336 streaming/snapshot x extents", extent(streaming), extent(snapshot));
assert.deepEqual(extent(streaming), extent(snapshot), "identical retained data must have identical unzoomed axes");
assert.deepEqual(streaming.getOption().series[0].data, snapshot.getOption().series[0].data);
const renderedRows = chart => {
  const data = chart.getModel().getSeriesByIndex(0).getData();
  return data.mapArray(["x", "y"], (time, value) => [time, value]);
};
const beforeSlide = renderedRows(streaming);
api.setValues({ append: true, history: { A: [wave(7000)] }, values: { A: wave(7000)[1] } });
streaming.setOption({ series: [{ id: "A", data: probe.drawnBuffer("A", probe.state.series.A) }] });
const afterSlide = renderedRows(streaming);
const drawnTimes = new Set(afterSlide.map(row => row[0]));
const lostHistorical = beforeSlide.filter(row => row[0] > 210 && row[0] < 690 && !drawnTimes.has(row[0]));
console.log("R-336 rendered points before/after slide", beforeSlide.length, afterSlide.length,
  "unchanged historical points omitted", lostHistorical.length);
assert.equal(lostHistorical.length, 0, "sliding capacity must not change which unchanged historical samples are drawn");
streaming.dispose();
snapshot.dispose();
console.log("PASS R-336 streaming and newly created curves agree past capacity");

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
