#!/usr/bin/env python3
"""Hardware bench dashboard — multi-bench view, runs on dedicated host."""
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import os
import yaml
from flask import Flask, Response, abort, jsonify, render_template, request

from devices.buildkite_agent import BuildkiteAgent
from devices.denkovi_relay import DenkoviRelay
from devices.ethernet_device import EthernetDevice
from devices.hub import StarTechHub
from devices.intrepid import IntrepidDevice
from devices.labjack import LabJackDevice
from devices.relay import SainsmartRelay
from devices.serial_device import SerialDevice

APP_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="templates")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_config_file = os.environ.get("BENCH_CONFIG", "benches_config.yaml")
with open(APP_DIR / _config_file) as f:
    _config = yaml.safe_load(f)

_global_cfg = _config.get("global", {})
_poll_interval: float = _global_cfg.get("poll_interval_s", 10)
_server_cfg = _global_cfg.get("server", {})


# ---------------------------------------------------------------------------
# Per-bench state
# ---------------------------------------------------------------------------

ENUMERATION_GRACE_S = 30.0


@dataclass
class BenchContext:
    cfg: dict
    state: dict = field(default_factory=lambda: {
        "hubs": [], "relays": [], "serials": [], "denkovi": [], "intrepid": [],
        "agent": None, "timestamp": 0.0,
    })
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: list = field(default_factory=list)
    subscribers_lock: threading.Lock = field(default_factory=threading.Lock)
    agent: Any = None
    last_device_ok: dict = field(default_factory=dict)
    agent_idle_since: float = 0.0


def _make_bench_context(cfg: dict) -> BenchContext:
    ctx = BenchContext(cfg=cfg)
    bk = cfg.get("buildkite", {})
    ssh = cfg["ssh"]
    if bk:
        ctx.agent = BuildkiteAgent(
            bk.get("service", "buildkite-agent-bk.service"),
            bk.get("agent_name", "unknown"),
            bk.get("queue", "unknown"),
            ssh["host"], ssh["user"],
        )
    return ctx


_benches: dict[str, BenchContext] = {
    cfg["name"]: _make_bench_context(cfg)
    for cfg in _config["benches"]
}

_fleet_subscribers: list[queue.SimpleQueue] = []
_fleet_lock = threading.Lock()


# ---------------------------------------------------------------------------
# powered_off logic
# ---------------------------------------------------------------------------

def _hub_port_on(hub_states: list[dict], hub_index: int, port: int) -> bool | None:
    if hub_index >= len(hub_states):
        return None
    port_states = hub_states[hub_index].get("port_states") or []
    if port >= len(port_states):
        return None
    return port_states[port]


def _apply_grace_period(result: dict, key: str, ctx: BenchContext) -> dict:
    """Replace transient MISSING with ENUMERATING if device was OK within grace window."""
    if result.get("status") == "ok":
        ctx.last_device_ok[key] = time.time()
    elif result.get("status") == "missing":
        last_ok = ctx.last_device_ok.get(key, 0)
        if time.time() - last_ok < ENUMERATION_GRACE_S:
            result["status"] = "enumerating"
            result["diagnosis"] = "Not found in USB device tree — enumerating after port toggle"
            result["error"] = None
    return result


def _apply_powered_off(result: dict, cfg: dict, hub_states: list[dict]) -> dict:
    if result.get("status") != "missing":
        return result
    pb = cfg.get("powered_by")
    if pb is None:
        return result
    hub = hub_states[pb["hub_index"]] if pb["hub_index"] < len(hub_states) else None
    if hub and hub.get("status") in ("comm_error", "driver_error", "unknown"):
        result["status"] = "unknown"
        result["diagnosis"] = f"Hub {hub.get('status', 'error')} — device status uncertain"
        result["error"] = None
        return result
    on = _hub_port_on(hub_states, pb["hub_index"], pb["port"])
    if on is False:
        result["status"] = "powered_off"
        result["diagnosis"] = "Hub port is off — device has no power"
        result["error"] = None
    return result


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

_AGENT_IDLE_COOLDOWN_S = 30.0

def _agent_active(ctx: BenchContext) -> bool:
    """Return True if hub/relay polling should be paused.

    Pauses when agent service is active (idle or busy). Also enforces a
    30s cooldown after going idle — prevents a new build starting between
    poll cycles from racing with hardware checks."""
    with ctx.lock:
        agent = ctx.state.get("agent")
        idle_since = ctx.agent_idle_since

    if not agent:
        return False

    service_active = agent.get("service") == "active"

    if service_active:
        if agent.get("busy"):
            # reset cooldown timer whenever busy
            with ctx.lock:
                ctx.agent_idle_since = time.time()
        elif idle_since == 0.0:
            # first time we see idle — start cooldown
            with ctx.lock:
                ctx.agent_idle_since = time.time()
        return True

    # service is stopped — check if cooldown has elapsed
    if idle_since > 0.0 and time.time() - idle_since < _AGENT_IDLE_COOLDOWN_S:
        return True

    with ctx.lock:
        ctx.agent_idle_since = 0.0
    return False


def _poll_bench(ctx: BenchContext) -> dict:
    ssh = ctx.cfg["ssh"]
    host, user = ssh["host"], ssh["user"]
    sudo_user = ssh.get("sudo_user")
    python_interp = ssh.get("python_interpreter", "python3")
    devices = ctx.cfg.get("devices", {})

    # Skip hub and relay checks while a CI build is running to avoid
    # concurrent serial port access corrupting the HIL test setup.
    agent_active = _agent_active(ctx)

    if agent_active:
        with ctx.lock:
            prev = ctx.state
        hubs = [{**h, "diagnosis": "Agent active — checks paused to avoid contention",
                 "error": None} for h in prev.get("hubs", [])]
        relays = [{**r, "diagnosis": "Agent active — checks paused to avoid contention",
                   "error": None} for r in prev.get("relays", [])]
        intrepid = prev.get("intrepid", [])
    else:
        hubs = [
            StarTechHub(h["serial_path"], host, user).check(h)
            for h in devices.get("usb_hubs", [])
        ]
        relays = [
            _apply_grace_period(
                _apply_powered_off(
                    SainsmartRelay(r["identifier"], host, user,
                                   sudo_user=sudo_user,
                                   python_interpreter=python_interp).check(r),
                    r, hubs,
                ),
                r["identifier"], ctx,
            )
            for r in devices.get("relay_boards", [])
        ]
    serials = [
        _apply_grace_period(
            _apply_powered_off(
                SerialDevice(s["identifier"], host, user).check(s),
                s, hubs,
            ),
            s["identifier"], ctx,
        )
        for s in devices.get("serial_devices", [])
    ]
    labjacks = [
        _apply_grace_period(
            _apply_powered_off(
                LabJackDevice(host, user).check(lj),
                lj, hubs,
            ),
            f"labjack_{lj['name']}", ctx,
        )
        for lj in devices.get("labjack_devices", [])
    ]
    eth_devices = [
        EthernetDevice(host, user).check(e)
        for e in devices.get("ethernet_devices", [])
    ]
    denkovi = [
        DenkoviRelay(d["host"], d.get("port", 80), d.get("password", "admin")).check(d)
        for d in devices.get("denkovi_relays", [])
    ]
    intrepid = [
        IntrepidDevice(i["serial_number"], host, user).check(i)
        for i in devices.get("intrepid_devices", [])
    ]
    agent = ctx.agent.status() if ctx.agent else None
    return {
        "hubs": hubs, "relays": relays, "serials": serials + labjacks + eth_devices,
        "denkovi": denkovi, "intrepid": intrepid, "agent": agent, "timestamp": time.time(),
    }


def _fleet_summary() -> dict:
    benches = []
    for name, ctx in _benches.items():
        with ctx.lock:
            state = dict(ctx.state)
        all_devices = (
            state.get("hubs", []) + state.get("relays", []) +
            state.get("serials", []) + state.get("denkovi", [])
        )
        n_ok = sum(1 for d in all_devices if d.get("status") == "ok")
        n_off = sum(1 for d in all_devices if d.get("status") == "powered_off")
        n_uncertain = sum(1 for d in all_devices if d.get("status") in ("unknown", "enumerating"))
        n_issues = sum(1 for d in all_devices
                       if d.get("status") in ("missing", "comm_error", "driver_error"))
        total = len(all_devices)

        if total == 0 or state.get("timestamp", 0) == 0:
            bench_status = "unknown"
        elif n_issues == 0 and n_uncertain == 0:
            bench_status = "ok"
        elif n_ok == 0 and n_off == 0 and n_uncertain == 0:
            bench_status = "down"
        else:
            bench_status = "issues"

        agent = state.get("agent")
        agent_status = None
        if agent:
            if agent.get("busy"):
                agent_status = "busy"
            elif agent.get("service") == "active":
                agent_status = "idle"
            else:
                agent_status = "stopped"

        benches.append({
            "name": name,
            "hostname": ctx.cfg.get("hostname", ""),
            "ip": ctx.cfg.get("ip", ""),
            "status": bench_status,
            "n_ok": n_ok,
            "n_off": n_off,
            "n_uncertain": n_uncertain,
            "n_issues": n_issues,
            "total": total,
            "agent_status": agent_status,
            "timestamp": state.get("timestamp", 0),
        })
    return {"benches": benches, "timestamp": time.time()}


def _broadcast_bench(ctx: BenchContext, data: dict) -> None:
    payload = json.dumps(data)
    with ctx.subscribers_lock:
        for q in list(ctx.subscribers):
            q.put_nowait(payload)


def _broadcast_fleet() -> None:
    payload = json.dumps(_fleet_summary())
    with _fleet_lock:
        for q in list(_fleet_subscribers):
            q.put_nowait(payload)


def _poll_loop(ctx: BenchContext) -> None:
    while True:
        try:
            new_state = _poll_bench(ctx)
        except Exception as exc:
            new_state = {
                "hubs": [], "relays": [], "serials": [], "denkovi": [],
                "agent": None, "timestamp": time.time(), "error": str(exc),
            }
        with ctx.lock:
            ctx.state.clear()
            ctx.state.update(new_state)
        _broadcast_bench(ctx, new_state)
        _broadcast_fleet()
        time.sleep(_poll_interval)


def _repoll_after_toggle(ctx: BenchContext) -> None:
    time.sleep(0.5)
    try:
        new_state = _poll_bench(ctx)
        with ctx.lock:
            ctx.state.clear()
            ctx.state.update(new_state)
        _broadcast_bench(ctx, new_state)
        _broadcast_fleet()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def fleet_view():
    return render_template("fleet.html", fleet=_fleet_summary())


@app.route("/bench/<name>")
def bench_view(name: str):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    with ctx.lock:
        state = dict(ctx.state)
    return render_template("bench.html", bench=ctx.cfg, state=state)


@app.route("/events")
def fleet_events():
    q: queue.SimpleQueue = queue.SimpleQueue()
    q.put_nowait(json.dumps(_fleet_summary()))
    with _fleet_lock:
        _fleet_subscribers.append(q)

    def stream():
        try:
            while True:
                try:
                    yield f"data: {q.get(timeout=25)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _fleet_lock:
                if q in _fleet_subscribers:
                    _fleet_subscribers.remove(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/bench/<name>/events")
def bench_events(name: str):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    q: queue.SimpleQueue = queue.SimpleQueue()
    with ctx.lock:
        q.put_nowait(json.dumps(ctx.state))
    with ctx.subscribers_lock:
        ctx.subscribers.append(q)

    def stream():
        try:
            while True:
                try:
                    yield f"data: {q.get(timeout=25)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with ctx.subscribers_lock:
                if q in ctx.subscribers:
                    ctx.subscribers.remove(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/bench/<name>/hub/<int:hi>/port/<int:pi>/toggle", methods=["POST"])
def toggle_hub_port(name: str, hi: int, pi: int):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    hubs_cfg = ctx.cfg.get("devices", {}).get("usb_hubs", [])
    if hi >= len(hubs_cfg):
        return jsonify({"error": "hub index out of range"}), 404
    ssh = ctx.cfg["ssh"]
    try:
        result = StarTechHub(hubs_cfg[hi]["serial_path"], ssh["host"], ssh["user"]).toggle(pi)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


@app.route("/api/bench/<name>/relay/<int:ri>/channel/<int:ci>/toggle", methods=["POST"])
def toggle_relay_channel(name: str, ri: int, ci: int):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    relays_cfg = ctx.cfg.get("devices", {}).get("relay_boards", [])
    if ri >= len(relays_cfg):
        return jsonify({"error": "relay index out of range"}), 404
    ssh = ctx.cfg["ssh"]
    try:
        result = SainsmartRelay(
            relays_cfg[ri]["identifier"], ssh["host"], ssh["user"],
            sudo_user=ssh.get("sudo_user"),
            python_interpreter=ssh.get("python_interpreter", "python3"),
        ).toggle(ci)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


@app.route("/api/bench/<name>/denkovi/<int:di>/channel/<int:ci>/toggle", methods=["POST"])
def toggle_denkovi_channel(name: str, di: int, ci: int):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    denkovi_cfg = ctx.cfg.get("devices", {}).get("denkovi_relays", [])
    if di >= len(denkovi_cfg):
        return jsonify({"error": "denkovi index out of range"}), 404
    cfg = denkovi_cfg[di]
    try:
        result = DenkoviRelay(cfg["host"], cfg.get("port", 80), cfg.get("password", "admin")).toggle(ci)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


@app.route("/api/bench/<name>/agent/stop", methods=["POST"])
def agent_stop(name: str):
    ctx = _benches.get(name)
    if not ctx or not ctx.agent:
        return jsonify({"error": "no agent configured"}), 404
    try:
        result = ctx.agent.stop()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


@app.route("/api/bench/<name>/agent/start", methods=["POST"])
def agent_start(name: str):
    ctx = _benches.get(name)
    if not ctx or not ctx.agent:
        return jsonify({"error": "no agent configured"}), 404
    try:
        result = ctx.agent.start()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


@app.route("/api/bench/<name>/power-cycle-dut", methods=["POST"])
def power_cycle_dut(name: str):
    ctx = _benches.get(name)
    if not ctx:
        abort(404)
    devices = ctx.cfg.get("devices", {})
    ssh = ctx.cfg["ssh"]

    # Search Sainsmart relay_boards first (Palm Proto)
    for relay_cfg in devices.get("relay_boards", []):
        for ci, ch in enumerate(relay_cfg.get("channels", [])):
            if ch.get("label") == "main_12v":
                relay = SainsmartRelay(
                    relay_cfg["identifier"], ssh["host"], ssh["user"],
                    sudo_user=ssh.get("sudo_user"),
                    python_interpreter=ssh.get("python_interpreter", "python3"),
                )
                try:
                    relay.set_channel(ci, False)
                    time.sleep(2)
                    relay.set_channel(ci, True)
                except Exception as exc:
                    return jsonify({"error": str(exc)}), 500
                threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
                return jsonify({"success": True})

    # Search Denkovi denkovi_relays (High Pine / Palm 2.0)
    for di, denkovi_cfg in enumerate(devices.get("denkovi_relays", [])):
        for ci, ch in enumerate(denkovi_cfg.get("channels", [])):
            label = ch.get("label", "")
            if "kl30" in label.lower() or "main_12v" in label.lower() or "pwr" in label.lower():
                denkovi = DenkoviRelay(
                    denkovi_cfg["host"],
                    denkovi_cfg.get("port", 80),
                    denkovi_cfg.get("password", "admin"),
                )
                try:
                    denkovi.set_channel(ci, False)
                    time.sleep(2)
                    denkovi.set_channel(ci, True)
                except Exception as exc:
                    return jsonify({"error": str(exc)}), 500
                threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
                return jsonify({"success": True})

    return jsonify({"error": "No power channel found on this bench"}), 404


@app.route("/api/bench/<name>/agent/run-test", methods=["POST"])
def agent_run_test(name: str):
    ctx = _benches.get(name)
    if not ctx or not ctx.agent:
        return jsonify({"error": "no agent configured"}), 404
    data = request.get_json(silent=True) or {}
    hil_pipeline = data.get("hil_pipeline", "vos")
    branch = data.get("branch", "main")
    try:
        result = ctx.agent.run_test(hil_pipeline=hil_pipeline, branch=branch)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    threading.Thread(target=_repoll_after_toggle, args=(ctx,), daemon=True).start()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Hub + Tester routes
# ---------------------------------------------------------------------------

@app.route("/hub")
def hub_view():
    fleet = _fleet_summary()
    busy = sum(1 for b in fleet["benches"] if b.get("agent_status") == "busy")
    idle = sum(1 for b in fleet["benches"] if b.get("agent_status") == "idle")
    total = len(fleet["benches"])
    return render_template("hub.html", total=total, busy=busy, idle=idle)


@app.route("/tester")
def tester_view():
    return render_template("tester.html")


@app.route("/api/tester/connect", methods=["POST"])
def tester_connect():
    from tests.discovery import discover
    data = request.get_json(silent=True) or {}
    host = data.get("host", "").strip()
    if not host:
        return jsonify({"error": "host required"}), 400
    result = discover(host)
    return jsonify(result)


@app.route("/api/tester/run", methods=["POST"])
def tester_run():
    from tests import (can_loopback, relay_toggle, denkovi_toggle,
                       hub_port_cycle, intrepid_interfaces, ethernet_ping)
    data = request.get_json(silent=True) or {}
    host = data.get("host", "")
    test = data.get("test", "")
    params = data.get("params", {})

    try:
        if test == "can_loopback":
            result = can_loopback.run(host, "dev",
                                      params["tx_iface"], params["rx_iface"])
        elif test == "relay_toggle":
            result = relay_toggle.run(host, "dev",
                                      params["identifier"],
                                      int(params.get("channel_idx", 0)),
                                      sudo_user="bk")
        elif test == "denkovi_toggle":
            result = denkovi_toggle.run(params["denkovi_host"],
                                        int(params.get("port", 80)),
                                        params.get("password", "admin"),
                                        int(params.get("channel_idx", 0)))
        elif test == "hub_port_cycle":
            result = hub_port_cycle.run(host, "dev",
                                        params["serial_path"],
                                        int(params.get("port_idx", 0)))
        elif test == "intrepid_interfaces":
            result = intrepid_interfaces.run(host, "dev",
                                             params["serial_number"],
                                             int(params.get("expected_count", 1)))
        elif test == "ethernet_ping":
            result = ethernet_ping.run(host, "dev",
                                       params["target_ip"],
                                       params.get("device_name", ""))
        else:
            return jsonify({"error": f"Unknown test: {test}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result.to_dict())


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def main() -> None:
    for ctx in _benches.values():
        threading.Thread(target=_poll_loop, args=(ctx,), daemon=True).start()

    host = _server_cfg.get("host", "127.0.0.1")
    port = _server_cfg.get("port", 5000)
    print(f"Hardware dashboard: http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
