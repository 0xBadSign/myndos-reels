"""Auto DM: private-reply the App Store link to people who comment a keyword on our reels.

  python tools/autodm.py run <app>        send DMs for new matching comments
  python tools/autodm.py run <app> --dry  show what would be sent, send and write nothing

Env: IG_ACCESS_TOKEN, IG_USER_ID. Message text: <app>/dm.txt. Sent comment ids: <app>/dm_sent.txt
(first token on each line is the comment id, an optional trailing note such as "skip" may follow).
Private replies are only allowed within 7 days of the comment.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import ig

KEYWORDS = {"tolk", "link"}
MAX_AGE = timedelta(days=7)
MAX_DMS = 20
MAX_COMMENTS = 200
PUBLIC_REPLY = "Sent you the link in DM 📩"
# Errors meaning this comment can never be answered (too old, deleted, already replied, user unreachable).
# Not 10 or 100: those also mean a missing permission, which must not mark comments as done.
SKIP_CODES = {551}
SKIP_SUBCODES = {2534022, 2534014, 2534023, 2018001, 2018108}


def post_json(path, body, token=None):
    token = token or ig.TOKEN
    url = f"{ig.BASE}/{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        for t in (ig.TOKEN, token):
            if t:
                raw = raw.replace(t, "***")
        try:
            err = json.loads(raw).get("error", {})
        except ValueError:
            err = {}
        raise ApiError(e.code, raw, err.get("code"), err.get("error_subcode"), path)


class ApiError(Exception):
    def __init__(self, status, body, code, subcode, path):
        super().__init__(f"HTTP {status} on {path}: {body}")
        self.code, self.subcode = code, subcode

    def permanent(self):
        return self.code in SKIP_CODES or self.subcode in SKIP_SUBCODES


def dm_sender():
    """Where private replies are sent from. Instagram Login: the Instagram account itself.
    Facebook Login: the Facebook Page linked to the Instagram account, with that Page's token."""
    if ig.INSTAGRAM_LOGIN:
        return ig.USER_ID, None
    pages = ig.call("GET", "me/accounts", fields="id,access_token,instagram_business_account", limit=100).get("data", [])
    for p in pages:
        if str((p.get("instagram_business_account") or {}).get("id")) == ig.USER_ID:
            return p["id"], p.get("access_token")
    raise SystemExit("no Facebook Page linked to this Instagram account is visible to the token")


def parse_time(s):
    # Instagram returns e.g. 2026-11-17T09:00:00+0000 (no colon in the offset)
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def matches(text):
    words = set(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())
    return bool(words & KEYWORDS)


def comments_of(media_id):
    out = []
    page = ig.call("GET", f"{media_id}/comments", fields="id,text,timestamp,from{id,username}", limit=50)
    while True:
        out += page.get("data", [])
        nxt = page.get("paging", {}).get("next")
        if not nxt or len(out) >= MAX_COMMENTS:
            return out[:MAX_COMMENTS]
        page = ig.call("GET", nxt)  # next url already carries its own parameters


def run(app, dry=False):
    if not ig.TOKEN or not ig.USER_ID:
        raise SystemExit("IG_ACCESS_TOKEN / IG_USER_ID missing, set them in the environment")
    dm_file = ig.ROOT / app / "dm.txt"
    if not dm_file.exists():
        raise SystemExit(f"{dm_file} not found")
    text = dm_file.read_text(encoding="utf-8").strip()
    sent_file = ig.ROOT / app / "dm_sent.txt"
    done = {l.split()[0] for l in sent_file.read_text(encoding="utf-8").splitlines() if l.strip()} if sent_file.exists() else set()

    me = ig.call("GET", ig.USER_ID, fields="username")  # works with both Instagram and Facebook Login tokens
    own_name = (me.get("username") or "").lower()
    cutoff = datetime.now(timezone.utc) - MAX_AGE
    media = [m for m in ig.call("GET", f"{ig.USER_ID}/media", fields="id,timestamp,permalink", limit=15).get("data", [])
             if parse_time(m["timestamp"]) >= cutoff]

    sender, sender_token = (None, None) if dry else dm_sender()
    checked = found = sent = skipped = 0
    for m in media:
        checked += 1
        for c in comments_of(m["id"]):
            cid = c["id"]
            who = c.get("from") or {}
            if not matches(c.get("text")) or cid in done:
                continue
            if str(who.get("id")) == ig.USER_ID or (who.get("username") or "").lower() == own_name:
                continue
            if parse_time(c["timestamp"]) < cutoff:
                continue
            found += 1
            if dry:
                print(f"[dry] would DM @{who.get('username')} for comment {cid}: {c.get('text')!r}")
                continue
            if sent >= MAX_DMS:
                skipped += 1
                continue
            try:
                post_json(f"{sender}/messages", {"recipient": {"comment_id": cid}, "message": {"text": text}}, sender_token)
            except ApiError as e:
                print(f"DM failed for comment {cid}: {e}")
                if e.permanent():
                    with sent_file.open("a", encoding="utf-8") as f:
                        f.write(f"{cid} skip\n")
                skipped += 1
                continue
            with sent_file.open("a", encoding="utf-8") as f:
                f.write(cid + "\n")
            done.add(cid)
            sent += 1
            print(f"DM sent to @{who.get('username')} for comment {cid}")
            try:
                ig.call("POST", f"{cid}/replies", message=PUBLIC_REPLY)
            except SystemExit as e:
                print(f"public reply failed for {cid}: {e}")
    print(f"{app}: media checked {checked}, matches {found}, sent {sent}, skipped {skipped}" + (" (dry)" if dry else ""))


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "run":
        run(args[1], dry="--dry" in args)
    else:
        raise SystemExit(__doc__)
