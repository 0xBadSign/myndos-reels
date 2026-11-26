"""Instagram Graph API (Instagram Login) helper. Never prints the access token.

  python tools/ig.py check            account name and token lifetime
  python tools/ig.py post <app>       publish the oldest unposted reel in <app>/queue
  python tools/ig.py post <app> --dry show what would be posted
  python tools/ig.py stats            followers and per-reel views, reach, likes, comments, shares, saves

Env: IG_ACCESS_TOKEN, IG_USER_ID, GITHUB_REPOSITORY, GITHUB_SHA (set by Actions).
Videos are served to Instagram from this public repository through jsDelivr, pinned to the commit.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("IG_USER_ID", "").strip()
# Instagram Login tokens start with "IG" and use graph.instagram.com; Facebook Login for Business
# (system-user) tokens use graph.facebook.com. The publishing calls are the same on both.
INSTAGRAM_LOGIN = TOKEN.startswith("IG")
BASE = "https://graph.instagram.com/v23.0" if INSTAGRAM_LOGIN else "https://graph.facebook.com/v23.0"
ROOT = Path(__file__).resolve().parent.parent


def call(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params)
    url = path if path.startswith("http") else f"{BASE}/{path}"
    if method == "GET":
        req = urllib.request.Request(f"{url}?{data}")
    else:
        req = urllib.request.Request(url, data=data.encode(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if TOKEN:
            body = body.replace(TOKEN, "***")
        raise SystemExit(f"HTTP {e.code} on {path}: {body}")


def check():
    if not TOKEN:
        raise SystemExit("IG_ACCESS_TOKEN is empty")
    print("token kind:", "Instagram Login" if INSTAGRAM_LOGIN else "Facebook Login / system user", "| length", len(TOKEN))
    if INSTAGRAM_LOGIN:
        me = call("GET", "me", fields="user_id,username,account_type,media_count")
        print("account:", me.get("username"), "| type:", me.get("account_type"), "| media:", me.get("media_count"), "| id:", me.get("user_id"))
        if USER_ID and str(me.get("user_id")) != USER_ID:
            print("note: IG_USER_ID differs from the token's account:", me.get("user_id"))
    else:
        me = call("GET", USER_ID, fields="username,media_count")
        print("account:", me.get("username"), "| media:", me.get("media_count"), "| id:", USER_ID)
        return
    # A long-lived Instagram Login token lasts 60 days and can be refreshed; a system-user token never expires.
    try:
        r = call("GET", "https://graph.instagram.com/refresh_access_token", grant_type="ig_refresh_token")
        same = r.get("access_token") == TOKEN
        print(f"token refresh: expires in {int(r.get('expires_in', 0)) // 86400} days, same token: {same}")
    except SystemExit as e:
        print("token refresh not available (fine for a never-expiring token):", str(e)[:200])


def next_reel(app):
    queue = ROOT / app / "queue"
    posted_file = ROOT / app / "posted.txt"
    posted = set(posted_file.read_text(encoding="utf-8").split()) if posted_file.exists() else set()
    for video in sorted(queue.glob("*.mp4")):
        if video.stem not in posted:
            return video, posted_file
    return None, posted_file


def post(app, dry=False):
    video, posted_file = next_reel(app)
    if video is None:
        print(f"{app}: queue is empty, nothing to post")
        return
    caption_file = video.with_suffix(".txt")
    caption = caption_file.read_text(encoding="utf-8").strip() if caption_file.exists() else ""
    repo = os.environ.get("GITHUB_REPOSITORY", "0xBadSign/myndos-reels")
    ref = os.environ.get("GITHUB_SHA", "main")
    rel = video.relative_to(ROOT).as_posix()
    video_url = f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/{urllib.parse.quote(rel)}"
    print(f"{app}: posting {video.name}\n  url: {video_url}\n  caption: {caption[:80]!r}...")
    if dry:
        return
    if not TOKEN or not USER_ID:
        raise SystemExit("IG_ACCESS_TOKEN / IG_USER_ID missing")
    # The schedule fires more than once a day as a fallback, because GitHub drops scheduled runs.
    # A scheduled run posts only if nothing was posted in the last 20 hours.
    if os.environ.get("POST_GUARD") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        times = []
        # Trial reels may not show in the media list, so the time of our own last post is kept in the repo too.
        stamp = ROOT / app / "last_posted_at.txt"
        if stamp.exists():
            times.append(datetime.fromtimestamp(int(stamp.read_text().strip()), timezone.utc))
        last = call("GET", f"{USER_ID}/media", fields="timestamp", limit=1).get("data", [])
        if last:
            times.append(datetime.strptime(last[0]["timestamp"], "%Y-%m-%dT%H:%M:%S%z"))
        if times and now - max(times) < timedelta(hours=20):
            print(f"{app}: posted less than 20 hours ago, skipping this scheduled run")
            return

    # Trial reels go to non-followers first; Instagram shares them to followers on its own when they do well
    # (graduation_strategy SS_PERFORMANCE). IG_TRIAL=0 turns this off. If the API refuses the trial, post a normal reel.
    params = dict(media_type="REELS", video_url=video_url, caption=caption)
    container = None
    if os.environ.get("IG_TRIAL", "1") != "0":
        try:
            container = call("POST", f"{USER_ID}/media", trial_params=json.dumps({"graduation_strategy": "SS_PERFORMANCE"}),
                             **params)["id"]
            print(f"{app}: trial reel container {container}")
        except SystemExit as e:
            print(f"{app}: trial reel refused, posting a normal reel instead: {str(e)[:300]}")
    if container is None:
        container = call("POST", f"{USER_ID}/media", share_to_feed="true", **params)["id"]
    for _ in range(60):  # up to 10 minutes
        time.sleep(10)
        status = call("GET", container, fields="status_code,status")
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"container {code}: {status.get('status')}")
    else:
        raise SystemExit("container did not finish in 10 minutes")
    media = call("POST", f"{USER_ID}/media_publish", creation_id=container)["id"]
    print(f"{app}: published media {media}")
    with posted_file.open("a", encoding="utf-8") as f:
        f.write(video.stem + "\n")
    (ROOT / app / "last_posted_at.txt").write_text(str(int(time.time())) + "\n")


def stats():
    if not TOKEN or not USER_ID:
        raise SystemExit("IG_ACCESS_TOKEN / IG_USER_ID missing")
    me = call("GET", USER_ID, fields="username,followers_count,media_count")
    print(f"account: {me.get('username')} | followers: {me.get('followers_count')} | media: {me.get('media_count')}")
    media = call("GET", f"{USER_ID}/media", fields="id,caption,timestamp,media_product_type,like_count,comments_count", limit=50).get("data", [])
    print("date        views   reach  likes  comm  share  saved  avg_watch_s  caption")
    insight_error = ""
    for m in media:
        vals = {}
        for metric in ("views", "reach", "shares", "saved", "ig_reels_avg_watch_time"):
            try:
                r = call("GET", f"{m['id']}/insights", metric=metric)
                vals[metric] = r["data"][0]["values"][0]["value"]
            except SystemExit as e:
                vals[metric] = "-"
                if not insight_error:
                    insight_error = str(e)[:300]
        watch = vals["ig_reels_avg_watch_time"]
        watch = f"{watch / 1000:.1f}" if isinstance(watch, (int, float)) else "-"
        cap = (m.get("caption") or "").splitlines()[0][:50] if m.get("caption") else ""
        print(f"{m['timestamp'][:10]}  {vals['views']!s:>6}  {vals['reach']!s:>6}  {m.get('like_count', 0):>5}  {m.get('comments_count', 0):>4}  "
              f"{vals['shares']!s:>5}  {vals['saved']!s:>5}  {watch:>11}  {cap}")
    if insight_error:
        print("first insights error:", insight_error)


if __name__ == "__main__":
    args = sys.argv[1:] or ["check"]
    if args[0] == "check":
        check()
    elif args[0] == "stats":
        stats()
    elif args[0] == "post" and len(args) >= 2:
        post(args[1], dry="--dry" in args)
    else:
        raise SystemExit(__doc__)
