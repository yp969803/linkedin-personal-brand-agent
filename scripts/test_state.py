import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LINKEDIN_AGENT_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).parent))
import state as s

fresh = lambda: {"week": "", "slots": [], "log": [], "session_start": ""}
rng = random.Random(1)
mon = datetime(2026, 11, 2, 9)  # Monday
fri_eve = datetime(2026, 11, 6, 19)

# exactly 2 distinct weekday slots per week, reset on a new week
st = fresh()
s.roll_week(st, mon, rng)
assert len(st["slots"]) == 2 and st["slots"][0]["day"] != st["slots"][1]["day"]
first = list(st["slots"])
s.roll_week(st, fri_eve, rng)
assert st["slots"] == first
s.roll_week(st, datetime(2026, 11, 9, 9), rng)
assert st["week"].endswith("W46")

# every slot has passed by Friday evening -> post is due and forces awake
st = fresh()
out = s.decide(st, fri_eve, rng)
assert out["awake"] and out["actions"][0] == "post"

# recording two posts clears the week's slots
s.record(st, fri_eve, "post", "u1", "topic a")
s.record(st, fri_eve, "post", "u2", "topic b")
assert all(x["done"] for x in st["slots"])
assert [e["note"] for e in s.history(st)] == ["topic a", "topic b"]

# daily and session caps; remaining is read-only
st = fresh()
for i in range(3):
    s.record(st, mon, "connect", f"p{i}")
before = json.loads(json.dumps(st))
rem = s.remaining(st, mon)
assert st == before and rem["connect"] == 0 and rem["comment"] == s.SESSION["comment"]
st["session_start"] = mon.isoformat()
for i in range(2):
    s.record(st, mon, "comment", f"c{i}")
assert s.remaining(st, mon)["comment"] == 0  # session cap of 2

# weekly connect cap
st = fresh()
for i in range(s.WEEKLY_CONNECT):
    s.record(st, mon - timedelta(hours=1), "connect", f"w{i}")
assert s.remaining(st, mon + timedelta(days=1))["connect"] == 0

# gates: active hours, PAUSE, min gap
st = fresh()
assert s.decide(st, datetime(2026, 11, 3, 3), rng)["reason"] == "outside active hours"
pause = Path(os.environ["LINKEDIN_AGENT_HOME"]) / "PAUSE"
pause.touch()
assert s.decide(st, fri_eve, rng)["reason"] == "paused"
pause.unlink()
st = fresh()
s.record(st, fri_eve - timedelta(minutes=10), "comment", "x")
acts = s.decide(st, fri_eve, rng)["actions"]
assert "comment" not in acts and "connect" not in acts and "reply" in acts

# like cap, seen
st = fresh()
for i in range(s.CAPS["like"]):
    s.record(st, mon, "like", f"l{i}")
assert s.remaining(st, mon)["like"] == 0 and s.seen(st, "l0") and not s.seen(st, "nope")

# lint
good_c = "We hit the same thing with queue retries. Moving the dedupe key into the message fixed it for us."
assert s.lint("comment", good_c) == []
assert s.lint("comment", "Great insights! Absolutely agree.")
assert s.lint("comment", "The future is here.")
assert s.lint("comment", "See https://x.com for more.")
assert s.lint("comment", "One. Two. Three. Four.")
assert s.lint("comment", "Nice #tag")
post = ("We moved our queue to a single writer last month. " * 14).strip()
assert 600 <= len(post) <= 1200 and s.lint("post", post) == []
assert s.lint("post", "too short")
assert s.lint("post", post + " Here are 5 key takeaways")
assert s.lint("post", post + " \U0001F680\U0001F525")
print("ok")
