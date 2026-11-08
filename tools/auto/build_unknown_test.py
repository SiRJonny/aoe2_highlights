"""Build the unknown-object test page: out/mp5/unknown_test.html

Reads the api10_cam census (unknown-type objects near the game-start
camera, with positions) and the api11_gaia census (map-wide counts) from
positions.jsonl, renders the map with colored numbered markers per
unknown type, and writes an HTML page with a checkbox list so the user
can identify each type in-game."""
import json

import cv2
import numpy as np

POS = 'out/mp5/positions.jsonl'
MAP_PNG = 'out/mp5/map.png'
OUT_HTML = 'out/mp5/unknown_test.html'
OUT_IMG = 'out/mp5/unknown_map.png'

tn = {}
api10 = api11 = None
with open(POS, encoding='utf-8') as fh:
    for line in fh:
        r = json.loads(line)
        t = r.get('type')
        if t == 'meta':
            tn = r['typeNames']
        elif t == 'api10_cam':
            api10 = r
        elif t == 'api11_gaia':
            api11 = r

unk = (api10 or {}).get('unk') or {}
gaia = (api11 or {}).get('out') or {}

# map.png dims for the coordinate transform
img = cv2.imread(MAP_PNG)
h, w = img.shape[:2]
D = 231
s2 = w / (2 * D)

# marker colors per unknown type (BGR for cv2)
COLORS = [(0, 0, 255), (0, 255, 255), (255, 0, 255), (0, 255, 0),
          (255, 128, 0), (0, 128, 255), (255, 255, 0), (128, 0, 255),
          (255, 0, 128), (0, 255, 128)]

unknown_ids = sorted(unk.keys(), key=lambda u: -len(unk[u]))
color_of = {u: COLORS[i % len(COLORS)] for i, u in enumerate(unknown_ids)}

# draw the markers on the map image: a numbered circle per object
for ui, ut in enumerate(unknown_ids):
    color = color_of[ut]
    for p in unk[ut]:
        x, y = map(int, p.split(','))
        sx = int((x + y) * s2)
        sy = int((D - x + y) * s2)
        cv2.circle(img, (sx, sy), 9, color, -1)
        cv2.circle(img, (sx, sy), 9, (0, 0, 0), 1)
        cv2.putText(img, ut, (sx - 8, sy - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3)
        cv2.putText(img, ut, (sx - 8, sy - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
cv2.imwrite(OUT_IMG, img)

# the checkbox list: unknown types with their map-wide gaia counts
rows = []
for ut in unknown_ids:
    gaia_count = gaia.get('0:' + ut, 0)
    name = tn.get(ut, '?')
    plist = unk[ut][:3]
    rows.append((ut, name, gaia_count, plist))

html_rows = '\n'.join(
    '<label class="urow"><input type="checkbox" data-type="{tid}">'
    '<span class="dot" style="background:rgb({r},{g},{b})"></span>'
    '<b>type {tid}</b> — gaia count: {n} — dataset name: {name} — '
    'sample: {sample}</label>'.format(
        tid=ut, r=color_of[ut][2], g=color_of[ut][1], b=color_of[ut][0],
        n=gaia_count, name=name, sample=' '.join(plist))
    for ut, name, gaia_count, plist in rows)

html = '''<!doctype html>
<html><head><meta charset="utf-8"><title>Unknown map objects - identify them</title>
<style>
body { background: #101410; color: #cfd8cf; font: 14px/1.5 system-ui; margin: 0;
       display: flex; }
#mapwrap { position: relative; width: 960px; height: 960px; flex: 0 0 960px; }
#mapimg { width: 960px; height: 960px; image-rendering: pixelated; }
.mk { position: absolute; width: 18px; height: 18px; border-radius: 50%;
      border: 1px solid #000; transform: translate(-9px, -9px); opacity: .85; }
.mkl { position: absolute; color: #000; font: bold 11px monospace;
       transform: translate(-10px, -22px); text-shadow: 0 0 2px #fff; }
#side { padding: 16px; }
.urow { display: block; margin: 10px 0; padding: 8px; background: #1a221a;
        border: 1px solid #2c352c; border-radius: 4px; cursor: pointer; }
.urow input { margin-right: 8px; }
.dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%;
       margin-right: 6px; vertical-align: middle; }
h1 { font-size: 18px; } p { color: #8aa08a; }
</style></head><body>
<div id="mapwrap"><img id="mapimg" src="unknown_map.png"></div>
<div id="side"><h1>Unknown map objects — identify them</h1>
<p>Each colored marker on the map is one object of that type (game-start
area). Open the replay in-game, seek to ~0:30, fly the camera to a marker
position and look. Check the box for every type that turns out to be
FISH.</p>
__ROWS__
</div>
<script>
const pts = __PTS__;
const wrap = document.getElementById('mapwrap');
const D = 231, s2 = 960 / (2 * D);
for (const [ut, plist] of Object.entries(pts)) {
    plist.forEach((p, i) => {
        const [x, y] = p.split(',').map(Number);
        const sx = (x + y) * s2, sy = (231 - x + y) * s2;
        const dot = document.createElement('div');
        dot.className = 'mk'; dot.style.left = sx + 'px'; dot.style.top = sy + 'px';
        const colors = __COLORS__;
        dot.style.background = colors[ut] || '#888';
        wrap.appendChild(dot);
    });
}
document.querySelectorAll('.urow input').forEach(cb => {
    cb.onchange = () => {
        document.querySelectorAll('.mk[data-type="' + cb.dataset.type + '"]')
            .forEach(mk => mk.style.opacity = cb.checked ? 1 : .15);
    };
});
</script>
</body></html>'''

# embed the positions + marker colors as JSON for the JS markers
pts_json = json.dumps({ut: unk[ut] for ut in unknown_ids})
colors_css = json.dumps({u: 'rgb(%d,%d,%d)' % (color_of[u][2], color_of[u][1],
                                               color_of[u][0])
                         for u in unknown_ids})
html = html.replace('__ROWS__', html_rows)
html = html.replace('__PTS__', pts_json)
html = html.replace('__COLORS__', colors_css)
open(OUT_HTML, 'w', encoding='utf-8').write(html)
print('wrote', OUT_HTML, 'and', OUT_IMG)
print('unknown types:', [(ut, len(unk[ut]), gaia.get('0:' + ut, 0))
                         for ut in unknown_ids])
