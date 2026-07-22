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
    # NOTE: 1d6b:* are Linux Foundation root hubs — present on every machine,
    # never real StarTech hardware. Genesys (05e3) and Realtek (0bda) hub
    # chips are what StarTech units actually enumerate as.
    HUB_CHIP_VIDS = {"05e3:0626", "05e3:0610", "0bda:5411", "0bda:0411"}

    hub_chips = []
    r = subprocess.run(["lsusb"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        m = re.search(r"ID ([0-9a-f]{4}:[0-9a-f]{4})", line)
        if not m:
            continue
        vid_pid = m.group(1)
        if "StarTech" in line or vid_pid in HUB_CHIP_VIDS:
            hub_chips.append(line.strip())
        elif vid_pid == "0cd5:0104":
            result["labjack"].append({"description": line.strip()})

    # StarTech hub power control goes through an FTDI FT232R UART — the
    # serial_path used in benches_config.yaml. Pair each FT232R control port
    # with the hub-chip evidence from lsusb.
    serial_dir = Path("/dev/serial/by-id")
    if serial_dir.exists():
        for p in sorted(serial_dir.iterdir()):
            if "FT232R" in p.name and p.name.endswith("-port0"):
                result["usb_hubs"].append({
                    "serial_path": str(p),
                    "name": p.name,
                    "description": hub_chips[0] if hub_chips else "",
                })

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
    intrepid_re = re.compile(r"\\d+:\\s+(\\w+_([0-9A-Za-z]{6})):")
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

    # Probe Denkovi IPs from the target machine — 192.168.1.x is only
    # routable from the bench, not from esi-dashboard.
    denkovi = []
    try:
        script = f"denkovi_ips = {DENKOVI_PROBE_IPS!r}\n" + _DENKOVI_CHECK_SCRIPT
        probes = ssh_exec(ssh_host, ssh_user, script, timeout=15)
        denkovi = [p for p in probes if p.get("reachable")]
    except Exception:
        pass

    devices["denkovi"] = denkovi
    devices.setdefault("error", None)
    return devices
