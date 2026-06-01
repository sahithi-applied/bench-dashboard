# Bench Dashboard

Live hardware dashboard for monitoring and controlling HIL benches at Applied Intuition.
Hosted on **emersonai** — a dedicated Minisforum mini PC on the bench network. Anyone on
the internal network can access it at `http://10.80.11.13:5000` without installing anything.

---

## What It Does

- **Fleet view** — all 12 benches at a glance with live device status breakdown
- **Per-bench detail** — click any bench to see USB hubs, relay boards, serial devices,
  LabJack T7, FlexRay adapter, and BuildKite agent status
- **Hub and relay control** — toggle USB hub ports and relay channels directly from the browser
- **Agent control** — stop/restore BuildKite CI agent via `bk-debug-start` / `bk-debug-stop`
- **Live updates** — Server-Sent Events, no page refresh needed
- **Smart status** — distinguishes POWERED OFF (hub port off), ENUMERATING (transient USB),
  UNKNOWN (hub comm error), and MISSING (genuine hardware issue)

---

## Architecture

```
Browser (anyone on internal network)
        |
   http://10.80.11.13:5000
        |
   emersonai (Minisforum, Ubuntu 22.04)
   esi-dashboard user, systemd service: bench-dashboard
        |
        ├── SSH (dev@) → each bench host (parallel, one thread per bench)
        │         ├── USB hub state / toggle      (termios)
        │         ├── Relay state / toggle        (pyftdi, runs as bk user)
        │         ├── Serial device presence      (/sys/bus/usb/devices)
        │         ├── LabJack T7 presence         (USB vendor/product ID)
        │         ├── FlexRay adapter + ping      (ip link + ping)
        │         └── BuildKite agent status      (systemctl)
        │
        └── SSH (bk-debug@) → bench host (agent stop/restore only)
                  ├── bk-debug-start  →  removes bench from CI queue
                  └── bk-debug-stop   →  puts bench back in CI queue
```

No files are installed on any bench. All bench-side checks are Python snippets sent
over SSH and executed inline. Benches only need Python 3 stdlib (relay checks use
pyftdi from the existing Bazel cache).

---

## Bench Config

`benches_config.yaml` is the single source of truth for all 12 benches. It is
**auto-generated** from `hil_benches.pkl` in core-stack via a GitHub Action that runs
hourly — when the firmware team updates the pkl, a PR is opened automatically.

To manually regenerate:
```bash
python3 scripts/sync_from_pkl.py /path/to/hil_benches.pkl --out benches_config.yaml
```

---

## Deployment (emersonai)

emersonai runs the dashboard as a systemd service that survives reboots and auto-restarts
on crash.

**SSH access:**
```bash
ssh esi-dashboard@10.80.11.13
```

**Service management:**
```bash
sudo systemctl status bench-dashboard
sudo systemctl restart bench-dashboard
sudo journalctl -u bench-dashboard -f   # logs
```

**Updating the dashboard:**
```bash
# From your MacBook — sync latest code to emersonai
rsync -av --exclude='.venv' --exclude='__pycache__' \
  /path/to/bench-dashboard/ esi-dashboard@10.80.11.13:~/dashboard/
ssh esi-dashboard@10.80.11.13 "sudo systemctl restart bench-dashboard"
```

---

## SSH Key Setup (one-time per bench)

emersonai needs passwordless SSH to each bench as both `dev` (polling) and `bk-debug`
(agent control):

```bash
# On emersonai
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

for ip in 10.80.11.28 10.80.10.25 10.80.10.18 10.80.11.101 10.80.10.180 \
          10.80.11.103 10.80.9.213 10.80.11.100 10.80.9.211 10.80.11.102 \
          10.80.9.212 10.80.9.210; do
  ssh-copy-id dev@$ip
  ssh-copy-id bk-debug@$ip
done
```

---

## File Structure

```
bench-dashboard/
├── app.py                    # Flask app: routing, multi-bench polling, SSE
├── benches_config.yaml       # All 12 benches — auto-synced from hil_benches.pkl
├── requirements.txt
├── run.sh                    # Dev runner (sets up venv, starts app)
├── devices/
│   ├── ssh_runner.py         # SSH exec utility (base64-encoded scripts)
│   ├── hub.py                # StarTech USB hub check + toggle
│   ├── relay.py              # Sainsmart relay check + toggle (pyftdi)
│   ├── serial_device.py      # USB serial presence (/dev/serial/by-id)
│   ├── labjack.py            # LabJack T7 USB presence (0cd5:0007)
│   ├── ethernet_device.py    # Ethernet adapter + device ping
│   ├── denkovi_relay.py      # Denkovi HTTP relay check + toggle
│   ├── buildkite_agent.py    # BuildKite agent status / stop / restore
│   └── ssh_runner.py         # Shared SSH exec
├── templates/
│   ├── fleet.html            # Fleet overview page
│   └── bench.html            # Per-bench detail page
├── scripts/
│   └── sync_from_pkl.py      # Parses hil_benches.pkl → benches_config.yaml
└── .github/workflows/
    └── sync-benches.yaml     # Hourly sync from core-stack hil_benches.pkl
```

---

## Benches

| Bench | IP | Notes |
|-------|----|-------|
| elpaso-hw-bench-5 | 10.80.11.28 | LabJack T7, FlexRay |
| coconut-hw-bench-18 | 10.80.10.25 | FlexRay |
| mater-fw-hil-19 | 10.80.10.18 | |
| themis-fw-hil-12 | 10.80.11.101 | LabJack T7, FlexRay |
| helen-fw-hil-20 | 10.80.10.180 | FlexRay |
| cassiopeia-fw-hil-17 | 10.80.11.103 | |
| brisket-hw-bench-4 | 10.80.9.213 | |
| crius-fw-hil-1 | 10.80.11.100 | |
| hyperion-fw-hil-4 | 10.80.9.211 | |
| phoebe-fw-hil-8 | 10.80.11.102 | |
| caniaccombo-fw-hil-10 | 10.80.9.212 | |
| drpepper-fw-hil-11 | 10.80.9.210 | |
