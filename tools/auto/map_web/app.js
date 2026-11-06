/* AoE2 map replay: canvas minimap animation over collected object
 * snapshots. Data (per game-second, columnar [id, owner, unitType, x, y]):
 *   meta    {mapWidth, mapHeight, players:[{id,name}], typeNames}
 *   objects {b: buildings, v: villagers, m: military}
 *   stats   {stats: [[age,food,wood,stone,gold,pop,score?], ...]} per player
 *   names   {names: {pid: {name, colorId}}} from the replay header
 *
 * Orientation matches the in-game minimap: the world is rotated 45
 * degrees (north corner top, east right, south bottom, west left).
 * Buildings are squares (notable ones larger with an icon glyph inside),
 * units are dots, all in each player's real in-game color (mgz color_id
 * from the replay header, falling back to slot defaults). The strip above
 * the map mimics the in-game top bar: color, name, score, age.
 */
"use strict";

const tag = new URLSearchParams(location.search).get("tag") || "demo";
// DE lobby color indexes per mgz.reference: 0=Blue,1=Red,2=Green,3=Yellow,
// 4=Teal,5=Purple,6=Gray,7=Orange
const COLOR_ID_HEX = [
  "#2C5AD8", "#C3332F", "#2E7D32", "#D9B426",
  "#17A2A2", "#8A44B8", "#9E9E9E", "#E0801E",
];
// slot fallback when the replay header is unavailable (classic cycle:
// blue red yellow green cyan purple gray orange)
const SLOT_HEX = [
  null,
  COLOR_ID_HEX[0], COLOR_ID_HEX[1], COLOR_ID_HEX[3], COLOR_ID_HEX[2],
  COLOR_ID_HEX[4], COLOR_ID_HEX[5], COLOR_ID_HEX[6], COLOR_ID_HEX[7],
];
const GAIA = "#9ab09a";
const NOTABLE = [
  ["TOWN_CENTER", "\u2302"],
  ["CASTLE", "\u265c"],
  ["KREPOST", "\u265c"],
  ["DONJON", "\u265c"],
  ["FORTIFIED_CHURCH", "\u271d"],
  ["FEITORIA", "\u265c"],
];
// draw order: big buildings under, units on top (sizes in world tiles)
const LAYER_PX = 1.3, LAYER_UNIT = 0.75, LAYER_VILL = 0.5, NOTABLE_PX = 2.6;
const AGE_ROMAN = ["I", "II", "III", "IV"];

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const playBtn = document.getElementById("play");
const seekEl = document.getElementById("seek");
const speedEl = document.getElementById("speed");
const clockEl = document.getElementById("clock");
const stripEl = document.getElementById("scorebar");

let meta = null;          // latest meta row
let snaps = [];           // [{t, b, v, m}]
let statRows = [];        // [{t, stats}] (stats[i+1] = player i)
let playerInfo = {};      // pid -> {name, colorId}
let chips = [];           // {pid, scoreEl, ageEl}
let bgImg = new Image();
let bgReady = false;
let playing = false;
let t = 0;                // current game time
let lastTs = null;
// id -> row index maps for the two frames around the cursor (rebuilt on
// frame change only — 1500 lookups/frame instead of 1500 inserts/frame)
let idxA = null, idxB = null, idxI = -1;
let statsI = -1;

function fmtTime(s) {
  const m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return m + ":" + String(ss).padStart(2, "0");
}

function colorOf(pid) {
  const info = playerInfo[pid];
  if (info && Number.isInteger(info.colorId) &&
      COLOR_ID_HEX[info.colorId] !== undefined)
    return COLOR_ID_HEX[info.colorId];
  return SLOT_HEX[pid] || GAIA;
}

function idIndex(snap, layer) {
  const m = new Map();
  // an empty Lua layer arrives as {} (truthy, not an array)
  const rows = Array.isArray(snap[layer]) ? snap[layer] : [];
  for (let i = 0; i < rows.length; i++) m.set(rows[i][0], rows[i]);
  return m;
}

function interp(from, to, id, f) {
  const a = from ? from.get(id) : null;
  const b = to ? to.get(id) : null;
  if (a && b) return [a[3] + (b[3] - a[3]) * f, a[4] + (b[4] - a[4]) * f, a];
  if (a) return [a[3], a[4], a];
  if (b) return [b[3], b[4], b];
  return null;
}

function notableFor(typeName) {
  const n = meta.typeNames[typeName] || "";
  for (const [prefix, glyph] of NOTABLE)
    if (n.startsWith(prefix)) return glyph;
  return null;
}

function draw() {
  if (!snaps.length) return;
  const w = canvas.width;
  const D = meta.mapWidth;
  const s2 = w / (2 * D);          // diagonal fit: diamond fills the square
  const tile = w / D;              // world-tile screen size (for markers)
  // locate the two samples around t
  let i = 0;
  while (i + 1 < snaps.length && snaps[i + 1].t <= t) i++;
  const s0 = snaps[i];
  const s1 = snaps[Math.min(i + 1, snaps.length - 1)];
  const span = s1.t > s0.t ? s1.t - s0.t : 1;
  const f = Math.max(0, Math.min(1, (t - s0.t) / span));
  if (idxI !== i || !idxA) {
    idxA = { b: idIndex(s0, "b"), m: idIndex(s0, "m"), v: idIndex(s0, "v") };
    idxB = { b: idIndex(s1, "b"), m: idIndex(s1, "m"), v: idIndex(s1, "v") };
    idxI = i;
  }

  // terrain: rotate the square map image 45 deg + fit so the world
  // diamond touches the canvas edge midpoints (dark corners, in-game
  // minimap look). Matrix = rotate 45 + scale 1/sqrt2.
  ctx.setTransform(0.5, 0.5, -0.5, 0.5, w / 2, 0);
  if (bgReady) ctx.drawImage(bgImg, 0, 0, w, w);
  else { ctx.fillStyle = "#2d542d"; ctx.fillRect(0, 0, w, w); }
  ctx.setTransform(1, 0, 0, 1, 0, 0);

  const toScreen = (x, y) => [(x - y + D) * s2, (x + y) * s2];

  const layer = (rowsIn, from, to, px, circular) => {
    const rows = Array.isArray(rowsIn) ? rowsIn : [];
    for (const row of rows) {
      const p = interp(from, to, row[0], f);
      if (!p) continue;
      const [x, y] = toScreen(p[0], p[1]);
      const color = colorOf(row[1]);
      const notable = circular ? null : notableFor(row[2]);
      const size = (notable ? NOTABLE_PX : px) * tile;
      const r = size / 2;
      ctx.fillStyle = color;
      ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.75)";
      if (circular) {
        ctx.beginPath();
        ctx.arc(x, y, Math.max(1.1, r), 0, 6.2832);
        ctx.fill();
        ctx.stroke();
      } else {
        ctx.fillRect(x - r, y - r, size, size);
        ctx.strokeRect(x - r, y - r, size, size);
        if (notable) {
          ctx.font = Math.round(size * 0.85) + "px serif";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillStyle = "#fff";
          ctx.fillText(notable, x, y);
          ctx.strokeText(notable, x, y);
        }
      }
    }
  };
  layer(s0.b, idxA.b, idxB.b, LAYER_PX, false);
  layer(s0.m, idxA.m, idxB.m, LAYER_UNIT, true);
  layer(s0.v, idxA.v, idxB.v, LAYER_VILL, true);

  updateStrip();
  clockEl.textContent = fmtTime(t);
}

// in-game-style top strip: per player color chip + name + pop + age
// (the engine exposes no score attribute — probed PlayerAttribute.SCORE,
// absent in this API — so the strip shows the top bar's pop/age columns)
function updateStrip() {
  let i = statsI;
  while (i + 1 < statRows.length && statRows[i + 1].t <= t) i++;
  if (i === statsI) return;
  statsI = i;
  if (i < 0) return;
  const stats = statRows[i].stats;
  for (const chip of chips) {
    const row = Array.isArray(stats) ? stats[chip.pid] : null;
    chip.popEl.textContent = row && Number.isFinite(row[5]) ? row[5] : "";
    chip.ageEl.textContent =
      row && row[0] >= 0 && row[0] <= 3 ? AGE_ROMAN[row[0]] : "";
  }
}

function buildStrip() {
  stripEl.innerHTML = "";
  chips = [];
  for (const p of meta.players) {
    if (p.id < 1 || p.id > 8) continue;
    const chip = document.createElement("span");
    chip.className = "pchip";
    const name = (playerInfo[p.id] && playerInfo[p.id].name) ||
      ("P" + p.id);
    chip.innerHTML = "<span class='pdot' style='background:" +
      colorOf(p.id) + "'></span><span class='pname'></span>" +
      "<span class='ppop'></span><span class='page'></span>";
    chip.querySelector(".pname").textContent = name;
    stripEl.appendChild(chip);
    chips.push({ pid: p.id,
                 popEl: chip.querySelector(".ppop"),
                 ageEl: chip.querySelector(".page") });
  }
  statsI = -1;
}

function loop(ts) {
  if (playing) {
    if (lastTs !== null) {
      t += (ts - lastTs) / 1000 * parseFloat(speedEl.value);
      const end = snaps[snaps.length - 1].t;
      if (t >= end) { t = end; setPlaying(false); }
    }
    seekEl.value = t;
  }
  lastTs = ts;
  draw();
  requestAnimationFrame(loop);
}

function setPlaying(on) {
  playing = on;
  playBtn.innerHTML = on ? "&#10074;&#10074;" : "&#9654;";
}

playBtn.onclick = () => {
  if (!playing && t >= snaps[snaps.length - 1].t) t = 0;
  setPlaying(!playing);
};
seekEl.oninput = () => { t = parseFloat(seekEl.value); };
document.addEventListener("keydown", (e) => {
  if (e.code === "Space") { e.preventDefault(); playBtn.onclick(); }
  else if (e.code === "ArrowRight") t = Math.min(t + 1, snaps.at(-1).t);
  else if (e.code === "ArrowLeft") t = Math.max(0, t - 1);
});

async function load() {
  document.getElementById("mapname").textContent = "loading " + tag + " ...";
  const [txt] = await Promise.all([
    fetch("/api/data/" + tag).then((r) => {
      if (!r.ok) throw new Error("no positions for '" + tag + "'");
      return r.text();
    }),
    new Promise((res) => {
      bgImg.onload = () => { bgReady = true; res(); };
      bgImg.onerror = res;
      bgImg.src = "/api/map/" + tag;
    }),
  ]);
  let lastMeta = null, mapName = null;
  for (const line of txt.split("\n")) {
    if (!line.trim()) continue;
    const r = JSON.parse(line);
    if (r.type === "meta") lastMeta = r;
    else if (r.type === "map") mapName = r.name;
    else if (r.type === "names") playerInfo = r.names || {};
    else if (r.type === "objects")
      snaps.push({ t: r.time, b: r.b, v: r.v, m: r.m });
    else if (r.type === "stats") statRows.push({ t: r.time, stats: r.stats });
  }
  meta = lastMeta;
  if (!meta || !snaps.length)
    throw new Error("no usable rows in the positions file");
  meta.mapName = mapName;
  document.getElementById("mapname").textContent =
    (meta.mapName ? meta.mapName + " \u2014 " : "") + tag;
  buildStrip();
  const end = snaps[snaps.length - 1].t;
  seekEl.max = end;
  const t0 = parseFloat(new URLSearchParams(location.search).get("t"));
  if (t0 > 0) { t = Math.min(t0, end); seekEl.value = t; }
  requestAnimationFrame(loop);
}

load().catch((e) => {
  document.getElementById("mapname").textContent = "ERROR: " + e.message;
});
