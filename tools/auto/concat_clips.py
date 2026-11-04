"""Concatenate each replay's highlight clips into one video per replay.

All clips share the same encode parameters (1920x1080 mp4v 30 fps video,
AAC 48 kHz stereo audio, same writer + mux), so the ffmpeg concat demuxer
with `-c copy` is enough - no re-encode, no quality loss.

Segments are chronological in each clips_<tag> folder (NN index in the
filename), so sorted order is the timeline order. Time gaps between
segments (seek-skipped stretches) become jump cuts.

Output: out/highlights_<tag>.mp4 for every out/clips_<tag> folder.
"""
import glob
import os
import subprocess
from pathlib import Path

import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
HERE = Path(__file__).parent
OUT = HERE.parents[1] / "out"

for tag_dir in sorted(glob.glob(str(OUT / "clips_*"))):
    tag = os.path.basename(tag_dir)[len("clips_"):]
    clips = sorted(glob.glob(os.path.join(tag_dir, "*.mp4")))
    if not clips:
        print(tag, "no clips; skip")
        continue
    out = OUT / f"highlights_{tag}.mp4"
    lst = OUT / f"concat_{tag}.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            rel = Path(c).relative_to(OUT).as_posix()
            f.write(f"file '{rel}'\n")
    r = subprocess.run(
        [FF, "-y", "-f", "concat", "-safe", "0", "-i",
         os.path.basename(lst), "-c", "copy", os.path.basename(out)],
        cwd=str(OUT), capture_output=True, text=True)
    if r.returncode != 0:
        print(tag, "FAIL:", r.stderr.strip().splitlines()[-1])
        continue
    pr = subprocess.run([FF, "-i", out], capture_output=True, text=True)
    dur = [l.strip() for l in pr.stderr.splitlines()
           if "Duration" in l][0][12:23]
    audio = "aac" if "aac" in pr.stderr else "NO AUDIO"
    mb = out.stat().st_size // 1048576
    print(f"{tag}: {len(clips)} clips -> {out.name} {dur} {audio} {mb} MB")
    os.remove(lst)