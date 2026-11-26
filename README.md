# myndos-reels

Scheduled Reels for Myndos Studio apps. Each app has a `queue/` of finished videos (`NNN-name.mp4`) with a caption next to it (`NNN-name.txt`). The daily workflow posts the oldest unposted one and records it in `posted.txt`.

Footage is reused under the licenses named in each caption (CC BY / CC0 via Wikimedia Commons).

## Tolk reel maker

`tools/make_reels.py` turns raw phone clips into up to 5 reel variants each (1080x1920, hook text for the first 3.5 s, "Sıradaki ne olsun? Yoruma yaz" for the last 2.5 s, never mirrored). Drop `*.mp4|*.mov` (optionally with `<name>.json`: `{"hooks": [...], "caption": "...", "start": 0, "end": 15}`) into `tolk/raw/`, then:

    python tools/make_reels.py --dry          # show the plan
    python tools/make_reels.py                # render into tolk/queue as NNNv-<slug>-<k>.mp4 + .txt
    python tools/make_reels.py --only <name> --variants 3

Processed clips move to `tolk/raw/done/`. Needs ffmpeg/ffprobe on PATH and the Windows font Segoe UI Black.
