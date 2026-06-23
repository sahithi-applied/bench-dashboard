#!/usr/bin/env python3
"""Parse hil_benches.pkl and regenerate benches_config.yaml.

Usage:
    python3 scripts/sync_from_pkl.py path/to/hil_benches.pkl [--out benches_config.yaml]
"""
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HubChannel:
    name: str
    idx: int
    description: str = ""


@dataclass
class Hub:
    var_name: str
    name: str
    serial_port: str
    channels: list[HubChannel] = field(default_factory=list)

    def port_idx(self, channel_name: str) -> Optional[int]:
        for ch in self.channels:
            if ch.name == channel_name:
                return ch.idx
        return None


@dataclass
class RelayChannel:
    label: str
    idx: int
    inverted: bool = False
    default: bool = False


@dataclass
class Relay:
    var_name: str
    name: str
    identifier: str
    channels: list[RelayChannel] = field(default_factory=list)
    depends_hub: Optional[str] = None
    depends_port: Optional[str] = None


@dataclass
class LabJack:
    var_name: str
    name: str
    depends_hub: Optional[str] = None
    depends_port: Optional[str] = None


@dataclass
class EthernetAdapter:
    var_name: str
    name: str
    interface: str
    device_ip: Optional[str] = None


@dataclass
class SerialInstrument:
    name: str
    identifier: str


@dataclass
class DenkoviDevice:
    var_name: str
    name: str
    host: str
    port: int = 80
    password: str = "admin"
    channels: list[dict] = field(default_factory=list)


@dataclass
class IntrepidDevice:
    var_name: str
    name: str
    serial_number: str
    channel_names: list[str] = field(default_factory=list)


@dataclass
class Bench:
    benchname: str
    hostname: str
    hubs: list[Hub] = field(default_factory=list)
    relays: list[Relay] = field(default_factory=list)
    labjacks: list[LabJack] = field(default_factory=list)
    eth_adapters: list[EthernetAdapter] = field(default_factory=list)
    serial_instruments: list[SerialInstrument] = field(default_factory=list)
    denkovi_devices: list[DenkoviDevice] = field(default_factory=list)
    intrepid_devices: list[IntrepidDevice] = field(default_factory=list)
    nodes_order: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _extract_blocks(text: str) -> list[str]:
    """Extract top-level hidden const bench blocks."""
    blocks = []
    pattern = re.compile(r'hidden\s+const\s+\w+_bench\s*:.*?=\s*new\s*\{', re.DOTALL)
    for m in pattern.finditer(text):
        start = m.start()
        brace_start = text.index('{', m.start())
        depth, i = 1, brace_start + 1
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        blocks.append(text[start:i])
    return blocks


def _first(pattern: str, text: str, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def _extract_local_block(text: str, var_name: str) -> Optional[str]:
    """Extract the full { } block for a local variable definition."""
    pattern = re.compile(
        rf'local\s+{re.escape(var_name)}\s*=\s*new\s+\S+\s*\{{', re.DOTALL
    )
    m = pattern.search(text)
    if not m:
        return None
    brace_start = text.index('{', m.start())
    depth, i = 1, brace_start + 1
    while i < len(text) and depth > 0:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[brace_start:i]


def _parse_hub(text: str, var_name: str) -> Optional[Hub]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    serial_port = _first(r'serial_port\s*=\s*"([^"]+)"', block) or ""
    hub = Hub(var_name=var_name, name=name, serial_port=serial_port)

    for m in re.finditer(
        r'\["(\w+)"\]\s*=\s*new\s*\{([^}]*)\}', block, re.DOTALL
    ):
        ch_name, ch_body = m.group(1), m.group(2)
        idx_m = re.search(r'channel_idx\s*=\s*(\d+)', ch_body)
        desc_m = re.search(r'description\s*=\s*"([^"]+)"', ch_body)
        if idx_m:
            hub.channels.append(HubChannel(
                name=ch_name,
                idx=int(idx_m.group(1)),
                description=desc_m.group(1) if desc_m else "",
            ))
    hub.channels.sort(key=lambda c: c.idx)
    return hub


def _parse_relay(text: str, var_name: str) -> Optional[Relay]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    identifier = _first(r'identifier\s*=\s*"([^"]+)"', block) or ""
    relay = Relay(var_name=var_name, name=name, identifier=identifier)

    for m in re.finditer(r'\["([^"]+)"\]\s*=\s*new\s*\{([^}]*)\}', block, re.DOTALL):
        ch_name, ch_body = m.group(1), m.group(2)
        if ch_name.startswith('_'):
            _m = re.search(r'channel_idx.*?(\d+)', ch_body)
            label = f"unused_ch{_m.group(1) if _m else '?'}"
        else:
            label = ch_name
        idx_m = re.search(r'channel_idx\s*=\s*(\d+)', ch_body)
        if idx_m:
            relay.channels.append(RelayChannel(
                label=label,
                idx=int(idx_m.group(1)),
                inverted='invert\s*=\s*true' in ch_body or bool(re.search(r'invert\s*=\s*true', ch_body)),
                default=bool(re.search(r'default_state\s*=\s*true', ch_body)),
            ))
    relay.channels.sort(key=lambda c: c.idx)

    dep_m = re.search(r'depends_on\s*=\s*new\s*\{[^}]*?(\w+)\.get_channel\("([^"]+)"\)', block)
    if dep_m:
        relay.depends_hub, relay.depends_port = dep_m.group(1), dep_m.group(2)
    return relay


def _parse_labjack(text: str, var_name: str) -> Optional[LabJack]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    lj = LabJack(var_name=var_name, name=name)
    dep_m = re.search(r'depends_on\s*=\s*new\s*\{[^}]*?(\w+)\.get_channel\("([^"]+)"\)', block)
    if dep_m:
        lj.depends_hub, lj.depends_port = dep_m.group(1), dep_m.group(2)
    return lj


def _parse_eth(text: str, var_name: str) -> Optional[EthernetAdapter]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    interface = _first(r'interface\s*=\s*"([^"]+)"', block) or ""
    route_m = re.search(r'host_routes\s*=\s*new\s*\{\s*"([^/]+)', block)
    device_ip = route_m.group(1).strip() if route_m else None
    return EthernetAdapter(var_name=var_name, name=name, interface=interface, device_ip=device_ip)


def _parse_denkovi(text: str, var_name: str) -> Optional[DenkoviDevice]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    host = _first(r'host\s*=\s*"([^"]+)"', block) or ""
    port = int(_first(r'port\s*=\s*(\d+)', block) or "80")
    password = _first(r'password\s*=\s*"([^"]+)"', block) or "admin"
    channels = []
    for m in re.finditer(r'\[(\d+)\]\s*=\s*new\s*\{([^}]*)\}', block, re.DOTALL):
        idx, ch_body = int(m.group(1)), m.group(2)
        label_m = re.search(r'name\s*=\s*"([^"]+)"', ch_body)
        label = label_m.group(1) if label_m else f"ch{idx}"
        channels.append({"label": label, "idx": idx})
    channels.sort(key=lambda c: c["idx"])
    return DenkoviDevice(var_name=var_name, name=name, host=host, port=port,
                         password=password, channels=channels)


def _parse_intrepid(text: str, var_name: str) -> Optional[IntrepidDevice]:
    block = _extract_local_block(text, var_name)
    if not block:
        return None
    name = _first(r'name\s*=\s*"([^"]+)"', block) or var_name
    serial = _first(r'serial_number\s*=\s*"([^"]+)"', block) or ""
    channel_slots = ["hscan", "hscan2", "hscan3", "hscan4", "hscan5", "hscan6", "hscan7",
                     "mscan", "dwcan09", "dwcan10", "dwcan11", "dwcan12", "dwcan13", "dwcan14", "dwcan15"]
    channel_names = []
    for slot in channel_slots:
        slot_m = re.search(rf'{slot}\s*\{{[^}}]*name\s*=\s*"([^"]+)"', block)
        if slot_m:
            channel_names.append(slot_m.group(1))
    return IntrepidDevice(var_name=var_name, name=name, serial_number=serial,
                          channel_names=channel_names)


def _parse_bench(block: str) -> Optional[Bench]:
    benchname = _first(r'benchname\s*=\s*"([^"]+)"', block)
    hostname = _first(r'hostname\s*=\s*"([^"]+)"', block)
    if not benchname or not hostname:
        return None

    bench = Bench(benchname=benchname, hostname=hostname)

    # find all local variable names and their types
    local_vars = re.findall(
        r'local\s+(\w+)\s*=\s*new\s+teq_devices\.(\w+)', block
    )

    hub_vars, relay_vars, lj_vars, eth_vars, denkovi_vars, intrepid_vars = [], [], [], [], [], []
    for var_name, type_name in local_vars:
        if 'Hub' in type_name:
            hub_vars.append(var_name)
        elif 'DenkoviRelay' in type_name:
            denkovi_vars.append(var_name)
        elif 'Relay' in type_name:
            relay_vars.append(var_name)
        elif 'LabJack' in type_name:
            lj_vars.append(var_name)
        elif 'Ethernet' in type_name:
            eth_vars.append(var_name)
        elif 'Intrepid' in type_name or 'NeoVi' in type_name:
            intrepid_vars.append(var_name)

    for v in hub_vars:
        h = _parse_hub(block, v)
        if h:
            bench.hubs.append(h)

    for v in relay_vars:
        r = _parse_relay(block, v)
        if r:
            bench.relays.append(r)

    for v in lj_vars:
        lj = _parse_labjack(block, v)
        if lj:
            bench.labjacks.append(lj)

    for v in eth_vars:
        e = _parse_eth(block, v)
        if e:
            bench.eth_adapters.append(e)

    for v in denkovi_vars:
        d = _parse_denkovi(block, v)
        if d:
            bench.denkovi_devices.append(d)

    for v in intrepid_vars:
        ip = _parse_intrepid(block, v)
        if ip:
            bench.intrepid_devices.append(ip)

    # test_equipment nodes order (determines hub indices)
    nodes_m = re.search(r'nodes\s*=\s*new\s*\{([^}]+)\}', block, re.DOTALL)
    if nodes_m:
        bench.nodes_order = [n.strip() for n in nodes_m.group(1).split(';') if n.strip()]

    # serial instruments
    for m in re.finditer(
        r'\["([^"]+)"\]\s*=\s*\(benchlib\.HwBenchSerial\)\s*\{\s*identifier\s*=\s*"([^"]+)"',
        block
    ):
        bench.serial_instruments.append(SerialInstrument(name=m.group(1), identifier=m.group(2)))

    return bench


# ---------------------------------------------------------------------------
# Config generator
# ---------------------------------------------------------------------------

def _hub_index_for_var(bench: Bench, var_name: str) -> Optional[int]:
    """Return index of hub within the hubs list (ordered by nodes_order)."""
    hub_vars_in_order = [n for n in bench.nodes_order if any(h.var_name == n for h in bench.hubs)]
    try:
        return hub_vars_in_order.index(var_name)
    except ValueError:
        # fallback: position in hubs list
        for i, h in enumerate(bench.hubs):
            if h.var_name == var_name:
                return i
        return None


def _powered_by(bench: Bench, depends_hub: Optional[str], depends_port: Optional[str]) -> Optional[dict]:
    if not depends_hub or not depends_port:
        return None
    hub_idx = _hub_index_for_var(bench, depends_hub)
    if hub_idx is None:
        return None
    hub = next((h for h in bench.hubs if h.var_name == depends_hub), None)
    if not hub:
        return None
    port_idx = hub.port_idx(depends_port)
    if port_idx is None:
        return None
    return {"hub_index": hub_idx, "port": port_idx}


def _hub_port_labels(hub: Hub) -> list[str]:
    labels = ["Unused"] * 4
    for ch in hub.channels:
        if ch.idx < len(labels):
            if ch.description:
                labels[ch.idx] = ch.description
            elif ch.name.startswith('_'):
                labels[ch.idx] = "Unused"
            else:
                labels[ch.idx] = ch.name
    return labels


def _bench_to_config(bench: Bench) -> dict:
    short_name = bench.benchname.split('-')[0]
    has_relays = bool(bench.relays)

    ssh: dict = {"host": bench.hostname, "user": "dev"}
    if has_relays:
        ssh["sudo_user"] = "bk"

    usb_hubs = []
    for hub in bench.hubs:
        usb_hubs.append({
            "name": hub.name,
            "serial_path": hub.serial_port,
            "ports": _hub_port_labels(hub),
        })

    relay_boards = []
    for relay in bench.relays:
        pb = _powered_by(bench, relay.depends_hub, relay.depends_port)
        entry: dict = {
            "name": relay.name,
            "identifier": relay.identifier,
        }
        if pb:
            entry["powered_by"] = pb
        entry["channels"] = [
            {
                "label": ch.label,
                "inverted": ch.inverted,
                "default": ch.default,
            }
            for ch in relay.channels
        ]
        relay_boards.append(entry)

    serial_devices = []
    for s in bench.serial_instruments:
        serial_devices.append({"name": s.name, "identifier": s.identifier})

    labjack_devices = []
    for lj in bench.labjacks:
        pb = _powered_by(bench, lj.depends_hub, lj.depends_port)
        entry = {"name": lj.name}
        if pb:
            entry["powered_by"] = pb
        labjack_devices.append(entry)

    ethernet_devices = []
    for e in bench.eth_adapters:
        entry: dict = {"name": e.name, "interface": e.interface}
        if e.device_ip:
            entry["device_ip"] = e.device_ip
        ethernet_devices.append(entry)

    denkovi_relays = []
    for d in bench.denkovi_devices:
        entry: dict = {
            "name": d.name,
            "host": d.host,
        }
        if d.port != 80:
            entry["port"] = d.port
        if d.password != "admin":
            entry["password"] = d.password
        entry["channels"] = [{"label": ch["label"]} for ch in d.channels]
        denkovi_relays.append(entry)

    intrepid_devices = []
    for ip in bench.intrepid_devices:
        entry = {
            "name": ip.name,
            "serial_number": ip.serial_number,
        }
        if ip.channel_names:
            entry["channel_names"] = ip.channel_names
        intrepid_devices.append(entry)

    devices: dict = {}
    devices["usb_hubs"] = usb_hubs
    devices["relay_boards"] = relay_boards
    if denkovi_relays:
        devices["denkovi_relays"] = denkovi_relays
    if serial_devices:
        devices["serial_devices"] = serial_devices
    if labjack_devices:
        devices["labjack_devices"] = labjack_devices
    if ethernet_devices:
        devices["ethernet_devices"] = ethernet_devices
    if intrepid_devices:
        devices["intrepid_devices"] = intrepid_devices

    return {
        "name": short_name,
        "hostname": bench.benchname,
        "ip": bench.hostname,
        "ssh": ssh,
        "buildkite": {
            "service": "buildkite-agent-bk.service",
            "agent_name": bench.benchname,
            "queue": "fw_hil",
        },
        "devices": devices,
    }


def _generate_config(benches: list[Bench]) -> dict:
    return {
        "global": {
            "poll_interval_s": 10,
            "server": {"host": "0.0.0.0", "port": 5000},
        },
        "benches": [_bench_to_config(b) for b in benches if b.benchname != "sil"],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync benches_config.yaml from hil_benches.pkl")
    parser.add_argument("pkl", help="Path to hil_benches.pkl")
    parser.add_argument("--out", default="benches_config.yaml", help="Output YAML path")
    args = parser.parse_args()

    text = Path(args.pkl).read_text()
    blocks = _extract_blocks(text)
    if not blocks:
        print("ERROR: no bench blocks found in pkl file", file=sys.stderr)
        sys.exit(1)

    benches = [b for block in blocks if (b := _parse_bench(block)) is not None]
    print(f"Parsed {len(benches)} benches: {[b.benchname for b in benches]}")

    config = _generate_config(benches)

    out_path = Path(args.out)
    with open(out_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
