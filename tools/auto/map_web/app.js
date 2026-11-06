/* AoE2 map replay: canvas minimap animation over collected object
 * snapshots. Data (per game-second, columnar [id, owner, unitType, x, y]):
 *   meta    {mapWidth, mapHeight, players:[{id,name}], typeNames}
 *   objects {b: buildings, v: villagers, m: military}
 * Buildings are drawn as squares (notable types get an icon), units as
 * dots in player colors. Frames interpolate between samples for smooth
 * movement; the slider seeks, the button/space plays.
 */
"use strict";

const tag = new URLSearchParams(location.search).get("tag") || "demo";
const PLAYER_COLORS = {
  1: "#4a7dff", 2: "#ff4a4a", 3: "#ffd24a", 4: "#ff8a2a",
  5: "#37d2b8", 6: "#d24ae0", 7: "#c8c8c8", 8: "#b8ff4a",
};
const GAIA = "#9ab09a";
const ICONS = [
  ["TOWN_CENTER", "\u2302"],      // house-like glyph
  ["CASTLE", "\u265e"],           // knight
  ["KREPOST", "\u265e"],
];
// draw order: big buildings under, units on top (sizes in tiles; the
// demo's 120-tile map renders ~2x bigger than a 231-tile map)
const LAYER_PX = 1.3, LAYER_UNIT = 0.75, LAYER_VILL = 0.5;

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const playBtn = document.getElementById("play");
const seekEl = document.getElementById("seek");
const speedEl = document.getElementById("speed");
const clockEl = document.getElementById("clock");

let meta = null;          // latest meta row
let snaps = [];           // [{t, b, v, m}]
let bgImg = new Image();
let bgReady = false;
let playing = false;
let t = 0;                // current game time
let lastTs = null;
// id -> row index maps for the two frames around the cursor (rebuilt on
// frame change only — 1500 lookups/frame instead of 1500 inserts/frame)
let idxA = null, idxB = null, idxI = -1;

function fmtTime(s) {
  const m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return m + ":" + String(ss).padStart(2, "0");
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

function iconFor(typeName) {
  const n = meta.typeNames[typeName] || "";
  for (const [prefix, glyph] of ICONS)
    if (n.startsWith(prefix)) return glyph;
  return null;
}

function draw() {
  if (!snaps.length) return;
  const w = canvas.width;
  const scale = w / meta.mapWidth;
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

  if (bgReady) ctx.drawImage(bgImg, 0, 0, w, w);
  else { ctx.fillStyle = "#2d542d"; ctx.fillRect(0, 0, w, w); }

  const layer = (rowsIn, from, to, px, outline, iconFn) => {
    const rows = Array.isArray(rowsIn) ? rowsIn : [];
    for (const row of rows) {
      const p = interp(from, to, row[0], f);
      if (!p) continue;
      const x = p[0] * scale, y = p[1] * scale;
      const color = PLAYER_COLORS[row[1]] || GAIA;
      if (iconFn) {
        const glyph = iconFn(row[2]);
        if (glyph) {
          ctx.font = Math.round(px * 2.6 * scale) + "px serif";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillStyle = color;
          ctx.fillText(glyph, x, y);
          ctx.lineWidth = 1; ctx.strokeStyle = "#000";
          ctx.strokeText(glyph, x, y);
          continue;
        }
      }
      const r = px * scale / 2;
      ctx.fillStyle = color;
      if (px >= LAYER_PX) {          // buildings: minimap-style squares
        ctx.fillRect(x - r, y - r, r * 2, r * 2);
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.7)";
        ctx.strokeRect(x - r, y - r, r * 2, r * 2);
        continue;
      }
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 6.2832);
      ctx.fill();
      if (outline) { ctx.lineWidth = 1; ctx.strokeStyle = "rgba(0,0,0,.7)"; ctx.stroke(); }
    }
  };
  layer(s0.b, idxA.b, idxB.b, LAYER_PX, false, iconFor);
  layer(s0.m, idxA.m, idxB.m, LAYER_UNIT, true, null);
  layer(s0.v, idxA.v, idxB.v, LAYER_VILL, false, null);

  clockEl.textContent = fmtTime(t);
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
speedEl.onchange = () => {};
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
    else if (r.type === "objects")
      snaps.push({ t: r.time, b: r.b, v: r.v, m: r.m });
  }
  meta = lastMeta;
  if (!meta || !snaps.length)
    throw new Error("no usable rows in the positions file");
  meta.mapName = mapName;
  document.getElementById("mapname").textContent =
    tag + (meta.mapName ? " \u2014 " + meta.mapName : "");
  const pl = document.getElementById("players");
  for (const p of meta.players) {
    if (p.id < 1 || p.id > 8) continue;
    const chip = document.createElement("span");
    chip.className = "pchip";
    chip.innerHTML = "<span class='pdot' style='background:" +
      (PLAYER_COLORS[p.id] || GAIA) + "'></span>" +
      (p.name || ("P" + p.id));
    pl.appendChild(chip);
  }
  const end = snaps[snaps.length - 1].t;
  seekEl.max = end;
  const t0 = parseFloat(new URLSearchParams(location.search).get("t"));
  if (t0 > 0) { t = Math.min(t0, end); seekEl.value = t; }
  requestAnimationFrame(loop);
}

load().catch((e) => {
  document.getElementById("mapname").textContent = "ERROR: " + e.message;
});
