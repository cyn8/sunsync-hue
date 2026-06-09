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
| **Afternoon** | start of the setting golden hour |
| **Evening** | nautical dusk |
| **Night** | 11:30 PM local time (configurable via `night_local_time`) |

Day, Afternoon, and Evening track the sun. Night is a wall-clock time. If you set `night_local_time` earlier than nautical dusk, it's interpreted as the next morning so the four scenes always fire in chronological order.

Sun-controlled scenes can be shifted by signed minute offsets in
`config.toml`:

```toml
[schedule.sun_offsets]
day = 0
afternoon = +20
evening = -10
```

For example, if Afternoon's base event is 4:00 PM and `afternoon = +20`, the
Afternoon trigger fires at 4:20 PM.

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
| `python3 ./sunsync-hue.py learn [<room>] [--scene <scene>]` | Interactively snapshot per-room scenes. With a room name, only that room is re-learned. With `--scene`, only that scene is re-learned for that room. |
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

To re-learn just one room/scene snapshot and keep the room's other scenes:

```bash
python3 ./sunsync-hue.py learn "Bedroom" --scene Day
```

## Coming home

`sunsync-hue` can expose a small LAN webhook for HomeKit automations. It is
disabled by default. Enable it in `~/.config/sunsync-hue/config.toml`:

```toml
[webhook]
enabled = true
host = "0.0.0.0"
port = 8765
coming_home_window_seconds = 900
```

Then restart the daemon and create two HomeKit automations that send HTTP POST
requests to the machine running `sunsync-hue`:

```text
POST http://<sunsync-host>:8765/coming-home/arrival
POST http://<sunsync-host>:8765/coming-home/motion
```

Use `/coming-home/arrival` for "when the first person arrives" and
`/coming-home/motion` for the Hue motion sensor trigger. Request bodies are
ignored, so an empty POST is fine. `GET /health` returns a basic health check.

When both events occur within `coming_home_window_seconds` of each other, in
either order, `sunsync-hue` checks every monitored room. If every managed,
non-excluded light is off, it transitions all monitored rooms to the current
clock scene using the normal transition duration. If any managed light is
already on, a room cannot be read, or the current scene has not been learned,
the coming-home transition is skipped.

These endpoints use no app-level authentication. Keep the listener on a trusted
LAN and do not expose it to the internet.

## Files

- Config: `~/.config/sunsync-hue/config.toml`
- Learned scene snapshots: `~/.local/share/sunsync-hue/scenes.json`
- Runtime state (last-applied): `~/.local/state/sunsync-hue/state.json`

## Running as a service

A sample systemd unit lives at `systemd/sunsync-hue.service`. Edit the `User=`,
`WorkingDirectory=`, and `ExecStart=` paths in it to match your install, then:

```bash
sudo cp systemd/sunsync-hue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sunsync-hue.service
sudo journalctl -u sunsync-hue -f
```
