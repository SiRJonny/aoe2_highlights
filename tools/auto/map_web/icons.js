/* Building/unit icon options for the map animation (?icons=<name>).
 *
 * Sets:  square  plain squares, no glyph
 *        glyph   square + unicode glyph
 *        emoji   square + emoji glyph
 *        vector  hand-drawn mini silhouettes instead of squares
 *
 * Every set is keyed by kind: town_center, castle, krepost, donjon,
 * fortified_church, feitoria, tower. A set may return null for a kind —
 * the app falls back to the glyph square then.
 */
"use strict";

const GLYPH_ICONS = {
  town_center: "\u2302",
  castle: "\u265c",
  krepost: "\u265c",
  donjon: "\u265c",
  fortified_church: "\u271d",
  feitoria: "\u265c",
  tower: "\u265c",
};

const EMOJI_ICONS = {
  town_center: "\u{1F3E0}",                  // 🏠 house
  castle: "\u{1F3F0}",                       // 🏰 european castle
  krepost: "\u{1F3EF}",                      // 🏯 japanese castle
  donjon: "\u{1F3DB}\uFE0F",                 // 🏛 classical building
  fortified_church: "\u26EA",                // ⛪ church
  feitoria: "\u{1F3E4}",                     // 🏤 post / trade office
  tower: "\u{1F3DB}\uFE0F",                  // 🏛 same as donjon
};

/* Vector painters: design box is 24x24 centered on (12,12); the app
 * scales and centers before calling. Fill color is the owner's player
 * color; details render white on top. */
const VECTOR_ICONS = {
  town_center(ctx) {
    ctx.beginPath();
    ctx.rect(4, 11, 16, 10);
    ctx.moveTo(2, 12);
    ctx.lineTo(12, 3);
    ctx.lineTo(22, 12);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(10.4, 15, 3.2, 6);
  },
  castle(ctx) {
    ctx.beginPath();
    ctx.rect(5, 10, 14, 11);
    ctx.rect(5, 6, 3.5, 5);
    ctx.rect(10.25, 6, 3.5, 5);
    ctx.rect(15.5, 6, 3.5, 5);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(10.4, 14, 3.2, 7);
  },
  krepost(ctx) {
    ctx.beginPath();
    ctx.rect(3, 12, 18, 9);
    ctx.rect(8, 5, 8, 8);
    ctx.rect(8, 3, 3, 3);
    ctx.rect(13, 3, 3, 3);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(10.4, 14, 3.2, 7);
  },
  donjon(ctx) {
    ctx.beginPath();
    ctx.rect(8, 5, 8, 16);
    ctx.rect(8, 2, 8, 4);
    ctx.rect(9.5, 0.5, 1.6, 2);
    ctx.rect(13, 0.5, 1.6, 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(10.8, 9, 2.4, 5);
  },
  fortified_church(ctx) {
    ctx.beginPath();
    ctx.rect(5, 12, 14, 9);
    ctx.moveTo(4, 13);
    ctx.lineTo(12, 5);
    ctx.lineTo(20, 13);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(11.2, 6.5, 1.6, 5.5);
    ctx.fillRect(9.4, 8, 5.2, 1.6);
  },
  feitoria(ctx) {
    ctx.beginPath();
    ctx.rect(3, 11, 16, 10);
    ctx.rect(3, 8, 16, 4);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(19, 10);
    ctx.lineTo(19, 2);
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillRect(19, 2, 4, 2.6);
  },
};

// towers reuse the donjon silhouette (the dedicated tower painter read
// too heavy on the map)
VECTOR_ICONS.tower = VECTOR_ICONS.donjon;

/* Cavalry marker: small horse/rider silhouette (chess-knight profile).
 * Same 24x24 design box; the app scales it per dot. */
const CAVALRY_SHAPE = (ctx, color) => {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(6.5, 21);                            // base left
  ctx.lineTo(7.5, 15);                            // rump
  ctx.quadraticCurveTo(7, 11, 9.5, 8.5);          // back into neck
  ctx.lineTo(8.5, 6.5);                           // nape
  ctx.lineTo(10, 3);                              // ear
  ctx.lineTo(11.5, 4.5);                          // head top
  ctx.lineTo(14, 4.8);                            // forehead
  ctx.lineTo(17.5, 7.5);                          // muzzle
  ctx.lineTo(16.8, 9.5);
  ctx.lineTo(13.5, 9.8);                          // jaw
  ctx.quadraticCurveTo(15.5, 13, 15.2, 16);       // chest
  ctx.lineTo(17.5, 21);                           // base right
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.fillRect(11.6, 6, 1.6, 1.6);                // eye
};

const ICON_SETS = {
  square: {},
  glyph: GLYPH_ICONS,
  emoji: EMOJI_ICONS,
  vector: VECTOR_ICONS,
};
