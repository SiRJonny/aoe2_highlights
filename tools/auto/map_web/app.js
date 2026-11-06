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
const iconSet = QS.get("icons") || "vector";
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
  ["WATCH_TOWER", "tower"],
  ["GUARD_TOWER", "tower"],
  ["KEEP", "tower"],
  ["BOMBARD_TOWER", "tower"],
];
// draw order: plain buildings under, notable icons above them, villagers
// next, military on top (sizes in world tiles)
function qnum(k, d) {
  const v = parseFloat(QS.get(k));
  return v > 0 ? v : d;
}
const SIZES = {
  bld: qnum("bld", 1.3),        // plain building square
  notable: qnum("notable", 5),  // notable building (icon) square
  unit: qnum("unit", 0.75),     // military dot
  vill: qnum("vil", 0.5),       // villager dot
  cav: qnum("cav", 3),          // cavalry multiplier (default = slider max)
  inf: qnum("inf", 2),          // infantry multiplier (default = slider max)
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
let snaps = [];           // [{t, b, v, m, eco}]
let statRows = [];        // [{t, stats}] (stats[i] = player i)
let scoreRows = [];       // [{t, scores}] real score per player (API)
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
let bPlain = null, bNotable = null;   // buildings split for draw order
let curEco = {};          // eco count (villagers + fishers + traders)
let scoresI = -1, curScores = null;   // real score rows, aligned to t
let dataHasHp = false;    // snapshot building rows carry hp (new data)
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

const civAbbr = (c) =>
  (c || "").replace(/[^A-Za-z]/g, "").slice(0, 3).toUpperCase();

/* Unfinished buildings: hp below the type's max (or a foundation row
 * without hp). maxHp lookup strips age/upgrade suffixes from the type
 * name; BUILDING_HP comes from aoe2techtree (genie ids via the
 * aoc-reference-data names). */
const AGE_SUFFIX =
  /_(DARK_AGE|FEUDAL_AGE|CASTLE_AGE|IMPERIAL_AGE|UPGRADED|BASE)$/;
function maxHpFor(typeName) {
  let n = (meta.typeNames[typeName] || "").toUpperCase();
  for (;;) {
    const m = n.match(AGE_SUFFIX);
    if (!m) break;
    n = n.slice(0, n.length - m[0].length);
  }
  return BUILDING_HP[n] || null;
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
 * the annexes and collapse same-owner same-kind notables within a small
 * radius (completion re-ids the object, foundation row lingers) keeping
 * the older id. Radius 3 tiles for footprint buildings, 1.5 for 1x1
 * towers; legit same-kind notables sit farther apart. */
function pruneBuildings(rows) {
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const r of rows) {
    const name = meta.typeNames[r[2]] || "";
    if (name.includes("_ANNEX")) continue;
    const kind = notableKindFor(r[2]);
    if (kind) {
      const rad = kind === "tower" ? 1.5 : 3;
      let twin = null;
      for (const q of out) {
        if (q[1] === r[1] && notableKindFor(q[2]) === kind
            && Math.abs(q[3] - r[3]) <= rad
            && Math.abs(q[4] - r[4]) <= rad) {
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
    const bDed = pruneBuildings(s0.b);
    bNotable = bDed.filter((r) => notableKindFor(r[2]));
    bPlain = bDed.filter((r) => !notableKindFor(r[2]));
    curEco = s0.eco || {};
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
        // infantry vs cavalry vs warships: the Lua side tags row[5]
        const tag5 = p[2] && p[2][5];
        const mult = tag5 === "c" ? SIZES.cav
                   : tag5 === "i" ? SIZES.inf : 1;
        size *= mult * SIZES.dot;
        if (tag5 === "c" || tag5 === "i" || tag5 === "s") {
          // silhouette marker; its box may exceed the dot footprint
          ctx.save();
          ctx.translate(x, y);
          const box = size * 2.2;
          ctx.scale(box / 24, box / 24);
          ctx.translate(-12, -12);
          ctx.lineWidth = 2; ctx.lineJoin = "round";
          if (tag5 === "c") CAVALRY_SHAPE(ctx, color);
          else if (tag5 === "i") SOLDIER_SHAPE(ctx, color);
          else SHIP_SHAPE(ctx, color);
          ctx.restore();
          continue;
        }
        const r = size / 2;
        ctx.fillStyle = color;
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.75)";
        ctx.beginPath();
        ctx.arc(x, y, Math.max(1.1, r), 0, 6.2832);
        ctx.fill();
        ctx.stroke();
        continue;
      }
      drawBuilding(kind, x, y, size, color, row[6], row[2]);
    }
  };
  layer(bPlain, idxA.b, idxB.b, "bld", false);
  layer(bNotable, idxA.b, idxB.b, "notable", false);
  layer(s0.v, idxA.v, idxB.v, "vill", true);
  layer(s0.m, idxA.m, idxB.m, "unit", true);

  updateStrip();
  clockEl.textContent = fmtTime(t);
}

/* Buildings: dispatch to the selected icon set. Glyph/emoji draw a player-
 * colored square with the glyph on top; vector replaces the square with a
 * painted silhouette. Any kind a set does not cover falls back to the
 * glyph square. Buildings under construction (hp < max, or a foundation
 * row) render translucent. */
function drawBuilding(kind, x, y, size, color, hp, typeId) {
  const r = size / 2;
  let alpha = 1;
  if (dataHasHp) {
    const tname = meta.typeNames[typeId] || "";
    if (hp === undefined || hp === null || /_FOUNDATION/.test(tname)) {
      alpha = 0.45;
    } else {
      const max = maxHpFor(typeId);
      if (max && hp < max * 0.98) alpha = 0.45;
    }
  }
  if (alpha < 1) ctx.globalAlpha = alpha;
  ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.75)";
  ctx.fillStyle = color;
  const vec = kind && ICON_SETS.vector[kind];
  if (vec) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(size / 24, size / 24);
    ctx.translate(-12, -12);       // painters draw in a 24x24 box
    ctx.lineWidth = 2; ctx.lineJoin = "round";
    vec(ctx);
    ctx.restore();
  } else {
    ctx.fillRect(x - r, y - r, size, size);
    ctx.strokeRect(x - r, y - r, size, size);
    const glyph = kind ? ICON_SETS[iconSet][kind] : null;
    if (glyph) {
      ctx.font = Math.round(size * (iconSet === "emoji" ? 0.72 : 0.85)) +
        "px serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillStyle = "#fff";
      ctx.fillText(glyph, x, y + size * 0.04);
      ctx.strokeText(glyph, x, y + size * 0.04);
    }
  }
  if (alpha < 1) ctx.globalAlpha = 1;
}

// in-game-style left panel: teams sorted by total score (descending),
// players sorted within their team, each row score + color + name +
// pop + age. Score = the game's own Fact.CURRENT_SCORE per player when
// the API exposes it (auto-detected signature), otherwise the exact eco
// count (villagers + fishing ships + trade units) — no approximation.
function updateStrip() {
  // cursors rebase when seeking backwards so scores/pop/age track t
  let i = (statsI >= 0 && statRows[statsI] && statRows[statsI].t <= t)
    ? statsI : -1;
  while (i + 1 < statRows.length && statRows[i + 1].t <= t) i++;
  if (i === statsI) return;
  statsI = i;
  if (i < 0) return;
  let si = (scoresI >= 0 && scoreRows[scoresI] && scoreRows[scoresI].t <= t)
    ? scoresI : -1;
  while (si + 1 < scoreRows.length && scoreRows[si + 1].t <= t) si++;
  scoresI = si;
  curScores = si >= 0 ? scoreRows[si] : null;
  const stats = statRows[i].stats;
  for (const chip of chips) {
    const row = Array.isArray(stats) ? stats[chip.pid] : null;
    const sc = curScores ? curScores.scores[chip.pid] : undefined;
    const eco = curEco[chip.pid];
    const val = (sc !== undefined && sc !== null) ? sc
      : (eco === undefined ? null : eco);
    chip.scoreEl.textContent = val === null ? "" : val;
    chip.popEl.textContent = row && Number.isFinite(row[5]) ? row[5] : "";
    chip.ageEl.innerHTML = row && row[0] >= 0 && row[0] <= 3
      ? "<img src=\"/ages/age_" + (row[0] + 1) + ".png\" alt=\"Age " +
        AGE_ROMAN[row[0]] + "\" title=\"Age " + AGE_ROMAN[row[0]] + "\">"
      : "";
    chip.score = val;
    // resigned/defeated players: the PLAYER_IN_GAME fact flips to 0
    const out = !!(curScores && curScores.inGame &&
                   curScores.inGame[chip.pid] === 0);
    if (chip.out !== out) {
      chip.out = out;
      chip.rowEl.classList.toggle("out", out);
    }
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
    const teEl = group.querySelector(".teco");
    if (teEl) {
      teEl.textContent = "eco " +
        rows.reduce((s2, c) => s2 + (curEco[c.pid] || 0), 0);
    }
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
        "<span class='tmeta'><span class='tscore'></span>" +
        "<span class='teco'></span></span>";
      head.querySelector(".tname").textContent =
        key === "0" ? "FFA" : "Team " + key;
      group.appendChild(head);
    }
    const body = document.createElement("div");
    body.className = "tbody";
    group.appendChild(body);
    for (const pid of members) {
      const info = playerInfo[pid] || {};
      const rowEl = document.createElement("div");
      rowEl.className = "prow";
      rowEl.innerHTML = "<span class='pdot' style='background:" +
        colorOf(pid) + "'></span><span class='pname'></span>" +
        "<span class='ppop'></span><span class='page'></span>" +
        "<span class='pscore'></span>";
      const abbr = civAbbr(info.civ);
      rowEl.querySelector(".pname").textContent =
        (abbr ? "[" + abbr + "] " : "") + (info.name || ("P" + pid));
      body.appendChild(rowEl);
      chips.push({ pid, team: +key, score: null, out: false, rowEl,
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
    else if (r.type === "objects") {
      const eco = {};
      if (Array.isArray(r.v))
        for (const row of r.v)
          eco[row[1]] = (eco[row[1]] || 0) + 1;
      snaps.push({ t: r.time, b: r.b, v: r.v, m: r.m, eco });
    }
    else if (r.type === "stats") statRows.push({ t: r.time, stats: r.stats });
    else if (r.type === "scores")
      scoreRows.push({ t: r.time, scores: r.scores || {} });
  }
  meta = lastMeta;
  if (!meta || !snaps.length)
    throw new Error("no usable rows in the positions file");
  meta.mapName = mapName;
  document.getElementById("mapname").textContent =
    (meta.mapName ? meta.mapName + " \u2014 " : "") + tag;
  buildStrip();
  dataHasHp = snaps.some((s) => (s.b || []).some((r) => r.length >= 6));
  document.getElementById("scorelabel").textContent =
    scoreRows.length ? "score" : "eco count";
  const end = snaps[snaps.length - 1].t;
  seekEl.max = end;
  const t0 = parseFloat(new URLSearchParams(location.search).get("t"));
  if (t0 > 0) { t = Math.min(t0, end); seekEl.value = t; }
  requestAnimationFrame(loop);
}

load().catch((e) => {
  document.getElementById("mapname").textContent = "ERROR: " + e.message;
});
