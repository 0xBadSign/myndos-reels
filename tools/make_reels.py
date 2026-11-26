"""Turn raw phone clips into Instagram reel variants for Tolk.

  python tools/make_reels.py                    process every clip in tolk/raw
  python tools/make_reels.py --only clip1       one clip (name without extension)
  python tools/make_reels.py --dry              print the plan, render nothing
  python tools/make_reels.py --raw DIR --out DIR --variants 5

Clips: *.mp4 / *.mov, optional sidecar <name>.json:
  {"hooks": ["...", "..."], "caption": "...", "start": 0.0, "end": 15.0}
Output: NNNv-<slug>-<k>.mp4 + .txt in the queue folder (NNN continues after the highest
3-digit prefix already there). Processed clips move to <raw>/done/.
Never mirrors (it would reverse the burned-in subtitles in the footage).
Needs ffmpeg and ffprobe on PATH.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = "C\\:/Windows/Fonts/seguibl.ttf"  # drawtext-escaped colon
MAX_SECONDS = 60.0
HOOK_SECONDS = 3.5
END_SECONDS = 2.5
END_TEXT = "Sıradaki ne olsun? Yoruma yaz"
WRAP = 22
FONT_SIZE = 64
Y_TOP = 260
Y_LOWER = 1300

DEFAULT_HOOKS = [
    "Altyazısı olmayan videoya canlı Türkçe altyazı",
    "İngilizce bilmeyenlerin gizli silahı",
    "Yabancı diziyi altyazısız izlemenin yolu",
]
DEFAULT_CAPTION_TAIL = (
    "\n\n💬 Yoruma TOLK yaz, App Store linkini DM'den gönderelim."
    "\n\n#altyazı #ingilizce #yabancıdizi #iphone #tolk"
)

PLAN = {
    1: dict(hook=0, desc="hook 1, normal"),
    2: dict(hook=1, zoom=1.08, desc="hook 2, zoom 8%"),
    3: dict(hook=0, speed=1.1, desc="hook 1, speed 1.1x"),
    4: dict(hook=2, trim=9.0, desc="hook 3, first 9 s"),
    5: dict(hook=0, lower=True, eq="eq=brightness=0.03:saturation=1.08", desc="hook 1, lower third, color tweak"),
}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", str(path)])
    d = json.loads(r.stdout or "{}")
    dur = float(d.get("format", {}).get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return dur, has_audio


def slugify(s):
    s = s.replace("ı", "i").replace("İ", "i")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "clip"


def next_number(out_dir):
    nums = [int(m.group(1)) for p in out_dir.glob("*") if (m := re.match(r"(\d{3})", p.name))]
    return max(nums, default=0) + 1


def wrap(text):
    return "\n".join(textwrap.wrap(text, WRAP, break_long_words=False)) or text


def drawtext(txtfile, y, enable):
    return (f"drawtext=fontfile='{FONT}':textfile={txtfile}:fontcolor=white:fontsize={FONT_SIZE}"
            f":box=1:boxcolor=black@0.75:boxborderw=24:line_spacing=10:text_align=center"
            f":x=(w-text_w)/2:y={y}:enable='{enable}'")


def build(v, hook, win, has_audio, tmp, tag):
    """Return ffmpeg args placed after the input."""
    dur = win
    if v.get("trim"):
        dur = min(dur, v["trim"])
    speed = v.get("speed", 1.0)
    out_dur = dur / speed
    hf, ef = tmp / f"{tag}_hook.txt", tmp / f"{tag}_end.txt"
    hf.write_bytes(wrap(hook).encode("utf-8"))
    ef.write_bytes(wrap(END_TEXT).encode("utf-8"))
    z = v.get("zoom", 1.0)
    vf = [f"scale={round(1080 * z)}:{round(1920 * z)}:force_original_aspect_ratio=increase",
          "crop=1080:1920", "fps=30"]
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed}")
    if v.get("eq"):
        vf.append(v["eq"])
    y = Y_LOWER if v.get("lower") else Y_TOP
    vf.append(drawtext(hf.name, y, f"lt(t,{HOOK_SECONDS})"))
    vf.append(drawtext(ef.name, y, f"gte(t,{max(out_dur - END_SECONDS, 0):.2f})"))
    vf.append("format=yuv420p")
    args = ["-t", f"{dur:.3f}", "-vf", ",".join(vf)]
    if not has_audio:
        args += ["-an"]
    elif speed != 1.0:
        args += ["-af", f"atempo={speed}"]
    return args


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(ROOT / "tolk" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "tolk" / "queue"))
    ap.add_argument("--variants", type=int, default=5)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    clips = sorted(p for p in raw.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mov"))
    if a.only:
        clips = [p for p in clips if p.stem == a.only or p.name == a.only]
    if not clips:
        print("no clips found in", raw)
        return 0
    if not a.dry:
        out.mkdir(parents=True, exist_ok=True)
    num = next_number(out) if out.exists() else 1
    failed = 0
    for clip in clips:
        side = clip.with_suffix(".json")
        meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        hooks = meta.get("hooks") or DEFAULT_HOOKS
        total, has_audio = probe(clip)
        start = float(meta.get("start", 0) or 0)
        end = float(meta.get("end", total) or total)
        win = min(max(min(end, total) - start, 0), MAX_SECONDS)
        if win <= 0:
            print(f"skip {clip.name}: empty time window")
            failed += 1
            continue
        slug = slugify(clip.stem)
        print(f"{clip.name}: {win:.1f}s window, audio={has_audio}, no={num:03d}v")
        ok = True
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for k in range(1, a.variants + 1):
                v = PLAN.get(k)
                if not v:
                    break
                hook = hooks[v["hook"] % len(hooks)]
                name = f"{num:03d}v-{slug}-{k}"
                print(f"  {name}: {v['desc']} | {hook!r}")
                if a.dry:
                    continue
                args = build(v, hook, win, has_audio, tmp, f"v{k}")
                cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(clip.resolve())] + args + [
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-r", "30",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str((out / f"{name}.mp4").resolve())]
                r = run(cmd, cwd=tmp)  # cwd=tmp so drawtext textfile names stay relative
                if r.returncode != 0:
                    print("  ffmpeg failed:", r.stderr.strip()[-600:])
                    ok = False
                    break
                caption = meta.get("caption") or (hook + DEFAULT_CAPTION_TAIL)
                (out / f"{name}.txt").write_text(caption, encoding="utf-8")
        if not a.dry and ok:
            done = raw / "done"
            done.mkdir(exist_ok=True)
            shutil.move(str(clip), str(done / clip.name))
            if side.exists():
                shutil.move(str(side), str(done / side.name))
        if not ok:
            failed += 1
        else:
            num += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
