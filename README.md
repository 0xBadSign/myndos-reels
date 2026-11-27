# myndos-reels

Scheduled Reels for Myndos Studio apps. Each app has a `queue/` of finished videos (`NNN-name.mp4`) with a caption next to it (`NNN-name.txt`). The daily workflow posts the oldest unposted one and records it in `posted.txt`.

Footage is reused under the licenses named in each caption (CC BY / CC0 via Wikimedia Commons).

## Tolk reel maker

`tools/make_reels.py` turns raw phone clips into up to 5 reel variants each (1080x1920, hook text for the first 3.5 s, "Sıradaki ne olsun? Yoruma yaz" for the last 2.5 s, never mirrored). Drop `*.mp4|*.mov` (optionally with `<name>.json`: `{"hooks": [...], "caption": "...", "start": 0, "end": 15}`) into `tolk/raw/`, then:

    python tools/make_reels.py --dry          # show the plan
    python tools/make_reels.py                # render into tolk/queue as NNNv-<slug>-<k>.mp4 + .txt
    python tools/make_reels.py --only <name> --variants 3

Processed clips move to `tolk/raw/done/`. Needs ffmpeg/ffprobe on PATH and the Windows font Segoe UI Black.

## TikTok

`tools/tiktok.py` posts the oldest video not in `<app>/posted_tiktok.txt` through TikTok's Content Posting API, using the "Myndos Publisher" developer app (one connected account per product). The Worker in `cron/` keeps the OAuth tokens in KV and starts `tiktok.yml` daily after `post.yml`. One-time setup:

1. `cd cron && wrangler kv namespace create TOKENS`, paste the id into `wrangler.toml`.
2. `wrangler secret put TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` and `PUBLISHER_KEY` (any long random string).
3. Set the repo secret `PUBLISHER_KEY` to the same value. Optional repo variables: `TIKTOK_MODE`, `TIKTOK_PRIVACY`.
4. `wrangler deploy`, then open `https://myndos.studio/publisher/tiktok/login?account=tolk` while signed in to the Tolk TikTok account.
5. Run the TikTok workflow with `check`, then `dry`.

Modes (`TIKTOK_MODE`): `inbox` (default) uploads a draft to the creator's TikTok inbox and you finish it in the app. `direct` posts by itself with `TIKTOK_PRIVACY` (default `SELF_ONLY`); it needs TikTok's app audit, and until then every direct post is private.
