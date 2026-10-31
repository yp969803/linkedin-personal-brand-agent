#!/usr/bin/env python3
"""State, caps, schedule and draft checks for the LinkedIn growth skill.

Text, notes and anything copied from LinkedIn must go in on stdin through a quoted
heredoc (<<'EOF'), never as shell arguments. URLs go in single quotes.

  state.py decide [--dry-run]   -> JSON: awake, reason, actions, remaining. Starts a session.
                                   --dry-run ignores the gates, never saves, always lists every action.
  state.py remaining            -> JSON of what is still allowed now (read-only; all 0 when paused)
  state.py record TYPE URL      -> log an action (post|comment|reply|connect|like).
                                   For post/comment/reply stdin is: line 1 = NOTE, the rest = the published TEXT.
                                   NOTE = "pillar: subtopic" (post) or "their post topic: my angle" (comment/reply).
  state.py seen URL             -> "yes" or "no" (post URLs match by activity id)
  state.py history              -> all posts and the last 40 comment/reply notes, oldest first
  state.py topic KIND           -> stdin = NOTE. "pass", or the clashing earlier notes (exit 1). Run before drafting.
  state.py lint KIND            -> stdin = TEXT. "pass", or "fail: reason" lines (exit 1). Also rejects repeats.
                                   KIND = post|comment (comment also covers replies)
  state.py pause | unpause      -> create / remove the PAUSE kill switch
  state.py home                 -> print the state directory (profile.md lives there)

State lives in $LINKEDIN_AGENT_HOME (default ~/.linkedin-agent). Times are the machine's local time.
"""
import difflib
import fcntl  # shortcut: POSIX only, no Windows support; use msvcrt locking if that ever matters
import json
import os
import random
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

CAPS = {"comment": 4, "connect": 3, "reply": 10, "like": 15}  # per day
WEEKLY_CONNECT = 15
SESSION = {"comment": 2, "connect": 2}  # per wake-up
SESSION_TTL = timedelta(hours=2)  # a wake-up this old no longer counts as a live session
MIN_GAP = timedelta(minutes=30)  # between the previous wake-up's last comment/connect and this one
POST_GAP = timedelta(hours=24)
ACTIVE_HOURS = range(8, 20)  # local time
POSTS_PER_WEEK = 2
AWAKE_CHANCE = 0.5
KINDS = {"post": ("post",), "comment": ("comment", "reply")}
TOPIC_WINDOW = {"post": None, "comment": 100}  # earlier entries a new note is compared with
STOP = {"the", "and", "for", "with", "that", "this", "what", "from", "have", "about", "into",
        "how", "why", "are", "you", "your", "was", "not", "but", "can", "our", "its"}

# "unlock" is left to the model (voice.md): it is also a real technical word (mutex unlock)
BANNED = [
    "delve", "game-changer", "game changer", "leverage", "landscape", "journey",
    "in today's", "let that sink in", "here's the thing", "i'm thrilled", "excited to share",
    "great post", "thanks for sharing", "this resonates", "great insights", "absolutely agree",
    "rapidly evolving", "dive deep", "dive in", "the future is here",
]


def home():
    return Path(os.environ.get("LINKEDIN_AGENT_HOME", Path.home() / ".linkedin-agent"))


def paused():
    return (home() / "PAUSE").exists()


def fresh():
    return {"week": "", "slots": [], "log": [], "session_start": ""}


def load():
    p = home() / "state.json"
    try:
        st = json.loads(p.read_text())
    except FileNotFoundError:
        return fresh()
    except json.JSONDecodeError:
        bad = p.with_suffix(".corrupt")
        p.rename(bad)
        sys.exit(f"state file was corrupt; moved to {bad}. Run again to start fresh.")
    for k, v in fresh().items():
        st.setdefault(k, v)
    return st


def save(st):
    home().mkdir(parents=True, exist_ok=True)
    tmp = home() / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, home() / "state.json")  # atomic: readers never see half a file


@contextmanager
def locked():
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / "lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=None)


def week_id(now):
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def roll_week(st, now, rng):
    """New week: pick post slots only from the time that is left, and carry one missed post over."""
    if st["week"] == week_id(now):
        return
    carry = min(1, sum(1 for s in st["slots"] if not s["done"]))
    cands = [(d, h) for d in range(5) for h in range(8, 19) if (d, h) > (now.weekday(), now.hour)]
    days = sorted({d for d, _ in cands})
    n = POSTS_PER_WEEK - carry
    if len(days) >= n:
        picks = [(d, rng.choice([h for dd, h in cands if dd == d])) for d in sorted(rng.sample(days, n))]
    else:
        picks = sorted(rng.sample(cands, min(n, len(cands))))
    st["week"] = week_id(now)
    st["slots"] = [{"day": now.weekday(), "hour": now.hour, "done": False} for _ in range(carry)]
    st["slots"] += [{"day": d, "hour": h, "done": False} for d, h in picks]


def due(s, now):
    return not s["done"] and (s["day"], s["hour"]) <= (now.weekday(), now.hour)


def entries(st, kind, since):
    return [e for e in st["log"] if e["type"] == kind and e["t"] >= since]


def remaining(st, now):
    if paused():
        return {k: 0 for k in CAPS}
    day = now.date().isoformat()
    wk = (now.date() - timedelta(days=now.weekday())).isoformat()
    r = {k: CAPS[k] - len(entries(st, k, day)) for k in CAPS}
    r["connect"] = min(r["connect"], WEEKLY_CONNECT - len(entries(st, "connect", wk)))
    start = st.get("session_start")
    if not start or now - ts(start) > SESSION_TTL:
        r.update({k: 0 for k in SESSION})  # no live wake-up: decide must run first
    else:
        before = [ts(e["t"]) for e in st["log"] if e["type"] in SESSION and e["t"] < start]
        gap_blocked = bool(before) and ts(start) - max(before) < MIN_GAP
        for k, c in SESSION.items():
            r[k] = 0 if gap_blocked else min(r[k], c - len(entries(st, k, start)))
    return {k: max(0, n) for k, n in r.items()}


def decide(st, now, rng, dry=False):
    if dry:
        return {"awake": True, "reason": "dry run", "actions": ["post", *CAPS], "remaining": dict(CAPS)}
    roll_week(st, now, rng)
    posts = [ts(e["t"]) for e in st["log"] if e["type"] == "post"]
    post_due = any(due(s, now) for s in st["slots"]) and (not posts or now - max(posts) >= POST_GAP)
    out = {"awake": False, "reason": "", "actions": [], "week": st["week"]}
    if paused():
        out["reason"] = "paused"
    elif now.hour not in ACTIVE_HOURS:
        out["reason"] = "outside active hours"
    # a due post forces a wake-up, otherwise the weekly goal could be skipped by chance
    elif not post_due and rng.random() >= AWAKE_CHANCE:
        out["reason"] = "random skip"
    else:
        st["session_start"] = now.isoformat(timespec="seconds")
        out["awake"] = True
        out["actions"] = (["post"] if post_due else []) + [k for k, n in remaining(st, now).items() if n > 0]
    out["remaining"] = remaining(st, now)
    return out


def norm(url):
    m = re.search(r"(?:activity|ugcPost|share)[:-](\d{10,})", url)
    return "post:" + m.group(1) if m else url.split("?")[0].split("#")[0].rstrip("/").lower()


def record(st, now, kind, url, note="", text=""):
    e = {"t": now.isoformat(timespec="seconds"), "type": kind, "url": url}
    if note:
        e["note"] = note
    if text:
        e["text"] = text
    st["log"].append(e)
    if kind == "post":
        for s in st["slots"]:
            if due(s, now):  # a manual extra post must not use up a future slot
                s["done"] = True
                break


def seen(st, url):
    return any(norm(e["url"]) == norm(url) for e in st["log"])


def earlier(st, kind):
    es = [e for e in st["log"] if e["type"] in KINDS[kind]]
    return es if TOPIC_WINDOW[kind] is None else es[-TOPIC_WINDOW[kind]:]


def history(st):
    return earlier(st, "post") + [e for e in st["log"] if e["type"] in KINDS["comment"]][-40:]


def stem(w):
    return w[:-3] if len(w) > 5 and w.endswith("ing") else w[:-1] if len(w) > 3 and w.endswith("s") else w


def words(text, n=2):
    return {stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= n and w not in STOP}


def topic(st, kind, note):
    """Earlier notes whose subtopic (text after the first colon) shares at least half its words with this one."""
    sub = lambda n: n.split(":", 1)[-1]
    mine, clashes = words(sub(note)), []
    for e in earlier(st, kind):
        theirs = words(sub(e.get("note", "")))
        shared = mine & theirs
        small = min(len(mine), len(theirs))
        if shared and len(shared) >= min(2, small) and len(shared) / small >= 0.5:
            clashes.append(e["note"])
    return clashes


def last_sentence(t):
    return re.split(r"(?<=[.!?])\s+", t.strip())[-1].strip().lower()


def repeats(st, text):
    """Compare with every published post, comment and reply."""
    why, mine, toks = [], words(text, 4), text.lower().split()
    for e in st["log"]:
        old = e.get("text")
        if not old:
            continue
        theirs, otoks = words(old, 4), old.lower().split()
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= 0.4:
            why.append("too similar in words to an earlier text")
        elif min(len(text), len(old)) > 120 and difflib.SequenceMatcher(None, text.lower(), old.lower()).ratio() >= 0.6:
            why.append("too similar in wording to an earlier text")
        elif len(toks) >= 5 and toks[:5] == otoks[:5]:
            why.append("same opening as an earlier text")
        elif len(last_sentence(text).split()) >= 4 and last_sentence(text) == last_sentence(old):
            why.append("same closing sentence as an earlier text")
    return sorted(set(why))


def emojis(text):
    """Count emoji as people see them: a ZWJ sequence is one, skin tones and variation selectors add nothing."""
    n, joined = 0, False
    for c in text:
        o = ord(c)
        if c == "‍":
            joined = True
        elif 0x1F3FB <= o <= 0x1F3FF or o == 0xFE0F:
            continue
        elif 0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2300 <= o <= 0x23FF or 0x2B00 <= o <= 0x2BFF or o in (0x203C, 0x2049):
            n += 0 if joined else 1
            joined = False
        else:
            joined = False
    return n


def lint(kind, text, st=None):
    low, why = text.lower(), []
    why += [f"banned phrase: {b}" for b in BANNED if re.search(r"\b" + re.escape(b) + r"\b", low)]
    if re.search(r"\b\d+\s+(key\s+)?(takeaways|lessons)\b", low):
        why.append("'N takeaways/lessons' framing")
    if text.count("—") > 1:
        why.append("more than one em dash")
    if len(re.findall(r"#[A-Za-z]\w*", text)) > (2 if kind == "post" else 0):
        why.append("too many hashtags")
    if emojis(text) > (0 if kind == "post" else 1):
        why.append("too many emoji (posts: none, comments: at most 1)")
    if kind == "post" and not 700 <= len(text) <= 1000:
        why.append(f"post length {len(text)} not in 700-1000")
    if kind == "comment":
        if re.search(r"https?://|www\.", low):
            why.append("link in comment")
        n = len([s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()])
        if not 1 <= n <= 3:
            why.append(f"{n} sentences, want 1-3")
    if st:
        why += repeats(st, text)
    return why


def url_arg(u):
    if not re.fullmatch(r"https?://\S+", u):
        sys.exit("url must be a plain http(s) URL in single quotes, got: " + u[:60])
    return u


def main(argv):
    now, rng = datetime.now(), random.Random()
    cmd, a = (argv[1] if len(argv) > 1 else ""), argv[2:]
    if cmd == "home" and not a:
        print(home())
        return
    with locked():
        st = load()
        if cmd == "decide" and a in ([], ["--dry-run"]):
            out = decide(st, now, rng, dry=bool(a))
            if not a:
                save(st)
            print(json.dumps(out))
        elif cmd == "remaining" and not a:
            print(json.dumps(remaining(st, now)))
        elif cmd == "record" and len(a) == 2 and a[0] in (*CAPS, "post"):
            if paused():
                sys.exit("paused: PAUSE exists, nothing may be recorded or done")
            note, text = "", ""
            if a[0] in ("post", "comment", "reply"):
                note, _, text = sys.stdin.read().strip().partition("\n")
                if not note.strip() or not text.strip():
                    sys.exit("stdin must be: note on line 1, published text after it")
            record(st, now, a[0], url_arg(a[1]), note.strip(), text.strip())
            save(st)
        elif cmd == "seen" and len(a) == 1:
            print("yes" if seen(st, url_arg(a[0])) else "no")
        elif cmd == "history" and not a:
            for e in history(st):
                print(e["type"], e["url"], e.get("note", ""))
        elif cmd == "topic" and len(a) == 1 and a[0] in KINDS:
            clash = topic(st, a[0], sys.stdin.read().strip())
            print("\n".join(f"fail: overlaps earlier note: {c}" for c in clash) or "pass")
            sys.exit(1 if clash else 0)
        elif cmd == "lint" and len(a) == 1 and a[0] in KINDS:
            why = lint(a[0], sys.stdin.read().strip(), st)
            print("\n".join(f"fail: {w}" for w in why) or "pass")
            sys.exit(1 if why else 0)
        elif cmd == "pause" and not a:
            (home() / "PAUSE").touch()
        elif cmd == "unpause" and not a:
            (home() / "PAUSE").unlink(missing_ok=True)
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
