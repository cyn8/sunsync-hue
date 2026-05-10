# sunsync-hue

A non-destructive Philips Hue scene daemon. Transitions your lights through Day → Afternoon → Evening → Night based on actual sunrise/sunset for your location, but only when nobody's been touching them. If you've manually dimmed the lounge for a movie, sunsync-hue leaves it alone.

## Quick start

```bash
# Python 3.11+
python -m venv env
env/bin/pip install -r requirements.txt

# All commands run as `python3 ./sunsync-hue.py …` (no install step).
# The examples below assume you've activated the venv (or replace `python` with `env/bin/python`).

# One-time bridge pairing
python3 ./sunsync-hue.py setup

# Capture per-room scenes for Day / Afternoon / Evening / Night
python3 ./sunsync-hue.py learn

# Verify what the daemon would do right now
python3 ./sunsync-hue.py check

# Run the daemon
python3 ./sunsync-hue.py run
```

## When the triggers fire

| Scene | When |
|---|---|
| **Day** | sunrise (computed from your latitude/longitude) |
| **Afternoon** | sunset |
| **Evening** | 10:00 PM local time (configurable via `evening_local_time`) |
| **Night** | 1:00 AM the following morning (configurable via `night_local_time`) |

Day and Afternoon track the sun. Evening and Night are wall-clock times. If you set `night_local_time` earlier than `evening_local_time` (e.g. the default 1:00 AM vs 10:00 PM), it's interpreted as the next morning so the four scenes always fire in chronological order.

## How it decides whether to transition a room

At each scheduled trigger, for each monitored room:

1. Read the current state of every light in the room from the bridge.
2. Compare it (with small tolerances) against the snapshot for the **previous** scene — e.g. when about to apply Afternoon, compare against the Day snapshot. (The order wraps: Day's predecessor is Night.)
3. If the room matches the previous scene — proceed with the transition.
4. If it doesn't — skip the room. Either something custom is going on, or a transition was missed and the chain has to resume on the next trigger.

So manually selecting Day via the Hue app keeps the room aligned; the next Afternoon trigger still fires from there. But dimming the room for a movie (a state that doesn't match the previous scene) will be respected. If the daemon was offline through several transitions, the room recovers gradually — one scene per trigger as the day progresses.

## CLI commands

| Command | Purpose |
|---|---|
| `python3 ./sunsync-hue.py setup` | Discover bridge on LAN, prompt for link button press, save app key. |
| `python3 ./sunsync-hue.py learn [<room>]` | Interactively snapshot per-room scenes. With a room name, only that room is re-learned (other rooms untouched). |
| `python3 ./sunsync-hue.py check` | Run the match check for every monitored room and print verdicts. Doesn't apply anything. |
| `python3 ./sunsync-hue.py apply <scene> [--room <name>] [--force]` | Manual override. Without `--force`, still respects the match check. |
| `python3 ./sunsync-hue.py run [--dry-run] [--catch-up]` | The daemon. `--dry-run` logs would-be apply actions without writing. |
| `python3 ./sunsync-hue.py status` | Print parsed config, today's computed event times, last-applied scene per room. |
| `python3 ./sunsync-hue.py forget <room>` | Remove a room from monitoring and drop its snapshots. |
| `python3 ./sunsync-hue.py exclude <room> <light>` | Stop sunsync-hue from managing a single light in a room. |
| `python3 ./sunsync-hue.py include <room> <light>` | Re-include a previously excluded light. |

## Excluding individual lights

Rooms in the Hue app sometimes group bulbs you don't actually want sunsync-hue
driving — a lava lamp on a smart plug, a TV bias-light, an accent that runs on
its own schedule. Mark those lights as excluded and the daemon will skip them
during `learn`, `check`, and `apply`. Other lights in the room continue as
normal, and the room can still match a scene even if the excluded light is in
some random state.

```bash
python3 ./sunsync-hue.py exclude "Living Room" "Lava Lamp"
python3 ./sunsync-hue.py include "Living Room" "Lava Lamp"
```

`learn` also lets you uncheck lights interactively when you set up a room.
Exclusions live in `config.toml` under `[rooms.excluded]` and are identified
by the light's Hue name — if you rename a bulb in the Hue app, the exclusion
silently lapses and the daemon starts managing it again.

## Files

- Config: `~/.config/sunsync-hue/config.toml`
- State (snapshots, last-applied): `~/.local/state/sunsync-hue/state.json`

## Running as a service

A sample systemd unit lives at `systemd/sunsync-hue.service`. Edit the `User=`,
`WorkingDirectory=`, and `ExecStart=` paths in it to match your install, then:

```bash
sudo cp systemd/sunsync-hue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sunsync-hue.service
sudo journalctl -u sunsync-hue -f
```
