"""Auto-discover hardware devices on a target machine via SSH."""
from __future__ import annotations
import textwrap
from devices.ssh_runner import ssh_exec

_DISCOVERY_SCRIPT = textwrap.dedent("""\
    import json, subprocess, re, os
    from pathlib import Path

    result = {
        "usb_hubs": [],
        "sainsmart_relays": [],
        "intrepid": [],
        "serial_devices": [],
        "labjack": [],
        "error": None,
    }

    # ── lsusb scan ──────────────────────────────────────────────────────────
    KNOWN_VIDS = {
        "0403:6001": "FTDI FT232R (Hub/Relay controller)",
        "0403:6011": "FTDI FT4232H",
        "0cd5:0104": "LabJack T7",
        "1a86:7523": "CH340 (Sainsmart relay)",
        "04b4:00f3": "Cypress FX2 (FL3x)",
        "0525:a4a2": "Digi AnywhereUSB",
    }
    STARTECH_VIDS = {"1d6b:0003", "05e3:0626", "05e3:0610", "0bda:5411", "0bda:0411"}

    r = subprocess.run(["lsusb"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        m = re.search(r"ID ([0-9a-f]{4}:[0-9a-f]{4})", line)
        if not m:
            continue
        vid_pid = m.group(1)
        if "StarTech" in line or vid_pid in STARTECH_VIDS:
            result["usb_hubs"].append({"description": line.strip()})
        elif vid_pid == "0cd5:0104":
            result["labjack"].append({"description": line.strip()})

    # ── Sainsmart relay scan via /sys/bus/usb ────────────────────────────────
    usb_path = Path("/sys/bus/usb/devices")
    if usb_path.exists():
        for serial_file in usb_path.glob("*/serial"):
            try:
                serial = serial_file.read_text().strip()
                if len(serial) == 8 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in serial):
                    prod_file = serial_file.parent / "product"
                    product = prod_file.read_text().strip() if prod_file.exists() else ""
                    if "SAINSMART" in product.upper() or "CH340" in product.upper() or "USB2.0" in product.upper():
                        result["sainsmart_relays"].append({
                            "identifier": serial,
                            "product": product,
                        })
            except Exception:
                pass

    # ── Intrepid SocketCAN interfaces ────────────────────────────────────────
    r2 = subprocess.run(["ip", "link", "show"], capture_output=True, text=True)
    intrepid_re = re.compile(r"\\d+:\\s+(\\w+_([A-F0-9]{6})):")
    seen_serials = set()
    for m in intrepid_re.finditer(r2.stdout):
        iface, serial = m.group(1), m.group(2)
        if serial not in seen_serials:
            seen_serials.add(serial)
            ifaces = re.findall(rf"\\w+_{serial}", r2.stdout)
            result["intrepid"].append({
                "serial_number": serial,
                "interfaces": list(dict.fromkeys(ifaces)),
            })

    # ── Serial devices ────────────────────────────────────────────────────────
    serial_dir = Path("/dev/serial/by-id")
    if serial_dir.exists():
        for p in sorted(serial_dir.iterdir()):
            result["serial_devices"].append({
                "path": str(p),
                "name": p.name,
            })

    print(json.dumps(result))
""")

_DENKOVI_CHECK_SCRIPT = textwrap.dedent("""\
    import json, socket
    results = []
    for ip in denkovi_ips:
        try:
            with socket.create_connection((ip, 80), timeout=2):
                results.append({"host": ip, "reachable": True})
        except OSError:
            results.append({"host": ip, "reachable": False})
    print(json.dumps(results))
""")

DENKOVI_PROBE_IPS = ["192.168.1.100", "192.168.1.101", "192.168.1.102"]


def discover(ssh_host: str, ssh_user: str = "dev") -> dict:
    """SSH into target machine and discover all connected hardware devices."""
    try:
        devices = ssh_exec(ssh_host, ssh_user, _DISCOVERY_SCRIPT, timeout=20)
    except Exception as exc:
        return {"error": str(exc), "usb_hubs": [], "sainsmart_relays": [],
                "intrepid": [], "serial_devices": [], "labjack": [], "denkovi": []}

    # Probe Denkovi IPs directly from esi-dashboard (HTTP)
    import socket
    denkovi = []
    for ip in DENKOVI_PROBE_IPS:
        try:
            with socket.create_connection((ip, 80), timeout=2):
                denkovi.append({"host": ip, "reachable": True})
        except OSError:
            pass

    devices["denkovi"] = denkovi
    devices.setdefault("error", None)
    return devices
