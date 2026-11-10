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

/* Infantry marker: foot soldier with spear and shield, pawn-like
 * profile so it reads beside the cavalry horse at dot sizes. */
const SOLDIER_SHAPE = (ctx, color) => {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(8.5, 21);                            // left foot
  ctx.lineTo(8, 15);                              // hip left
  ctx.lineTo(9.5, 9);                             // shoulder
  ctx.quadraticCurveTo(9.5, 6.5, 11.5, 6);        // neck
  ctx.arc(12, 4.2, 2.1, Math.PI * 0.9, Math.PI * 0.1, true); // head
  ctx.lineTo(14.5, 9);                            // right shoulder
  ctx.lineTo(16, 15);                             // hip right
  ctx.lineTo(15.5, 21);                           // right foot
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  // spear: shaft from ground up past the head, tip upper-left
  ctx.beginPath();
  ctx.moveTo(16.2, 21);
  ctx.lineTo(6.2, 4.5);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(6.2, 4.5);
  ctx.lineTo(4.2, 2);
  ctx.lineTo(7.6, 2.6);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  // shield on the left arm with a white boss
  ctx.beginPath();
  ctx.arc(8.6, 14.5, 3.1, 0, 6.2832);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.arc(8.6, 14.5, 1.1, 0, 6.2832);
  ctx.fill();
};

/* Warship marker: long low hull with an upturned bow and two white
 * lateen sails (tall fore mast, shorter aft mast), after the user's
 * reference sketch. */
const SHIP_SHAPE = (ctx, color) => {
  // masts first, so the hull and sails overlap them cleanly
  ctx.beginPath();
  ctx.moveTo(9, 16);                              // fore mast
  ctx.lineTo(9, 2.6);
  ctx.moveTo(16.5, 16);                           // aft mast
  ctx.lineTo(16.5, 6.6);
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.beginPath();                                // fore sail (bulges right)
  ctx.moveTo(9.4, 3.4);
  ctx.quadraticCurveTo(14.8, 6.2, 13.6, 12.2);
  ctx.quadraticCurveTo(13.1, 14.4, 11.6, 15.4);
  ctx.quadraticCurveTo(10.2, 9.4, 9.4, 3.8);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();                                // aft sail
  ctx.moveTo(16.9, 7.0);
  ctx.quadraticCurveTo(20.6, 9.4, 19.6, 13.8);
  ctx.quadraticCurveTo(19.2, 15.4, 18.0, 15.8);
  ctx.quadraticCurveTo(16.7, 11.2, 16.9, 7.4);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();                                // hull
  ctx.moveTo(2, 16.2);                            // bow tip
  ctx.lineTo(21.6, 15.2);                         // deck line
  ctx.quadraticCurveTo(20.4, 19.4, 17.2, 20.6);
  ctx.quadraticCurveTo(9, 22, 4.4, 20.6);
  ctx.quadraticCurveTo(2.6, 19.8, 2, 16.2);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
};

/* Fish marker (gaia deep-sea fish): plain body + tail; draws in the
 * caller's current fillStyle (white normally, gold for whales). */
const FISH_SHAPE = (ctx) => {
  ctx.beginPath();
  ctx.ellipse(9.5, 12, 6, 3.4, 0, 0, 6.2832);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(14.5, 12);
  ctx.lineTo(19.5, 8.5);
  ctx.lineTo(19.5, 15.5);
  ctx.closePath();
  ctx.fill();
};

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

/* ---- unit silhouettes routed by unit-type id (app.js). Same 24x24
 * design box, player-color fill, white details, dark stroke. Two
 * variants each live here side by side so the comparison table can offer
 * both (?variant=); TRANSPORT_SHAPE/SIEGE_SHAPE/CANNON_SHAPE/
 * ELEPHANT_SHAPE alias the app's current choice. */

/* Transport ships. "sail": broad cog with a square sail (vs the
 * warship's crescent hull + triangular lateen). "deck": low open hull
 * with troop helmets peeking over the gunwale + oars. */
const TRANSPORT_SHAPES = {
  sail(ctx, color) {
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.rect(6.8, 4.6, 10, 6.6);                  // square sail
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.beginPath();                              // pennant
    ctx.moveTo(11.8, 3.2);
    ctx.lineTo(16.4, 4.1);
    ctx.lineTo(11.8, 5.2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();                              // hull
    ctx.moveTo(2.2, 14.2);
    ctx.quadraticCurveTo(3.5, 19.8, 12, 19.8);
    ctx.quadraticCurveTo(20.5, 19.8, 21.8, 14.2);
    ctx.lineTo(19.8, 13.2);
    ctx.lineTo(4.2, 13.2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();                              // mast
    ctx.moveTo(11.8, 13.2);
    ctx.lineTo(11.8, 3.2);
    ctx.stroke();
  },
  deck(ctx, color) {
    ctx.fillStyle = color;
    ctx.beginPath();                              // open hull
    ctx.moveTo(2.2, 13.6);
    ctx.lineTo(21.8, 13.6);
    ctx.lineTo(19.2, 18.8);
    ctx.quadraticCurveTo(12, 20.6, 4.8, 18.8);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";                       // troop helmets
    for (const hx of [7, 11.5, 16]) {
      ctx.beginPath();
      ctx.arc(hx, 13.4, 1.5, Math.PI, 0, false);
      ctx.fill();
    }
    ctx.beginPath();                              // oars
    ctx.moveTo(5.6, 15.6);
    ctx.lineTo(2.2, 19.4);
    ctx.moveTo(9.6, 16.4);
    ctx.lineTo(6.2, 20.2);
    ctx.moveTo(14.4, 16.4);
    ctx.lineTo(11, 20.2);
    ctx.moveTo(18.4, 15.6);
    ctx.lineTo(15, 19.4);
    ctx.lineWidth = 1.4;
    ctx.stroke();
  },
};
const TRANSPORT_SHAPE = TRANSPORT_SHAPES.deck;

/* Common siege (mangonel family + scorpions + trebs). "arm": A-frame
 * with a raised throwing arm and sling. "bolt": ballista/bolt thrower. */
const SIEGE_SHAPES = {
  arm(ctx, color) {
    ctx.fillStyle = color;
    for (const [wx, wy] of [[5.6, 19.6], [17.2, 19.6]]) {
      ctx.beginPath();                            // 2 wheels
      ctx.arc(wx, wy, 2.2, 0, 6.2832);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(wx, wy, 0.8, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = color;
    }
    ctx.beginPath();                              // left strut
    ctx.moveTo(3.4, 20.8);
    ctx.lineTo(12.6, 11.6);
    ctx.lineTo(14.8, 13.6);
    ctx.lineTo(7.4, 20.8);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();                              // throwing arm
    ctx.moveTo(12.8, 12.4);
    ctx.lineTo(18.6, 3.6);
    ctx.stroke();
    ctx.fillStyle = "#fff";                       // sling
    ctx.beginPath();
    ctx.arc(18.8, 3.4, 1.8, 0, 6.2832);
    ctx.fill();
    ctx.stroke();
  },
  bolt(ctx, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.rect(5.4, 14.6, 12.4, 4);                 // carriage
    ctx.fill();
    ctx.stroke();
    for (const wx of [7.2, 16]) {
      ctx.beginPath();                            // 2 wheels
      ctx.arc(wx, 19.8, 2, 0, 6.2832);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(wx, 19.8, 0.8, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = color;
    }
    ctx.beginPath();                              // bow arc
    ctx.moveTo(6.2, 13.4);
    ctx.quadraticCurveTo(11.2, 5.6, 16.2, 13.4);
    ctx.lineWidth = 1.6;
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.beginPath();                              // string
    ctx.moveTo(6.2, 13.4);
    ctx.lineTo(16.2, 13.4);
    ctx.stroke();
    ctx.strokeStyle = "#fff";                     // bolt + head
    ctx.beginPath();
    ctx.moveTo(5, 11.6);
    ctx.lineTo(19.6, 11.6);
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.moveTo(19.6, 11.6);
    ctx.lineTo(16.8, 10);
    ctx.lineTo(16.8, 13.2);
    ctx.closePath();
    ctx.fill();
  },
};
const SIEGE_SHAPE = SIEGE_SHAPES.arm;

/* Bombard cannons. "barrel": angled tube on a two-wheel carriage,
 * muzzle ring picked out in white. "mortar": stubby steep tube. */
const CANNON_SHAPES = {
  barrel(ctx, color) {
    ctx.fillStyle = color;
    for (const wx of [6.8, 14.6]) {
      ctx.beginPath();                            // wheels
      ctx.arc(wx, 20, 2.1, 0, 6.2832);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(wx, 20, 0.8, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = color;
    }
    ctx.save();                                   // barrel tube
    ctx.translate(12.4, 10.6);
    ctx.rotate(-0.72);
    ctx.beginPath();
    ctx.rect(-8.4, -2, 15.6, 4);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
    ctx.strokeStyle = "#fff";                     // muzzle ring
    ctx.beginPath();
    ctx.moveTo(17.4, 3.4);
    ctx.lineTo(20.2, 6.2);
    ctx.lineWidth = 1.6;
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(0,0,0,.75)";
    ctx.fillStyle = color;                        // carriage bed
    ctx.beginPath();
    ctx.moveTo(3.6, 17.6);
    ctx.lineTo(18.4, 17.6);
    ctx.lineTo(16.6, 15.4);
    ctx.lineTo(5.4, 15.4);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  },
  mortar(ctx, color) {
    ctx.fillStyle = color;
    for (const wx of [7.6, 15.4]) {
      ctx.beginPath();                            // big wheels
      ctx.arc(wx, 20.2, 2.3, 0, 6.2832);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(wx, 20.2, 0.9, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = color;
    }
    ctx.save();                                   // stub tube
    ctx.translate(11.6, 11.8);
    ctx.rotate(-1.05);
    ctx.beginPath();
    ctx.rect(-6.6, -3, 12, 6);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = "#fff";                       // muzzle mouth
    ctx.beginPath();
    ctx.arc(16.4, 3.6, 1.5, 0, 6.2832);
    ctx.fill();
    ctx.stroke();
  },
};
const CANNON_SHAPE = CANNON_SHAPES.barrel;

/* Elephants. "howdah": war elephant with a battle tower + trunk.
 * "plain": same profile, tusk + forehead strap instead. */
const elephantBody = (ctx, color) => {
  ctx.fillStyle = color;
  ctx.beginPath();
  // one continuous outline: rump -> back -> head -> trunk -> legs
  ctx.moveTo(2.4, 14.2);                          // rear base
  ctx.quadraticCurveTo(2.0, 9.7, 6.8, 9.2);       // rump
  ctx.quadraticCurveTo(12.0, 8.3, 15.2, 9.2);     // back
  ctx.quadraticCurveTo(18.2, 9.8, 18.6, 11.4);    // forehead, face
  ctx.quadraticCurveTo(22.6, 12.8, 21.6, 18.0);   // trunk outer curve
  ctx.lineTo(20.3, 17.7);                         // trunk tip
  ctx.quadraticCurveTo(21.0, 13.6, 17.6, 13.0);   // trunk inner -> chin
  ctx.lineTo(17.2, 14.4);                         // chest
  ctx.lineTo(17.2, 19.9);                         // front leg
  ctx.lineTo(15.4, 19.9);
  ctx.lineTo(15.4, 14.4);
  ctx.lineTo(12.9, 14.4);                         // leg 3
  ctx.lineTo(12.9, 19.9);
  ctx.lineTo(11.0, 19.9);
  ctx.lineTo(11.0, 14.4);
  ctx.lineTo(8.0, 14.4);                          // leg 2
  ctx.lineTo(8.0, 19.9);
  ctx.lineTo(6.0, 19.9);
  ctx.lineTo(6.0, 14.4);
  ctx.lineTo(4.6, 14.4);                          // rear leg
  ctx.lineTo(4.6, 19.9);
  ctx.lineTo(2.8, 19.9);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fff";                         // eye
  ctx.beginPath();
  ctx.arc(16.4, 10.6, 0.9, 0, 6.2832);
  ctx.fill();
  ctx.beginPath();                                // tail
  ctx.moveTo(2.4, 13.8);
  ctx.lineTo(1.4, 16.8);
  ctx.stroke();
};
const ELEPHANT_SHAPES = {
  howdah(ctx, color) {
    elephantBody(ctx, color);
    ctx.fillStyle = "#fff";
    ctx.fillRect(8.2, 3.0, 7.6, 5.6);             // tower
    ctx.strokeRect(8.2, 3.0, 7.6, 5.6);
    ctx.fillRect(8.2, 1.5, 7.6, 1.5);             // battlements
    ctx.strokeRect(8.2, 1.5, 7.6, 1.5);
  },
  plain(ctx, color) {
    elephantBody(ctx, color);
    ctx.fillStyle = "#fff";                       // tusk
    ctx.beginPath();
    ctx.moveTo(18.2, 13.2);
    ctx.lineTo(21.2, 15.6);
    ctx.lineTo(17.8, 15.4);
    ctx.closePath();
    ctx.fill();
    ctx.fillRect(14.6, 8.6, 3.6, 1.4);            // forehead strap
    ctx.strokeRect(14.6, 8.6, 3.6, 1.4);
  },
};
const ELEPHANT_SHAPE = ELEPHANT_SHAPES.howdah;

const ICON_SETS = {
  square: {},
  glyph: GLYPH_ICONS,
  emoji: EMOJI_ICONS,
  vector: VECTOR_ICONS,
};
