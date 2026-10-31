# LinkedIn personal brand agent

One skill (`linkedin-growth`), packaged as a Claude Code plugin and a Codex plugin.

It wakes at random, then (within daily caps): writes 2 posts a week, replies to people who comment on your posts, comments on relevant posts from CEOs / CTOs / founders / founding engineers it finds in your feed, and sends them connection requests. Posts and comments use easy English, no politics, bait or jokes (see `skills/linkedin-growth/voice.md`).

## Warning
LinkedIn has no API for this and its terms ban automation. Your account can be limited or banned. Caps (per day: 4 comments, 3 connects, 10 replies, 15 likes; per week: 15 connects, 2 posts; per wake-up: 2 comments, 2 connects), active hours 8-20, a 30 minute gap, and random timing lower the risk, they do not remove it. To stop everything, run `python3 scripts/state.py pause` or `touch ~/.linkedin-agent/PAUSE` (the agent also pauses itself if LinkedIn shows a warning or captcha); `unpause` resumes. Times use the machine's local clock, so set `TZ` if it is not your own. You are responsible for what it posts: start with `--dry-run`.

## Setup
1. Install
   - Claude Code: `/plugin install linkedin-personal-brand-agent --marketplace yp969803/linkedin-personal-brand-agent` (Claude Code 2.1.275 or later), or `/plugin marketplace add yp969803/linkedin-personal-brand-agent` then `/plugin install linkedin-personal-brand-agent`
   - Codex: add this repo as a plugin (it has `.codex-plugin/plugin.json`).
2. Open LinkedIn in the session's built-in browser tab and log in yourself. The agent never handles passwords.
3. Run `/linkedin-growth` (Codex: `$linkedin-growth`). The profile is `skills/linkedin-growth/profile.md` in this repo (no separate copy; edit it in place). It holds who you are, topics, post pillars, ideal contacts, real stories, **dated learning notes** and voice. The agent writes posts only from the learning notes and real stories, so add entries as you read and build; with none, it skips posting. In Claude Code the skill may be namespaced: `/linkedin-personal-brand-agent:linkedin-growth`.
4. Try `/linkedin-growth --dry-run` and read the output. A dry run ignores active hours and the random skip and saves nothing.
5. Schedule random wake-ups inside the session, e.g. in Claude `/loop` or a session cron at odd minutes, 4 times per weekday, prompt `/linkedin-growth`. The skill rolls a 50% chance to skip each wake-up, so timing stays irregular. Posting days and hours are picked randomly each week.

Wake-ups only work while the session holding the LinkedIn tab is open.

## How to use

**First time**
1. Install the plugin, open LinkedIn in the session's browser tab and log in.
2. Run `/linkedin-growth` once. It stops if the profile's topics or real stories are empty.
3. Open `skills/linkedin-growth/profile.md` and make it yours: who you are, topics, post pillars, ideal contacts, real stories, voice, never-mention list.
4. Add learning notes (next section). Without them the agent will not post.
5. Run `/linkedin-growth --dry-run`. It prints the post, comments and connection requests it would make, and clicks nothing. Read them. If something sounds wrong, fix `profile.md` or `skills/linkedin-growth/voice.md` and dry-run again.
6. When you are happy, run `/linkedin-growth` for a live run, or schedule it (Setup step 5).

**Learning notes: what keeps posts real.** The agent writes a post only from entries here and from "Real things I can talk about". Add one line each time you read, run or understand something, under its pillar (agents, inference and LLM architecture, RL, RL environments):
```
- 2026-11-03 AI inference: read how <paper or code> does X; I understood A and B; I still don't get C
```
Write only what is true. The agent will not add details you did not write. Each note is used once: a similar new post is rejected, so add new notes as you go. Roughly 2 notes a week keeps the 2 weekly posts supplied.

**Day to day**
- Do nothing. Scheduled wake-ups run while the session is open. Most are quiet: a 50% random skip, active hours 8-20, and caps.
- After a run, read its short summary: what it did, what it skipped and why, and what each comment was based on.
- Check what it has posted: `python3 scripts/state.py history`. The full log with text is `~/.linkedin-agent/state.json`.
- Stop it: `python3 scripts/state.py pause`. Resume: `python3 scripts/state.py unpause`. If LinkedIn shows a captcha or warning the agent pauses itself and tells you. Check your account before you unpause.

**Command reference**
| Command | What it does |
| --- | --- |
| `/linkedin-growth` | One normal run (`$linkedin-growth` in Codex) |
| `/linkedin-growth --dry-run` | Draft only, no clicks, nothing saved |
| `python3 scripts/state.py remaining` | What is still allowed now |
| `python3 scripts/state.py history` | All posts and recent comment notes |
| `python3 scripts/state.py pause` / `unpause` | Kill switch |
| `python3 scripts/test_state.py` | Run the self-checks |

**If nothing happens**
- Run printed nothing or "not awake": random skip, outside 8-20, or paused. Try `--dry-run` to see drafts regardless.
- No post: no slot is due, a post went out in the last 24 hours, or no unused learning note exists.
- No comments or connects: caps used, the 30 minute gap applies, or no feed post passed the targeting rules.
- It was working and stopped: look for `~/.linkedin-agent/PAUSE`.

## Files
- `skills/linkedin-growth/` the playbook, voice rules, targeting rules, and your profile (`profile.md`)
- `scripts/state.py` caps, weekly post schedule, "already seen" log, post history, draft lint (`~/.linkedin-agent/state.json`)
- `python3 scripts/test_state.py` runs the checks
