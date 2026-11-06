/* AoE2 map replay: canvas minimap animation over collected object
 * snapshots. Data (per game-second, columnar [id, owner, unitType, x, y]):
 *   meta    {mapWidth, mapHeight, players:[{id,name}], typeNames}
 *   objects {b: buildings, v: villagers, m: military}
 *   stats   {stats: [[age,food,wood,stone,gold,pop,score?], ...]} per player
 *   names   {names: {pid: {name, colorId}}} from the replay header
 *
 * Orientation: the world is rotated 45 degrees like the in-game minimap,
 * then turned 90 deg to the left (world (0,0) at the left corner).
 * Buildings are squares (notable ones larger with an icon glyph inside —
 * ?icons= switches between square/glyph/emoji/vector/game sets), units
 * are dots, all in each player's exact in-game color (spritecolors.json
 * TeamColors, addressed by the mgz color_id from the replay header).
 */
"use strict";

const QS = new URLSearchParams(location.search);
const tag = QS.get("tag") || "demo";
const iconSet = new URLSearchParams(location.search).get("icons") || "glyph";
// EXACT DE team colors, from the game's own
// resources/_common/palettes/spritecolors.json ("TeamColors", 0-1 floats
// x255): blue red green yellow teal purple gray orange.
const COLOR_ID_HEX = [
  "#0000FF", "#FF0000", "#00A91B", "#D6D61B",
  "#7BEFF0", "#8A13F7", "#666666", "#FF9205",
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
  ["TOWN_CENTER", "town_center"],
  ["CASTLE", "castle"],
  ["KREPOST", "krepost"],
  ["DONJON", "donjon"],
  ["FORTIFIED_CHURCH", "fortified_church"],
  ["FEITORIA", "feitoria"],
];
// draw order: big buildings under, units on top (sizes in world tiles).
// Every value is live-tunable in the debug panel on the right (press d
// to show/hide) and seedable by URL params:
//   ?bld=1.3&notable=2.6&unit=0.75&vil=0.5&cav=1.45&inf=0.85&dot=1
function qnum(k, d) {
  const v = parseFloat(QS.get(k));
  return v > 0 ? v : d;
}
const SIZES = {
  bld: qnum("bld", 1.3),        // plain building square
  notable: qnum("notable", 2.6),// notable building (icon) square
  unit: qnum("unit", 0.75),     // military dot
  vill: qnum("vil", 0.5),       // villager dot
  cav: qnum("cav", 1.45),       // cavalry multiplier on unit
  inf: qnum("inf", 0.85),       // infantry multiplier on unit
  dot: qnum("dot", 1),          // extra multiplier on every unit dot
};
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
let statRows = [];        // [{t, stats}] (stats[i] = player i)
let playerInfo = {};      // pid -> {name, colorId, team}
let chips = [];           // {pid, scoreEl, ageEl}
let bgImg = new Image();
let bgReady = false;
let playing = false;
let t = 0;                // current game time
let lastTs = null;
// id -> row index maps for the two frames around the cursor (rebuilt on
// frame change only — 1500 lookups/frame instead of 1500 inserts/frame)
let idxA = null, idxB = null, idxI = -1;
let bDed = null;          // buildings pruned of annexes/duplicate notables
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

function notableKindFor(typeName) {
  const n = meta.typeNames[typeName] || "";
  for (const [prefix, kind] of NOTABLE)
    if (n.startsWith(prefix)) return kind;
  return null;
}

/* DE town centers are composites: the main building plus 3 annex wings,
 * each a separate object one tile off the TC. All four names start with
 * TOWN_CENTER, so drawing every notable stacks 4 icons on one TC. Drop
 * the annexes and collapse same-owner same-kind notables within 3 tiles
 * (completion re-ids the object, foundation row lingers) keeping the
 * older id. Legit same-kind notables (TC next to TC) sit farther apart. */
function pruneBuildings(rows) {
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const r of rows) {
    const name = meta.typeNames[r[2]] || "";
    if (name.includes("_ANNEX")) continue;
    const kind = notableKindFor(r[2]);
    if (kind) {
      let twin = null;
      for (const q of out) {
        if (q[1] === r[1] && notableKindFor(q[2]) === kind
            && Math.abs(q[3] - r[3]) <= 3 && Math.abs(q[4] - r[4]) <= 3) {
          twin = q;
          break;
        }
      }
      if (twin) {
        if (r[0] < twin[0]) out[out.indexOf(twin)] = r;
        continue;
      }
    }
    out.push(r);
  }
  return out;
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
    bDed = pruneBuildings(s0.b);
    idxI = i;
  }

  // terrain: rotate the square map image 45 deg + fit so the world
  // diamond touches the canvas edge midpoints, then 90 deg to the left
  // (world (0,0) corner at the LEFT, (D,0) top, (0,D) bottom, (D,D)
  // right). Matrix = rotate 45 + scale 1/sqrt2 + quarter turn.
  ctx.setTransform(0.5, -0.5, 0.5, 0.5, 0, w / 2);
  if (bgReady) ctx.drawImage(bgImg, 0, 0, w, w);
  else { ctx.fillStyle = "#2d542d"; ctx.fillRect(0, 0, w, w); }
  ctx.setTransform(1, 0, 0, 1, 0, 0);

  const toScreen = (x, y) => [(x + y) * s2, (D - x + y) * s2];

  const layer = (rowsIn, from, to, sizeKey, circular) => {
    const rows = Array.isArray(rowsIn) ? rowsIn : [];
    for (const row of rows) {
      const p = interp(from, to, row[0], f);
      if (!p) continue;
      const [x, y] = toScreen(p[0], p[1]);
      const color = colorOf(row[1]);
      const kind = circular ? null : notableKindFor(row[2]);
      let size = (kind ? SIZES.notable : SIZES[sizeKey]) * tile;
      if (circular) {
        // infantry vs cavalry: the Lua side tags row[5] where it can
        const tag5 = p[2] && p[2][5];
        const mult = tag5 === "c" ? SIZES.cav
                   : tag5 === "i" ? SIZES.inf : 1;
        size *= mult * SIZES.dot;
        const r = size / 2;
        ctx.fillStyle = color;
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.75)";
        ctx.beginPath();
        ctx.arc(x, y, Math.max(1.1, r), 0, 6.2832);
        ctx.fill();
        ctx.stroke();
        continue;
      }
      drawBuilding(kind, x, y, size, color, row[2]);
    }
  };
  layer(bDed, idxA.b, idxB.b, "bld", false);
  layer(s0.m, idxA.m, idxB.m, "unit", true);
  layer(s0.v, idxA.v, idxB.v, "vill", true);

  updateStrip();
  clockEl.textContent = fmtTime(t);
}

/* Buildings: dispatch to the selected icon set. Glyph/emoji draw a player-
 * colored square with the glyph on top; vector replaces the square with a
 * painted silhouette; game draws the real DE icon on a dark plate. Any
 * kind a set does not cover falls back to the glyph square. */
const gameImgs = {};
for (const [kind, file] of Object.entries(ICON_SETS.game)) {
  if (!file) continue;
  const im = new Image();
  im.src = file;
  gameImgs[kind] = im;
}

function drawBuilding(kind, x, y, size, color, typeName) {
  const r = size / 2;
  ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.75)";
  ctx.fillStyle = color;
  const img = iconSet === "game" && kind && gameImgs[kind];
  const useGame = img && img.complete && img.naturalWidth > 0;
  const vec = kind && ICON_SETS.vector[kind]
    && (iconSet === "vector" || (iconSet === "game" && !useGame))
    ? ICON_SETS.vector[kind] : null;
  if (vec) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(size / 24, size / 24);
    ctx.translate(-12, -12);       // painters draw in a 24x24 box
    ctx.lineWidth = 2; ctx.lineJoin = "round";
    vec(ctx);
    ctx.restore();
    return;
  }
  if (useGame) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 6.2832);
    ctx.fillStyle = "rgba(10,14,10,.72)";
    ctx.fill();
    ctx.stroke();
    ctx.drawImage(img, x - r, y - r, size, size);
    return;
  }
  ctx.fillRect(x - r, y - r, size, size);
  ctx.strokeRect(x - r, y - r, size, size);
  const glyph = kind ? ICON_SETS[iconSet === "game" ? "glyph" : iconSet][kind]
    : null;
  if (glyph) {
    ctx.font = Math.round(size * (iconSet === "emoji" ? 0.72 : 0.85)) +
      "px serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = "#fff";
    ctx.fillText(glyph, x, y + size * 0.04);
    ctx.strokeText(glyph, x, y + size * 0.04);
  }
}

// in-game-style left panel: teams sorted by total score (descending),
// players sorted by score within their team, each row score + color +
// name + pop + age. The replay carries no per-player score value (the
// Lua API has no PlayerAttribute.SCORE and this DE version embeds no
// score in its sync packets), so the score column shows the player's
// current total resources — the number the in-game economy score is
// built from — and ordering mirrors that.
function updateStrip() {
  let i = statsI;
  while (i + 1 < statRows.length && statRows[i + 1].t <= t) i++;
  if (i === statsI) return;
  statsI = i;
  if (i < 0) return;
  const stats = statRows[i].stats;
  for (const chip of chips) {
    const row = Array.isArray(stats) ? stats[chip.pid] : null;
    const res = row ? [1, 2, 3, 4].reduce((s2, k) =>
      s2 + (Number.isFinite(row[k]) ? row[k] : 0), 0) : null;
    chip.scoreEl.textContent = res === null ? "" : Math.round(res);
    chip.popEl.textContent = row && Number.isFinite(row[5]) ? row[5] : "";
    chip.ageEl.textContent =
      row && row[0] >= 0 && row[0] <= 3 ? AGE_ROMAN[row[0]] : "";
    chip.score = res;
  }
  // re-order rows: teams by total score desc, players by score desc
  const teams = {};
  for (const chip of chips) {
    const key = chip.team || 0;
    (teams[key] = teams[key] || []).push(chip);
  }
  for (const group of stripEl.querySelectorAll(".tgroup")) {
    const key = group.dataset.team;
    const rows = teams[key] || [];
    rows.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    const body = group.querySelector(".tbody");
    for (const c of rows) body.appendChild(c.rowEl);
    const total = rows.reduce((s2, c) => s2 + (c.score ?? 0), 0);
    const tEl = group.querySelector(".tscore");
    if (tEl) tEl.textContent = Math.round(total);
  }
  const groups = [...stripEl.querySelectorAll(".tgroup")];
  groups.sort((a, b) => {
    const sum = (k) => (teams[k] || [])
      .reduce((s2, c) => s2 + (c.score ?? 0), 0);
    return sum(b.dataset.team) - sum(a.dataset.team);
  });
  for (const g of groups) stripEl.appendChild(g);
}

function buildStrip() {
  stripEl.innerHTML = "";
  chips = [];
  const teams = {};
  for (const p of meta.players) {
    if (p.id < 1 || p.id > 8) continue;
    const info = playerInfo[p.id] || {};
    const team = info.team || 0;
    teams[team] = teams[team] || [];
    teams[team].push(p.id);
  }
  const teamKeys = Object.keys(teams).sort((a, b) => +a - +b);
  for (const key of teamKeys) {
    const group = document.createElement("div");
    group.className = "tgroup";
    group.dataset.team = key;
    const members = teams[key];
    const showHeader = teamKeys.length > 1 || key !== "0";
    if (showHeader) {
      const head = document.createElement("div");
      head.className = "trow";
      head.innerHTML = "<span class='tname'></span>" +
        "<span class='tscore'></span>";
      head.querySelector(".tname").textContent =
        key === "0" ? "FFA" : "Team " + key;
      group.appendChild(head);
    }
    const body = document.createElement("div");
    body.className = "tbody";
    group.appendChild(body);
    for (const pid of members) {
      const rowEl = document.createElement("div");
      rowEl.className = "prow";
      rowEl.innerHTML = "<span class='pscore'></span>" +
        "<span class='pdot' style='background:" + colorOf(pid) +
        "'></span><span class='pname'></span>" +
        "<span class='ppop'></span><span class='page'></span>";
      rowEl.querySelector(".pname").textContent =
        (playerInfo[pid] && playerInfo[pid].name) || ("P" + pid);
      body.appendChild(rowEl);
      chips.push({ pid, team: +key, score: null, rowEl,
                   scoreEl: rowEl.querySelector(".pscore"),
                   popEl: rowEl.querySelector(".ppop"),
                   ageEl: rowEl.querySelector(".page") });
    }
    stripEl.appendChild(group);
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

// debug size panel: sliders write straight into SIZES; the RAF loop
// redraws every frame, so changes show immediately (even while paused)
const dbgEl = document.getElementById("dbg");
if (dbgEl) {
  for (const input of dbgEl.querySelectorAll("input[type=range]")) {
    const key = input.dataset.size;
    input.value = SIZES[key];
    const out = input.closest(".drow").querySelector(".dval");
    out.textContent = SIZES[key];
    input.oninput = () => {
      const v = parseFloat(input.value);
      if (v > 0) SIZES[key] = v;
      out.textContent = v;
    };
  }
  document.getElementById("dbghide").onclick = toggleDbg;
}
function toggleDbg() { if (dbgEl) dbgEl.classList.toggle("off"); }

playBtn.onclick = () => {
  if (!playing && t >= snaps[snaps.length - 1].t) t = 0;
  setPlaying(!playing);
};
seekEl.oninput = () => { t = parseFloat(seekEl.value); };
document.addEventListener("keydown", (e) => {
  if (e.code === "Space") { e.preventDefault(); playBtn.onclick(); }
  else if (e.code === "KeyD") toggleDbg();
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
