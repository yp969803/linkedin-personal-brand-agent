import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LINKEDIN_AGENT_HOME"] = tempfile.mkdtemp()
HOME = Path(os.environ["LINKEDIN_AGENT_HOME"])
SCRIPT = Path(__file__).parent / "state.py"
sys.path.insert(0, str(SCRIPT.parent))
import state as s


class Awake(random.Random):  # never takes the random skip
    def random(self):
        return 0.0


class Sleepy(random.Random):  # always takes the random skip
    def random(self):
        return 0.99


rng = random.Random(1)
mon = datetime(2026, 11, 2, 9)  # Monday
fri_eve = datetime(2026, 11, 6, 19)
fresh = s.fresh


def cli(args, stdin="", home=None):
    env = dict(os.environ, LINKEDIN_AGENT_HOME=str(home or HOME))
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True, env=env)


# --- weekly schedule -------------------------------------------------------
st = fresh()
s.roll_week(st, mon, rng)
assert len(st["slots"]) == 2 and st["slots"][0]["day"] != st["slots"][1]["day"]
assert all((x["day"], x["hour"]) > (0, 9) for x in st["slots"])
first = json.loads(json.dumps(st["slots"]))
s.roll_week(st, fri_eve, rng)
assert st["slots"] == first  # same week, unchanged

wed = datetime(2026, 11, 4, 10)  # installed mid-week: slots only from the time left
st = fresh()
s.roll_week(st, wed, rng)
assert len(st["slots"]) == 2 and all((x["day"], x["hour"]) > (2, 10) for x in st["slots"])

st = fresh()  # installed late Friday: nothing left this week, no bunched posts
s.roll_week(st, fri_eve, rng)
assert st["slots"] == []
assert "post" not in s.decide(st, fri_eve, Awake(1))["actions"]

# a post missed last week is carried over and due at once, total stays 2
st = fresh()
st["week"], st["slots"] = "2026-W44", [{"day": 4, "hour": 18, "done": False}, {"day": 1, "hour": 9, "done": True}]
out = s.decide(st, mon, Awake(1))
assert st["week"].endswith("W45") and len(st["slots"]) == 2 and "post" in out["actions"]

# 24h between posts, and a manual extra post does not use up a future slot
st = fresh()
st["week"] = s.week_id(mon)
st["slots"] = [{"day": 0, "hour": 9, "done": False}, {"day": 0, "hour": 10, "done": False}]
assert "post" in s.decide(st, mon, Awake(1))["actions"]
s.record(st, mon, "post", "u1", "AI inference: kv cache paging", "text")
assert [x["done"] for x in st["slots"]] == [True, False]
assert "post" not in s.decide(st, mon + timedelta(hours=1), Awake(1))["actions"]
assert "post" in s.decide(st, mon + timedelta(hours=26), Awake(1))["actions"]
st = fresh()
st["week"] = s.week_id(mon)
st["slots"] = [{"day": 3, "hour": 9, "done": False}]
s.record(st, mon, "post", "u9", "x: y", "text")
assert st["slots"][0]["done"] is False

# --- caps, session, gates ----------------------------------------------------
st = fresh()
for i in range(3):
    s.record(st, mon, "connect", f"p{i}")
before = json.loads(json.dumps(st))
rem = s.remaining(st, mon)
assert st == before and rem["connect"] == 0 and rem["comment"] == 0  # no live session yet
st["session_start"] = mon.isoformat()
assert s.remaining(st, mon)["comment"] == s.SESSION["comment"]
for i in range(2):
    s.record(st, mon, "comment", f"c{i}")
assert s.remaining(st, mon)["comment"] == 0  # session cap of 2
assert s.remaining(st, mon + timedelta(hours=3))["comment"] == 0  # stale session

st = fresh()  # weekly connect cap
for i in range(s.WEEKLY_CONNECT):
    s.record(st, mon - timedelta(hours=1), "connect", f"w{i}")
st["session_start"] = (mon + timedelta(days=1)).isoformat()
assert s.remaining(st, mon + timedelta(days=1))["connect"] == 0

st = fresh()  # like cap
for i in range(s.CAPS["like"]):
    s.record(st, mon, "like", f"l{i}")
assert s.remaining(st, mon)["like"] == 0

st = fresh()  # active hours, random skip, PAUSE
assert s.decide(st, datetime(2026, 11, 3, 3), Awake(1))["reason"] == "outside active hours"
assert s.decide(fresh(), fri_eve, Sleepy(1))["reason"] == "random skip"
(HOME / "PAUSE").touch()
assert s.decide(st, fri_eve, Awake(1))["reason"] == "paused"
assert s.remaining(st, fri_eve) == {k: 0 for k in s.CAPS}
r = cli(["record", "like", "https://x.com/a"])
assert r.returncode != 0 and "paused" in r.stderr
(HOME / "PAUSE").unlink()

st = fresh()  # minimum gap since the previous wake-up applies to comment/connect only
s.record(st, fri_eve - timedelta(minutes=10), "comment", "x")
acts = s.decide(st, fri_eve, Awake(1))["actions"]
assert "comment" not in acts and "connect" not in acts and "reply" in acts
st = fresh()
s.record(st, fri_eve - timedelta(hours=2), "comment", "x")
assert "comment" in s.decide(st, fri_eve, Awake(1))["actions"]

st = fresh()  # dry run: every action, nothing changes
before = json.loads(json.dumps(st))
out = s.decide(st, datetime(2026, 11, 3, 3), Sleepy(1), dry=True)
assert out["awake"] and "post" in out["actions"] and st == before
assert json.loads(cli(["decide", "--dry-run"]).stdout)["awake"]
assert not (HOME / "state.json").exists()

# --- state file problems --------------------------------------------------------
h = Path(tempfile.mkdtemp())
(h / "state.json").write_text('{"log": []}')
os.environ["LINKEDIN_AGENT_HOME"] = str(h)
assert s.load()["week"] == "" and s.load()["slots"] == []  # old file gets defaults
tz = fresh()
tz["log"] = [{"t": "2026-11-02T08:00:00+00:00", "type": "comment", "url": "a"}]
tz["session_start"] = "2026-11-02T09:00:00"
assert s.remaining(tz, mon)["comment"] == 2  # aware timestamps do not crash
(h / "state.json").write_text('{"log": [')
try:
    s.load()
    raise AssertionError("corrupt file should exit")
except SystemExit:
    assert (h / "state.corrupt").exists() and not (h / "state.json").exists()
os.environ["LINKEDIN_AGENT_HOME"] = str(HOME)

# --- seen normalizes post urls -------------------------------------------------------
st = fresh()
s.record(st, mon, "comment", "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/")
assert s.seen(st, "https://www.linkedin.com/posts/someone-slug-activity-7123456789012345678-AbCd?utm=x")
assert not s.seen(st, "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/")

# --- lint -----------------------------------------------------------------------
good_c = "We hit the same thing with queue retries. Moving the dedupe key into the message fixed it for us."
assert s.lint("comment", good_c) == []
for bad in ["Great insights! Absolutely agree.", "The future is here.", "See https://x.com for more.",
            "One. Two. Three. Four.", "Nice #tag", "Two \U0001F680\U0001F525 emoji", "⭐ and ⏰ here"]:
    assert s.lint("comment", bad), bad
assert s.lint("comment", "We ran vLLM 0.6.3 with it. It helped.") == []  # version numbers are not sentences
assert s.lint("comment", "This looks like issue #4521 in vLLM.") == []  # not a hashtag
assert s.lint("comment", "The spinlock unlock path was missing a barrier.") == []  # technical word
assert s.lint("comment", "Nice work \U0001F468‍\U0001F4BB on the repo.") == []  # ZWJ emoji counts once
assert s.lint("comment", "Agreed \U0001F44D\U0001F3FD thanks to the author.") == []
post = ("We moved our queue to a single writer last month. " * 16).strip()
assert 700 <= len(post) <= 1000 and s.lint("post", post) == []
assert s.lint("post", "too short")
assert s.lint("post", post + " Here are 5 key takeaways")
assert s.lint("post", post + " \U0001F680")  # posts: no emoji at all

# --- repeats: one pool for posts, comments and replies ---------------------------------
st = fresh()
p1 = ("I am reading how vLLM pages the KV cache into fixed blocks so GPU memory is not wasted on padding. "
      "Each sequence gets a block table, and blocks are handed out as tokens arrive. "
      "What I still cannot explain is how block size affects throughput on long prompts.")
s.record(st, mon, "post", "u1", "AI inference: kv cache paging", p1)
has = lambda kind, text, word: any(word in w for w in s.lint(kind, text, st))
assert has("post", p1, "similar")  # exact copy
assert has("post", p1.replace("fixed", "equal").replace("wasted", "lost"), "similar")
assert has("comment", p1, "similar")  # a comment may not copy an earlier post either
assert has("post", "I am reading how vLLM pages " + "Mamba keeps a state instead of a cache. " * 22, "opening")
p2 = ("Policy gradients confuse me less after writing a tiny REINFORCE loop on CartPole by hand. "
      "The baseline term was the part that clicked, since it only changes variance. "
      "What I still cannot explain is why the same trick behaves differently with sparse rewards.")
assert [w for w in s.lint("post", p2, st) if "earlier" in w] == []
assert has("post", p2.rsplit(". ", 1)[0] + ". " + p1.rsplit(". ", 1)[1], "closing")
s.record(st, mon, "reply", "r1", "retries: dedupe key", good_c)
assert has("comment", good_c, "similar") and has("post", good_c * 8, "similar")
s.record(st, mon, "comment", "r2", "batching: block size", "How big is your block size?")
assert [w for w in s.lint("comment", "How big is your batch size?", st) if "earlier" in w] == []  # short, different
for i in range(150):  # old text is never forgotten
    s.record(st, mon, "comment", f"c{i}", f"t{i}: angle{i}", f"Different text number {i} about topic{i} here")
assert has("comment", good_c, "similar")

# --- topics: compared after the colon; one shared word is not a clash -----------------------
st = fresh()
s.record(st, mon, "post", "u1", "AI inference: kv cache paging", "x")
assert s.topic(st, "post", "AI inference: kv cache eviction")
assert s.topic(st, "post", "Reinforcement learning: kv cache paging")  # pillar differs, subtopic same
assert s.topic(st, "post", "AI inference: mamba state space models") == []
assert s.topic(st, "post", "AI inference: CPU cache misses in tokenizers") == []
assert s.topic(st, "comment", "anything: whatever") == []
s.record(st, mon, "comment", "c1", "agents: how agents plan", "x")
assert s.topic(st, "comment", "tools: how agents use tools") == []
assert s.topic(st, "comment", "queues: dedupe key") == [] and s.topic(st, "post", "x: how agents plan") == []
s.record(st, mon, "reply", "r1", "retries: dedupe key", "x")
assert s.topic(st, "comment", "queues: dedupe key")
assert len([e for e in s.history(st) if e["type"] != "post"]) == 2

# --- CLI: text goes in on stdin, untouched; concurrent writers are safe -----------------------
h = Path(tempfile.mkdtemp())
tricky = "Costs $5 per GPU hour on `hostname`. Worth it $(echo hi)."
r = cli(["lint", "comment"], tricky, h)
assert r.returncode == 0 and r.stdout.strip() == "pass", r.stdout + r.stderr
assert cli(["record", "comment", "https://x.com/a"], "topic: angle\n" + tricky, h).returncode == 0
assert json.loads((h / "state.json").read_text())["log"][0]["text"] == tricky  # stored exactly
assert cli(["lint", "comment"], tricky, h).returncode == 1  # now a repeat
assert cli(["record", "like", "x; rm -rf ~"], "", h).returncode != 0  # not a URL
assert cli(["record", "comment", "https://x.com/b"], "", h).returncode != 0  # needs note and text
assert cli(["topic", "comment"], "topic: angle", h).returncode == 1
assert cli(["home"], "", h).stdout.strip() == str(h)
assert cli(["pause"], "", h).returncode == 0 and (h / "PAUSE").exists()
assert cli(["unpause"], "", h).returncode == 0 and not (h / "PAUSE").exists()

h = Path(tempfile.mkdtemp() + "/new")  # PAUSE works before the state directory exists
assert cli(["pause"], "", h).returncode == 0 and (h / "PAUSE").exists()

h = Path(tempfile.mkdtemp())
env = dict(os.environ, LINKEDIN_AGENT_HOME=str(h))
procs = [subprocess.Popen([sys.executable, str(SCRIPT), "record", "like", f"https://x.com/{i}"], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for i in range(20)]
assert all(p.wait() == 0 for p in procs)
assert len(json.loads((h / "state.json").read_text())["log"]) == 20
print("ok")
