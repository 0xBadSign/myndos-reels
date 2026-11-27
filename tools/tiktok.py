"""TikTok Content Posting API (v2) helper for the "Myndos Publisher" app. Never prints tokens.

  python tools/tiktok.py check <app>         account name, privacy options, max video length
  python tools/tiktok.py post <app>          post the oldest video in <app>/queue not in <app>/posted_tiktok.txt
  python tools/tiktok.py post <app> --dry    show what would be posted (no network)

Env: PUBLISHER_KEY (shared with the Worker), PUBLISHER_URL (default https://myndos.studio/publisher/tiktok/token),
TIKTOK_MODE = inbox (default, upload as a draft) | direct, TIKTOK_PRIVACY (direct mode, default SELF_ONLY), POST_GUARD=1.
The access token is fetched from the Worker, which keeps and refreshes it.
"""
import json
import re
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://open.tiktokapis.com/v2"
PUBLISHER_URL = os.environ.get("PUBLISHER_URL", "https://myndos.studio/publisher/tiktok/token")
PUBLISHER_KEY = os.environ.get("PUBLISHER_KEY", "").strip()
MODE = os.environ.get("TIKTOK_MODE", "inbox").strip() or "inbox"
CHUNK = 32 * 1024 * 1024  # chunks must be 5-64 MB; files up to 64 MB go up in one piece
SINGLE_MAX = 64 * 1024 * 1024
_secrets = []  # token values to redact from any error text


def http(method, url, body=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "myndos-publisher/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"{}"))
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        for s in _secrets:
            text = text.replace(s, "***")
        raise SystemExit(f"HTTP {e.code} on {url.split('?')[0]}: {text}")


def api(token, path, payload):
    _, j = http("POST", f"{API}/{path}", json.dumps(payload).encode(),
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    err = j.get("error") or {}
    if err.get("code", "ok") != "ok":
        raise SystemExit(f"TikTok error on {path}: {json.dumps(err)}")
    return j.get("data") or {}


def get_token(app):
    if not PUBLISHER_KEY:
        raise SystemExit("PUBLISHER_KEY is empty")
    _, j = http("POST", PUBLISHER_URL, json.dumps({"account": app}).encode(),
                {"Authorization": f"Bearer {PUBLISHER_KEY}", "Content-Type": "application/json"})
    _secrets.append(j["access_token"])
    return j["access_token"]


def creator_info(token):
    return api(token, "post/publish/creator_info/query/", {})


def check(app):
    info = creator_info(get_token(app))
    print(f"account: {info.get('creator_nickname')} (@{info.get('creator_username')})")
    print("privacy options:", ", ".join(info.get("privacy_level_options", [])))
    print("max video duration:", info.get("max_video_post_duration_sec"), "s")


def next_video(app):
    posted_file = ROOT / app / "posted_tiktok.txt"
    posted = set(posted_file.read_text(encoding="utf-8").split()) if posted_file.exists() else set()
    for video in sorted((ROOT / app / "queue").glob("*.mp4")):
        if video.stem not in posted:
            return video, posted_file
    return None, posted_file


def tiktok_caption(video):
    f = video.with_suffix(".txt")
    text = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    # Instagram-only calls to action make no sense here.
    lines = [l for l in text.splitlines()
             if not any(k in l for k in ("DM", "Yoruma TOLK yaz", "link in bio", "profilde"))]
    suffix = "\n\n\U0001F517 Uygulama linki profilde."
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return body[:2200 - len(suffix)].rstrip() + suffix


def chunk_plan(size):
    """(chunk_size, total_chunk_count) per the media transfer guide; the last chunk absorbs the remainder."""
    if size <= SINGLE_MAX:
        return size, 1
    return CHUNK, size // CHUNK


def upload(upload_url, path, size, chunk_size, count):
    with path.open("rb") as f:
        for i in range(count):
            start = i * chunk_size
            n = size - start if i == count - 1 else chunk_size
            f.seek(start)
            status, _ = http("PUT", upload_url, f.read(n), {
                "Content-Type": "video/mp4", "Content-Length": str(n),
                "Content-Range": f"bytes {start}-{start + n - 1}/{size}"}, raw=True)
            print(f"  chunk {i + 1}/{count}: HTTP {status}")


def post(app, dry=False):
    video, posted_file = next_video(app)
    if video is None:
        print(f"{app}: queue is empty, nothing to post")
        return
    size = video.stat().st_size
    chunk_size, count = chunk_plan(size)
    caption = tiktok_caption(video)
    print(f"{app}: {video.name}, {size} bytes, mode {MODE}\n  chunks: {count} x {chunk_size} bytes\n  caption ({len(caption)} chars):\n{caption}")
    if dry:
        return
    stamp = ROOT / app / "tiktok_last_posted_at.txt"
    if os.environ.get("POST_GUARD") == "1" and stamp.exists() and time.time() - int(stamp.read_text().strip()) < 20 * 3600:
        print(f"{app}: posted less than 20 hours ago, skipping")
        return
    token = get_token(app)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}
    if MODE == "direct":
        options = creator_info(token).get("privacy_level_options", [])
        want = os.environ.get("TIKTOK_PRIVACY", "").strip()
        privacy = want if want in options else "SELF_ONLY"
        print(f"  privacy: {privacy} (options: {', '.join(options)})")
        init = api(token, "post/publish/video/init/", {
            "post_info": {"title": caption, "privacy_level": privacy, "disable_comment": False, "disable_duet": False,
                          "disable_stitch": False, "brand_organic_toggle": True, "brand_content_toggle": False},
            "source_info": source})
        done = "PUBLISH_COMPLETE"
    else:
        init = api(token, "post/publish/inbox/video/init/", {"source_info": source})
        done = "SEND_TO_USER_INBOX"
    publish_id = init["publish_id"]
    print(f"{app}: publish_id {publish_id}, uploading")
    upload(init["upload_url"], video, size, chunk_size, count)
    status = None
    for _ in range(60):  # up to 5 minutes
        time.sleep(5)
        data = api(token, "post/publish/status/fetch/", {"publish_id": publish_id})
        status = data.get("status")
        if status == done:
            break
        if status == "FAILED":
            raise SystemExit(f"TikTok FAILED: {data.get('fail_reason')}")
    else:
        raise SystemExit(f"no final status after 5 minutes (last: {status})")
    print(f"{app}: {status}")
    with posted_file.open("a", encoding="utf-8") as f:
        f.write(video.stem + "\n")
    stamp.write_text(str(int(time.time())) + "\n")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "check":
        check(a[1])
    elif len(a) >= 2 and a[0] == "post":
        post(a[1], dry="--dry" in a)
    else:
        raise SystemExit(__doc__)
