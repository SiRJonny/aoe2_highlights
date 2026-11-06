/* Building icon options for the map animation (?icons=<name> picks one).
 *
 * Sets:  square  plain squares, no glyph
 *        glyph   square + unicode glyph (original look)
 *        emoji   square + emoji glyph
 *        vector  hand-drawn mini silhouettes instead of squares
 *        game    real DE building icons (tools/auto/map_web/icons/)
 *
 * Every set is keyed by kind: town_center, castle, krepost, donjon,
 * fortified_church, feitoria. A set may return null for a kind — the
 * app.js then falls back to the glyph square.
 */
"use strict";

const GLYPH_ICONS = {
  town_center: "\u2302",
  castle: "\u265c",
  krepost: "\u265c",
  donjon: "\u265c",
  fortified_church: "\u271d",
  feitoria: "\u265c",
};

const EMOJI_ICONS = {
  town_center: "\u{1F3E0}",                  // 🏠 house
  castle: "\u{1F3F0}",                       // 🏰 european castle
  krepost: "\u{1F3EF}",                      // 🏯 japanese castle
  donjon: "\u{1F5FC}",                       // 🗼 tower
  fortified_church: "\u26EA",                // ⛪ church
  feitoria: "\u{1F3E4}",                     // 🏤 post / trade office
};

/* Vector painters: design box is 24x24, centered at (12, 12). The app
 * translates to the building position and scales before calling. Fill
 * color is the owner's player color; details render white on top. */
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

/* Real DE building renders (aoe2db.com). Kinds with null have no clean
 * render anywhere — the game's own TC icon is a villager portrait, so
 * those kinds fall back to the vector silhouette, then the glyph. */
const GAME_ICONS = {
  town_center: null,
  castle: "/icons/castle.png",
  krepost: "/icons/krepost.png",
  donjon: "/icons/donjon.png",
  fortified_church: "/icons/fortified_church.png",
  feitoria: null,
};

const ICON_SETS = {
  square: {},
  glyph: GLYPH_ICONS,
  emoji: EMOJI_ICONS,
  vector: VECTOR_ICONS,
  game: GAME_ICONS,
};
