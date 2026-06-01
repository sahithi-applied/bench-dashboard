# Bench Bringup Dashboard

A web dashboard for monitoring and controlling the physical hardware bench (elpaso) at Applied Intuition. Runs as a Flask web server. Currently runs on a developer's laptop; the goal is to deploy it on Apps Platform so the whole team can use it without installing anything.

---

## What It Does

The dashboard connects to the hardware bench over the network and lets you:

- See which USB hubs, relay boards, serial devices, and network relays are online
- Toggle USB hub ports on/off (cuts power to connected devices)
- Toggle relay channels on/off (physically switches DUT power and boot mode signals)
- Stop and start the BuildKite CI agent running on the bench
- See live-updating status without refreshing the page (Server-Sent Events)

The bench hardware is on the Applied internal network. The dashboard SSHes into the bench host (`dev@10.80.11.28`) to run checks and control commands, and makes direct HTTP calls to a network relay module.

---

## Architecture

```
Browser (anyone on VPN or Apps Platform)
        |
   Web server  ←── bench_config.yaml (all benches + their hardware)
        |
        ├── [parallel] SSH → elpaso (10.80.11.28)
        │         ├── USB hub state/toggle     (termios, no extra libs)
        │         ├── Relay state/toggle       (pyftdi via Bazel cache)
        │         ├── Serial device presence   (/sys/bus/usb/devices)
        │         └── BuildKite agent control  (systemctl)
        │
        ├── [parallel] SSH → mater (10.80.X.X)
        │         └── (same device types, different hardware)
        │
        ├── [parallel] SSH → coconut (10.80.X.X)
        │         └── ...
        │
        └── HTTP → Denkovi relays (each bench can have its own)
```

**Multi-bench is a core requirement.** The server polls all configured benches in parallel. Each bench has its own SSH host, user, and device list. Benches can have different device types — a bench with no Denkovi relay simply omits that section. The frontend shows a bench tab or sidebar to switch between benches.

The server does **not** install anything on any bench. All bench-side code is base64-encoded Python snippets sent over SSH and executed inline. Benches only need Python 3 stdlib (except relay state reading which uses pyftdi from the existing Bazel cache).

---

## File Structure

```
bench_bringup/dashboard/
├── app.py                      # Flask app: routing, polling loop, SSE, toggle endpoints
├── bench_config.yaml           # Elpaso bench hardware layout (IPs, serials, channels)
├── bench_config.local.yaml     # Local test config (gitignored, for laptop dev)
├── requirements.txt            # flask, pyyaml, requests
├── run.sh                      # Local dev runner (creates venv, checks SSH, runs app)
├── README.md                   # This file
├── devices/
│   ├── ssh_runner.py           # Shared SSH exec utility — base64-encodes scripts, runs via SSH
│   ├── hub.py                  # StarTech USB hub — check port states + toggle via termios
│   ├── relay.py                # Sainsmart USB relay — check/toggle via pyftdi over SSH
│   ├── denkovi_relay.py        # Denkovi network relay — check/toggle via direct HTTP
│   ├── serial_device.py        # USB serial device presence check via /sys/bus/usb
│   └── buildkite_agent.py      # BuildKite agent status, stop, start via systemctl
└── templates/
    └── index.html              # Dashboard UI — SSE live updates, toggle buttons
```

---

## Key Implementation Details

### SSH execution pattern

All bench-side checks run like this (from `ssh_runner.py`):

```python
encoded = base64.b64encode(script.encode()).decode()
cmd = f"python3 -c \"import base64; exec(base64.b64decode('{encoded}').decode())\""
subprocess.run(["ssh", "-o", "BatchMode=yes", f"dev@10.80.11.28", cmd])
```

Scripts print a JSON result to stdout which is parsed back in the Flask server. This avoids installing any files on the bench.

### pyftdi on NixOS (elpaso relay reads)

Elpaso runs NixOS. pyftdi is not in the system Python. The relay check script includes a preamble that searches for pyftdi inside the existing Bazel pipdeps cache on the bench (populated by CI runs) and for libusb in the Nix store:

```python
_home = os.path.expanduser('~')
glob('~/.cache/bazel/_bazel_bk/*/external/pipdeps_py310_pyftdi/site-packages')
glob('/nix/store/*-libusb-*/lib/libusb-1.0.so.0')
```

This avoids installing anything — pyftdi is already present from CI.

### Relay state reading

The FTDI relay boards are bitbang USB devices (not serial TTY). They appear at `/sys/bus/usb/devices/*/serial` not `/dev/serial/by-id/`. Relay detection uses sysfs, not `/dev/`.

The kernel `ftdi_sio` driver must be unloaded before pyftdi can claim the device:
```bash
sudo rmmod ftdi_sio
```
This is a one-time bench setup step.

### Denkovi relay

The Denkovi smartDEN IP-16R is a network relay module at `10.80.10.57`. It exposes an HTTP API at `/current_state.json`. The dashboard talks to it directly from the Flask server — no SSH needed. This works from a laptop on VPN and will also work from Apps Platform since it has VPC access to the bench network.

### Live updates

The frontend connects to `/events` (Server-Sent Events). The Flask server has a background thread that polls all devices every 5 seconds and broadcasts updates to all connected browsers.

**Important for Cloud Run deployment:** The background thread will freeze when no request is active (Cloud Run CPU throttling). This needs to be changed before deploying. See the Apps Platform section below.

---

## How to Run Locally

```bash
cd bench_bringup/dashboard
./run.sh                                          # elpaso bench
BENCH_CONFIG=bench_config.local.yaml ./run.sh     # local test bench
```

Open http://localhost:5000

---

## Bench Hardware (elpaso)

| Component | Detail |
|-----------|--------|
| Host | Minisforum mini PC, NixOS |
| IP | 10.80.11.28 |
| SSH user | dev (NOPASSWD sudo) |
| BuildKite agent | runs as `bk` user, service `buildkite-agent-bk.service`, queue `fw_hil` |
| Hub 2 | StarTech B0B7K2U4 — DUT Relays, PCAN, LabJack T7 |
| Hub 1 | FTDI B001P1EX — S32Z debugger, relay hub, serial ports |
| Relay AH05I4ZX | DUT power/boot (4 channels) |
| Relay AH05I53H | WDT relays FS86/PF50 (4 channels) |
| Denkovi relay | 10.80.10.57, 16-channel HTTP relay (bench "mnemosyne") |
| Serial devices | MCU S32K, RTU S32Z, SMU S32Z, SENT gateway, CodeWarrior TAP |

---

## Apps Platform Deployment

### Why Apps Platform

Running the Flask server on a developer's laptop means:
- Only one person can use it at a time
- The server goes down when the laptop sleeps
- Everyone else has to clone the repo and run it themselves

Apps Platform hosts it at a stable URL that anyone at Applied can open.

### Platform facts learned

- Deployed on Google Cloud Run behind IAP (Google account login)
- Python Flask apps are supported natively (`apps-platform app init --template python-flask`)
- Apps have VPC access to internal IPs including `10.80.11.28` and `10.80.10.57`
- Static egress IP can be enabled so elpaso can allowlist the app's outbound IP
- **CPU throttling:** background threads freeze when no request is active. Must use request-driven polling instead of a background thread.
- Config goes in `project.toml` at the app root
- Secrets managed via `apps-platform app secret set KEY "value"`

### project.toml (create at bench_bringup/dashboard/project.toml)

```toml
name = "bench-bringup"

[metadata]
owner = "sahithi.kalva@applied.co"
description = "Hardware bench bring-up dashboard for elpaso"

[cloudrun]
port = 8080
use_static_egress_ip = true
```

### Changes required before deploying

#### 1. app.py — read PORT from environment

Cloud Run injects the port via `PORT` env variable. Change the main block:

```python
port = int(os.environ.get("PORT", 8080))
app.run(host="0.0.0.0", port=port)
```

Also remove the background `_poll_loop` thread and the `/events` SSE endpoint (see next point).

#### 2. Replace SSE with browser-side polling

The current `/events` SSE endpoint relies on a background thread that won't work on Cloud Run. Replace it with client-side polling in `index.html`:

```javascript
// Replace new EventSource('/events') with:
setInterval(() => {
    fetch('/api/state')
        .then(r => r.json())
        .then(data => updateDashboard(data));
}, 5000);
```

The `/api/state` endpoint already exists and returns the full bench state. No backend changes needed beyond removing the background thread and SSE route.

#### 3. SSH private key as a secret

On your laptop, SSH to elpaso uses `~/.ssh/id_rsa` implicitly. Cloud Run has no SSH keys on disk.

Set the secret once:
```bash
apps-platform app secret set SSH_PRIVATE_KEY "$(cat ~/.ssh/id_rsa)"
```

Then in `devices/ssh_runner.py`, at module load time, write the key to a temp file:

```python
import os, stat, tempfile, atexit

_key_file = None

def _ensure_key_file() -> str | None:
    global _key_file
    if _key_file:
        return _key_file
    key = os.environ.get("SSH_PRIVATE_KEY")
    if not key:
        return None
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
    f.write(key)
    f.close()
    os.chmod(f.name, stat.S_IRUSR | stat.S_IWUSR)
    atexit.register(os.unlink, f.name)
    _key_file = f.name
    return _key_file
```

And pass `-i {key_path}` to the SSH command when `_ensure_key_file()` returns a path.

#### 4. requirements.txt

No changes needed. The platform installs from `requirements.txt` automatically.

### Deploy commands

```bash
cd bench_bringup/dashboard

# First time only:
apps-platform app init --template python-flask --app-name bench-bringup
apps-platform app secret set SSH_PRIVATE_KEY "$(cat ~/.ssh/id_rsa)"

# Every deploy:
apps-platform app deploy
```

---

## Scalability

### Multi-bench config schema

The config file should support a list of benches. Each bench entry is independent — omit any device section that bench doesn't have:

```yaml
benches:
  - name: elpaso
    display_name: "Elpaso HW Bench 5"
    ssh:
      host: 10.80.11.28
      user: dev
      sudo_user: bk
    buildkite:
      service: buildkite-agent-bk.service
      agent_name: elpaso-hw-bench-5
      queue: fw_hil
    devices:
      usb_hubs:
        - name: "Hub 2 — DUT Relays / PCAN / LabJack"
          serial_path: /dev/serial/by-id/usb-StarTech.com_...
          ports: ["DUT Relays 0", "Hub 5 (PCAN0-3)", "LabJack T7", "Unused"]
      relay_boards:
        - name: "DUT Relays 0 — Power / Boot"
          identifier: AH05I4ZX
          channels:
            - {label: main_12v, inverted: true}
            - {label: wdg_dbg_fs26, inverted: true}
      serial_devices:
        - {name: "MCU S32K", identifier: AU0K41SH}
      denkovi_relays:
        - {name: "Mnemosyne", host: 10.80.10.57, port: 80, password: admin}

  - name: mater
    display_name: "Mater FW HIL 19"
    ssh:
      host: 10.80.X.X
      user: dev
    devices:
      relay_boards:
        - ...
```

The backend polls all benches in parallel. The `/api/state` response is keyed by bench name:
```json
{
  "elpaso": {"hubs": [...], "relays": [...], "serials": [...], "agent": {...}},
  "mater":  {"hubs": [...], "relays": [...], "serials": [...], "agent": {...}}
}
```

### Multi-instance Cloud Run (shared state)

For reliable multi-user access with multiple Cloud Run instances, move state out of memory into a shared cache:

- **Cloud Memorystore (Redis):** store the latest poll result as a JSON string with a TTL. Each instance reads from Redis on `/api/state`. One instance or Cloud Scheduler triggers `/internal/poll` to refresh it.
- **Cloud Firestore:** heavier but adds history and audit trail (who toggled what, when)

For a small team (< 20 concurrent users), a single Cloud Run instance with `min-instances: 1` (always warm) is simpler and sufficient. Add to `project.toml`:

```toml
[cloudrun]
min_instances = 1
```

This keeps one instance alive at all times so the background-thread-to-polling transition is not urgent.

### Polling frequency and SSH load

Each poll cycle opens 2 SSH connections (one per relay board) + 2 more for hubs. At 5-second intervals with multiple users that's manageable, but as bench count grows:

- **Increase poll interval** to 15-30s for production (hardware doesn't change state that fast)
- **Parallelize SSH calls** with `concurrent.futures.ThreadPoolExecutor` in `_poll()` — all device checks are independent
- **SSH connection multiplexing**: add `-o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=60` to SSH args to reuse connections across calls

### Adding new device types

Each device type is an independent driver class in `devices/`. To add a new one (e.g., PCAN interface, LabJack T7):

1. Add a new `devices/pcan.py` with a `check(cfg)` method returning a standard dict with `layer`, `status`, `error`, `diagnosis`
2. Add the device config section to `bench_config.yaml`
3. Wire it in `app.py` `_poll()` the same way other devices are wired

The layered status model (`layer: 0-3`) is consistent across all drivers and the frontend already handles any status generically.

### Authentication and access control

Currently: Apps Platform IAP handles Google login — anyone at Applied with a Google account can access the app.

If you need finer access control (e.g., read-only for most people, toggle access only for bench owners):
- Add a `X-Goog-Authenticated-User-Email` header check on toggle endpoints (IAP injects this)
- Maintain an allowlist in config or as a secret

---

## Known Issues and Next Steps

| Item | Status | Notes |
|------|--------|-------|
| pyftdi on elpaso | In progress | Preamble searches Bazel cache — needs validation on actual elpaso |
| `sudo rmmod ftdi_sio` on elpaso | One-time bench setup | Must be done once before relay checks work |
| Apps Platform deploy | Not started | Changes listed above needed first |
| PCAN interface status | Not started | Simple: `ip link show canX` via SSH, no libraries needed |
| LabJack T7 monitoring | Not started | Ethernet port on bench? If yes: Modbus TCP from server, no bench deps |
| SSH key for Cloud Run | Not started | Needs `apps-platform app secret set SSH_PRIVATE_KEY` |
