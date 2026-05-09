# sunsync-hue

A non-destructive Philips Hue scene daemon. Transitions your lights through Day → Afternoon → Evening → Night based on actual sunrise/sunset for your location, but only when nobody's been touching them. If you've manually dimmed the lounge for a movie, sunsync-hue leaves it alone.

## Quick start

```bash
# Python 3.11+
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# All commands run as `python3 ./sunsync-hue.py …` (no install step).
# The examples below assume you've activated the venv (or replace `python` with `.venv/bin/python`).

# One-time bridge pairing
python3 ./sunsync-hue.py setup

# Capture per-room scenes for Day / Afternoon / Evening / Night
python3 ./sunsync-hue.py learn

# Verify what the daemon would do right now
python3 ./sunsync-hue.py check

# Run the daemon
python3 ./sunsync-hue.py run
```

## How it decides whether to transition a room

At each daylight trigger (sunrise, ~golden hour, sunset, dusk), for each monitored room:

1. Read the current state of every light in the room from the bridge.
2. Compare it (with small tolerances) against each of the four scenes you captured for that room with `learn`.
3. If the room matches **any** of the four learned scenes — proceed with the transition.
4. If it matches **none** — skip the room. Something custom is going on.

So manually selecting Afternoon via the Hue app keeps the room in the "managed" set; the next Evening trigger will still fire. But dimming the room for a movie (a state that doesn't match any learned scene) will be respected.

## CLI commands

| Command | Purpose |
|---|---|
| `python3 ./sunsync-hue.py setup` | Discover bridge on LAN, prompt for link button press, save app key. |
| `python3 ./sunsync-hue.py learn` | Interactively snapshot per-room scenes. |
| `python3 ./sunsync-hue.py check` | Run the match check for every monitored room and print verdicts. Doesn't apply anything. |
| `python3 ./sunsync-hue.py apply <scene> [--room <name>] [--force]` | Manual override. Without `--force`, still respects the match check. |
| `python3 ./sunsync-hue.py run [--dry-run] [--catch-up]` | The daemon. `--dry-run` logs would-be apply actions without writing. |
| `python3 ./sunsync-hue.py status` | Print parsed config, today's computed event times, last-applied scene per room. |
| `python3 ./sunsync-hue.py forget <room>` | Remove a room from monitoring and drop its snapshots. |

## Files

- Config: `~/.config/sunsync-hue/config.toml`
- State (snapshots, last-applied): `~/.local/state/sunsync-hue/state.json`

## Running on a Raspberry Pi as a service

A sample systemd unit lives at `systemd/sunsync-hue.service`. Copy it to `~/.config/systemd/user/`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sunsync-hue.service
journalctl --user -u sunsync-hue -f
```
