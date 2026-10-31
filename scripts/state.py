#!/usr/bin/env python3
"""State, caps, schedule and draft lint for the LinkedIn growth skill.

  state.py decide               -> JSON: awake, reason, actions, remaining (rolls the awake chance, starts a session)
  state.py remaining            -> JSON of what is still allowed now (read-only)
  state.py record TYPE URL [NOTE] [TEXT] -> log an action (post|comment|connect|reply|like)
                                NOTE = "pillar: subtopic" (post) or "post topic: my angle" (comment/reply)
                                TEXT = what was published (required for post, comment, reply)
  state.py seen URL             -> "yes" or "no"
  state.py history              -> all posts and the last 40 comment/reply notes, oldest first
  state.py topic KIND NOTE      -> "pass", or the clashing earlier note (exit 1); run before drafting
  state.py lint KIND TEXT       -> "pass", or "fail: reason" lines (exit 1); also rejects repeats of earlier text
                                KIND = post|comment (comment also covers replies)

State lives in $LINKEDIN_AGENT_HOME (default ~/.linkedin-agent)/state.json.
Create $LINKEDIN_AGENT_HOME/PAUSE to stop all activity.
"""
import difflib
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

CAPS = {"comment": 4, "connect": 3, "reply": 10, "like": 15}  # per day
WEEKLY_CONNECT = 15
SESSION = {"comment": 2, "connect": 2}  # per wake-up
MIN_GAP = timedelta(minutes=30)  # between a wake-up and the last comment/connect
ACTIVE_HOURS = range(8, 20)  # local time
POSTS_PER_WEEK = 2
AWAKE_CHANCE = 0.5
KINDS = {"post": ("post",), "comment": ("comment", "reply")}
LOOKBACK = {"post": None, "comment": 100}  # how many earlier entries to compare with
STOP = {"the", "and", "for", "with", "that", "this", "what", "from", "have", "about", "into",
        "how", "why", "are", "you", "your", "was", "not", "but", "can", "our", "its"}

BANNED = [
    "delve", "game-changer", "game changer", "unlock", "leverage", "landscape", "journey",
    "in today's", "let that sink in", "here's the thing", "i'm thrilled", "excited to share",
    "great post", "thanks for sharing", "this resonates", "great insights", "absolutely agree",
    "rapidly evolving", "dive deep", "dive in", "the future is here",
]


def home():
    return Path(os.environ.get("LINKEDIN_AGENT_HOME", Path.home() / ".linkedin-agent"))


def load():
    try:
        return json.loads((home() / "state.json").read_text())
    except FileNotFoundError:
        return {"week": "", "slots": [], "log": [], "session_start": ""}


def save(st):
    home().mkdir(parents=True, exist_ok=True)
    (home() / "state.json").write_text(json.dumps(st, indent=1))


def week_id(now):
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def roll_week(st, now, rng):
    if st["week"] != week_id(now):
        days = sorted(rng.sample(range(5), POSTS_PER_WEEK))  # Mon-Fri
        st["week"] = week_id(now)
        st["slots"] = [{"day": d, "hour": rng.randint(8, 18), "done": False} for d in days]


def entries(st, kind, since):
    return [e for e in st["log"] if e["type"] == kind and e["t"] >= since]


def remaining(st, now):
    day = now.date().isoformat()
    wk = now.date() - timedelta(days=now.weekday())
    session = st.get("session_start") or "9999"
    r = {k: CAPS[k] - len(entries(st, k, day)) for k in CAPS}
    r["connect"] = min(r["connect"], WEEKLY_CONNECT - len(entries(st, "connect", wk.isoformat())))
    for k, c in SESSION.items():
        r[k] = min(r[k], c - len(entries(st, k, session)))
    return {k: max(0, n) for k, n in r.items()}


def decide(st, now, rng):
    roll_week(st, now, rng)
    post_due = any(
        not s["done"] and (s["day"], s["hour"]) <= (now.weekday(), now.hour) for s in st["slots"]
    )
    out = {"awake": False, "reason": "", "actions": [], "week": st["week"]}
    if (home() / "PAUSE").exists():
        out["reason"] = "paused"
    elif now.hour not in ACTIVE_HOURS:
        out["reason"] = "outside active hours"
    # a due post forces a wake-up, otherwise the weekly goal could be skipped by chance
    elif not post_due and rng.random() >= AWAKE_CHANCE:
        out["reason"] = "random skip"
    else:
        gap_ok = all(
            now - datetime.fromisoformat(e["t"]) >= MIN_GAP
            for e in st["log"][-20:] if e["type"] in SESSION
        )
        st["session_start"] = now.isoformat(timespec="seconds")
        rem = remaining(st, now)
        out["awake"] = True
        out["actions"] = (["post"] if post_due else []) + [
            k for k, n in rem.items() if n > 0 and (gap_ok or k not in SESSION)
        ]
    out["remaining"] = remaining(st, now)
    return out


def record(st, now, kind, url, note="", text=""):
    e = {"t": now.isoformat(timespec="seconds"), "type": kind, "url": url}
    if note:
        e["note"] = note
    if text:
        e["text"] = text
    st["log"].append(e)
    if kind == "post":
        for s in st["slots"]:
            if not s["done"]:
                s["done"] = True
                break


def seen(st, url):
    return any(e["url"] == url for e in st["log"])


def earlier(st, kind):
    """Earlier posts, or the last LOOKBACK comments/replies, oldest first."""
    es = [e for e in st["log"] if e["type"] in KINDS[kind]]
    return es if LOOKBACK[kind] is None else es[-LOOKBACK[kind]:]


def history(st):
    return earlier(st, "post") + [e for e in st["log"] if e["type"] in KINDS["comment"]][-40:]


def words(text, n=3):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= n and w not in STOP}


def topic(st, kind, note):
    """Earlier notes whose subtopic (text after the first colon) overlaps this one by half or more."""
    sub = lambda n: n.split(":", 1)[-1]
    mine = words(sub(note))
    return [
        e["note"] for e in earlier(st, kind)
        if e.get("note") and mine and words(sub(e["note"]))
        and len(mine & words(sub(e["note"]))) / min(len(mine), len(words(sub(e["note"])))) >= 0.5
    ]


def repeats(st, kind, text):
    why, mine, toks = [], words(text, 4), text.lower().split()
    last = lambda t: (re.split(r"(?<=[.!?])\s+", t.strip())[-1].strip().lower())
    for e in earlier(st, kind):
        old = e.get("text")
        if not old:
            continue
        theirs, otoks = words(old, 4), old.lower().split()
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= 0.4:
            why.append("too similar in words to an earlier " + kind)
        elif difflib.SequenceMatcher(None, text.lower(), old.lower()).ratio() >= 0.6:
            why.append("too similar in wording to an earlier " + kind)
        elif len(toks) >= 5 and toks[:5] == otoks[:5]:
            why.append("same opening as an earlier " + kind)
        elif len(last(text).split()) >= 4 and last(text) == last(old):
            why.append("same closing sentence as an earlier " + kind)
    return sorted(set(why))


def lint(kind, text, st=None):
    low, why = text.lower(), []
    why += [f"banned phrase: {b}" for b in BANNED if b in low]
    if re.search(r"\b\d+\s+(key\s+)?(takeaways|lessons)\b", low):
        why.append("'N takeaways/lessons' framing")
    if text.count("—") > 1:
        why.append("more than one em dash")
    if len(re.findall(r"#\w+", text)) > (2 if kind == "post" else 0):
        why.append("too many hashtags")
    if len([c for c in text if ord(c) >= 0x1F300 or 0x2600 <= ord(c) <= 0x27BF]) > 1:
        why.append("more than one emoji")
    if kind == "post" and not 700 <= len(text) <= 1000:
        why.append(f"post length {len(text)} not in 700-1000")
    if kind == "comment":
        if re.search(r"https?://|www\.", low):
            why.append("link in comment")
        n = len([s for s in re.split(r"[.!?]+", text) if s.strip()])
        if not 1 <= n <= 3:
            why.append(f"{n} sentences, want 1-3")
    if st:
        why += repeats(st, kind, text)
    return why


def main(argv):
    now, rng, st = datetime.now(), random.Random(), load()
    cmd, a = (argv[1] if len(argv) > 1 else ""), argv[2:]
    if cmd == "decide" and not a:
        out = decide(st, now, rng)
        save(st)
        print(json.dumps(out))
    elif cmd == "remaining" and not a:
        print(json.dumps(remaining(st, now)))
    elif cmd == "record" and 2 <= len(a) <= 4 and a[0] in (*CAPS, "post"):
        record(st, now, *a)
        save(st)
    elif cmd == "seen" and len(a) == 1:
        print("yes" if seen(st, a[0]) else "no")
    elif cmd == "history" and not a:
        for e in history(st):
            print(e["type"], e["url"], e.get("note", ""))
    elif cmd == "topic" and len(a) == 2 and a[0] in KINDS:
        clash = topic(st, *a)
        print("\n".join(f"fail: overlaps earlier note: {c}" for c in clash) or "pass")
        sys.exit(1 if clash else 0)
    elif cmd == "lint" and len(a) == 2 and a[0] in ("post", "comment"):
        why = lint(*a, st)
        print("\n".join(f"fail: {w}" for w in why) or "pass")
        sys.exit(1 if why else 0)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
