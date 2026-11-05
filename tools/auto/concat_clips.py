"""Concatenate each replay's highlight clips into one video per replay.

All clips share the same encode parameters (1920x1080 mp4v 30 fps video,
AAC 48 kHz stereo audio, same writer + mux), so the ffmpeg concat
demuxer with `-c copy` is enough - no re-encode, no quality loss.

Clips are chronological (NN index in the filename), so sorted order is
the timeline order. Time gaps between segments (seek-skipped stretches)
become jump cuts.

Layout: out/<tag>/clips/*.mp4 -> out/highlights_<tag>.mp4 (the only
files at the out root). Scans every replay folder; pass --tag to
rebuild just one on demand.

Usage:
    py -3.12 tools\\auto\\concat_clips.py [--tag mp5]
"""
import argparse
import subprocess
from pathlib import Path

import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = Path(__file__).resolve().parents[2] / "out"


def concat(tag_dir):
    clips = sorted((tag_dir / "clips").glob("*.mp4"))
    if not clips:
        return
    tag = tag_dir.name
    out = OUT / f"highlights_{tag}.mp4"
    lst = tag_dir / "concat.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            rel = c.relative_to(OUT).as_posix()
            f.write(f"file '{rel}'\n")
    r = subprocess.run(
        [FF, "-y", "-f", "concat", "-safe", "0", "-i",
         str(lst.relative_to(OUT)), "-c", "copy", out.name],
        cwd=str(OUT), capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        print(tag, "FAIL:", r.stderr.strip().splitlines()[-1])
        return
    pr = subprocess.run([FF, "-i", out], capture_output=True, text=True)
    dur = [l.strip() for l in pr.stderr.splitlines()
           if "Duration" in l][0][12:23]
    audio = "aac" if "aac" in pr.stderr else "NO AUDIO"
    mb = out.stat().st_size // 1048576
    print(f"{tag}: {len(clips)} clips -> {out.name} {dur} {audio} {mb} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None,
                    help="only this replay tag (default: all folders "
                         "under out/ that have clips)")
    args = ap.parse_args()
    dirs = sorted(p for p in OUT.iterdir() if p.is_dir())
    if args.tag:
        dirs = [d for d in dirs if d.name == args.tag]
        if not dirs:
            raise SystemExit(f"no out\\{args.tag}\\ folder")
    for tag_dir in dirs:
        concat(tag_dir)


if __name__ == "__main__":
    main()

