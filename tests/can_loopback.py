"""CAN loopback test — send a frame on one channel, receive on another."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_CAN_LOOPBACK_SCRIPT = textwrap.dedent("""\
    import json, subprocess, time, re, sys

    result = {"status": "fail", "input": "", "output": "", "error": None}

    TX_IFACE = tx_iface
    RX_IFACE = rx_iface
    FRAME_ID  = "123"
    FRAME_DATA = "DEADBEEF01020304"
    BITRATE   = 500000
    TIMEOUT_MS = 2000

    def bring_up(iface):
        subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                       capture_output=True)
        r = subprocess.run(
            ["sudo", "ip", "link", "set", iface, "up", "type", "can",
             "bitrate", str(BITRATE)],
            capture_output=True, text=True)
        return r.returncode == 0, r.stderr.strip()

    def bring_down(iface):
        subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                       capture_output=True)

    # Bring up both interfaces
    ok_tx, err_tx = bring_up(TX_IFACE)
    ok_rx, err_rx = bring_up(RX_IFACE)
    if not ok_tx or not ok_rx:
        result.update(status="error",
                      error=f"Failed to bring up interfaces: tx={err_tx} rx={err_rx}")
        print(json.dumps(result)); sys.exit(0)

    result["input"] = f"TX: {TX_IFACE}  RX: {RX_IFACE}  frame: {FRAME_ID}#{FRAME_DATA}  bitrate: {BITRATE}"

    try:
        # Start candump on RX in background
        dump = subprocess.Popen(
            ["candump", "-T", str(TIMEOUT_MS), "-n", "1", RX_IFACE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.1)

        # Send frame on TX
        subprocess.run(["cansend", TX_IFACE, f"{FRAME_ID}#{FRAME_DATA}"],
                       capture_output=True, text=True)

        stdout, stderr = dump.communicate(timeout=3)
        if FRAME_ID.lower() in stdout.lower() and FRAME_DATA.lower() in stdout.lower():
            result.update(status="pass", output=f"Received: {stdout.strip()}")
        else:
            result.update(status="fail",
                          output=stdout.strip() or "(no frame received)",
                          error=f"Frame not received within {TIMEOUT_MS}ms — check CAN wiring/termination")
    except Exception as exc:
        result.update(status="error", error=str(exc))
    finally:
        bring_down(TX_IFACE)
        bring_down(RX_IFACE)

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, tx_iface: str, rx_iface: str) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="CAN Loopback",
        device_name=f"Intrepid ({tx_iface} → {rx_iface})",
        status="running",
    )
    try:
        script = (
            f"tx_iface = {tx_iface!r}\n"
            f"rx_iface = {rx_iface!r}\n"
            + _CAN_LOOPBACK_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=15)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
