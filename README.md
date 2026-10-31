# LinkedIn personal brand agent

One skill (`linkedin-growth`), packaged as a Claude Code plugin and a Codex plugin.

It wakes at random, then (within daily caps): writes 2 posts a week, replies to people who comment on your posts, comments on relevant posts from CEOs / CTOs / founders / founding engineers it finds in your feed, and sends them connection requests. Posts and comments use easy English, no politics, bait or jokes (see `skills/linkedin-growth/voice.md`).

## Warning
LinkedIn has no API for this and its terms ban automation. Your account can be limited or banned. Caps (per day: 4 comments, 3 connects, 10 replies, 15 likes; per week: 15 connects, 2 posts; per wake-up: 2 comments, 2 connects), active hours 8-20, a 30 minute gap, and random timing lower the risk, they do not remove it. To stop everything, create the file `~/.linkedin-agent/PAUSE` (the agent also creates it if LinkedIn shows a warning or captcha). You are responsible for what it posts: start with `--dry-run`.

## Setup
1. Install
   - Claude Code: `/plugin marketplace add <path or git url of this repo>` then `/plugin install linkedin-personal-brand-agent`
   - Codex: add this repo as a plugin (it has `.codex-plugin/plugin.json`).
2. Open LinkedIn in the session's built-in browser tab and log in yourself. The agent never handles passwords.
3. Run `/linkedin-growth` (Codex: `$linkedin-growth`). First run creates `~/.linkedin-agent/profile.md`: fill in topics, voice samples, ideal contacts.
4. Try `/linkedin-growth run --dry-run` and read the output.
5. Schedule random wake-ups inside the session, e.g. in Claude `/loop` or a session cron at odd minutes, 4 times per weekday, prompt `/linkedin-growth run`. The skill rolls a 50% chance to skip each wake-up, so timing stays irregular. Posting days and hours are picked randomly each week.

Wake-ups only work while the session holding the LinkedIn tab is open.

## Files
- `skills/linkedin-growth/` the playbook, voice rules, targeting rules, profile template
- `scripts/state.py` caps, weekly post schedule, "already seen" log, post history, draft lint (`~/.linkedin-agent/state.json`)
- `python3 scripts/test_state.py` runs the checks
