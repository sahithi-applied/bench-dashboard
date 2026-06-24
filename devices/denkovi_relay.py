"""Denkovi smartDEN IP-16R relay health check and control.

DenkoviRelay       — direct HTTP (used by hardware tester when esi-dashboard
                     has direct network access to the device)
DenkoviRelaySSH    — HTTP proxied via SSH into the bench machine (used by
                     bench-dashboard poll loop, because the Denkovi lives on
                     192.168.1.x which is only reachable from the bench machine,
                     not from esi-dashboard directly)
"""
from __future__ import annotations

import socket
import textwrap

import requests

from devices.ssh_runner import ssh_exec

_SSH_SCRIPT = textwrap.dedent("""\
    import json, socket, urllib.request, urllib.parse

    base = dict(name=device_name, host=device_host, layer=0,
                status="unknown", error=None, diagnosis=None,
                channel_states=[], channel_labels=channel_labels)

    url = f"http://{device_host}:{device_port}/current_state.json"

    try:
        with socket.create_connection((device_host, device_port), timeout=5.0):
            pass
    except OSError as exc:
        base.update(layer=1, status="missing", error=str(exc),
                    diagnosis=f"Network: cannot reach {device_host}:{device_port}")
        print(json.dumps(base)); import sys; sys.exit(0)
    base["layer"] = 1

    try:
        params = urllib.parse.urlencode({"pw": device_password, **extra_params})
        with urllib.request.urlopen(f"{url}?{params}", timeout=5.0) as r:
            data = json.loads(r.read())
        if "LoginKey" in data.get("CurrentState", {}):
            base.update(layer=2, status="driver_error",
                        diagnosis="Authentication failed — check password in bench config")
            print(json.dumps(base)); import sys; sys.exit(0)
        outputs = data["CurrentState"]["Output"]
        states = [False] * 16
        for entry in outputs:
            idx = int(entry["Name"].replace("RELAY", "")) - 1
            states[idx] = entry["Value"] == "1"
        base.update(layer=3, status="ok", channel_states=states)
    except Exception as exc:
        base.update(layer=2, status="comm_error", error=str(exc),
                    diagnosis="HTTP request failed — device unreachable or bad response")

    print(json.dumps(base))
""")

_NUM_CHANNELS = 16
_REQUEST_TIMEOUT_S = 5.0


class DenkoviRelay:
    def __init__(self, host: str, port: int = 80, password: str = "admin") -> None:
        self._host = host
        self._port = port
        self._password = password

    @property
    def _url(self) -> str:
        return f"http://{self._host}:{self._port}/current_state.json"

    def _get(self, extra_params: dict[str, str] | None = None) -> dict:
        params: dict[str, str] = {"pw": self._password}
        if extra_params:
            params.update(extra_params)
        resp = requests.get(self._url, params=params, timeout=_REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        data: dict = resp.json()
        if "LoginKey" in data.get("CurrentState", {}):
            raise ConnectionError(f"Authentication failed for Denkovi relay at {self._host}")
        return data

    def _parse_states(self, data: dict) -> list[bool]:
        outputs = data["CurrentState"]["Output"]
        states = [False] * _NUM_CHANNELS
        for entry in outputs:
            idx = int(entry["Name"].replace("RELAY", "")) - 1
            states[idx] = entry["Value"] == "1"
        return states

    def check(self, cfg: dict) -> dict:
        channel_labels = [ch["label"] for ch in cfg.get("channels", [])]
        base = dict(name=cfg["name"], host=self._host,
                    layer=0, status="unknown", error=None, diagnosis=None,
                    channel_states=[], channel_labels=channel_labels)

        try:
            with socket.create_connection((self._host, self._port), timeout=_REQUEST_TIMEOUT_S):
                pass
        except OSError as exc:
            return dict(base, layer=1, status="missing", error=str(exc),
                        diagnosis=f"Network: cannot reach {self._host}:{self._port} — check Ethernet and device power")
        base["layer"] = 1

        try:
            data = self._get()
        except ConnectionError as exc:
            return dict(base, layer=2, status="driver_error", error=str(exc),
                        diagnosis="Software: authentication failed — check password in bench config")
        except Exception as exc:
            return dict(base, layer=2, status="comm_error", error=str(exc),
                        diagnosis="Software: HTTP request failed — device unreachable or bad response")

        return dict(base, layer=3, status="ok", channel_states=self._parse_states(data))

    def toggle(self, ch_idx: int) -> dict:
        if ch_idx < 0 or ch_idx >= _NUM_CHANNELS:
            raise ValueError(f"Channel {ch_idx} out of range (0-{_NUM_CHANNELS - 1})")
        data = self._get()
        new_state = not self._parse_states(data)[ch_idx]
        relay_num = ch_idx + 1
        self._get(extra_params={f"Relay{relay_num}": "1" if new_state else "0"})
        return {"channel": ch_idx, "new_state": new_state}

    def set_channel(self, ch_idx: int, state: bool) -> dict:
        if ch_idx < 0 or ch_idx >= _NUM_CHANNELS:
            raise ValueError(f"Channel {ch_idx} out of range (0-{_NUM_CHANNELS - 1})")
        relay_num = ch_idx + 1
        self._get(extra_params={f"Relay{relay_num}": "1" if state else "0"})
        return {"channel": ch_idx, "new_state": state}


class DenkoviRelaySSH:
    """Denkovi relay accessed via SSH proxy through the bench machine.

    Used by bench-dashboard because the Denkovi is on 192.168.1.x which is
    only reachable from the bench machine, not from esi-dashboard directly.
    """

    def __init__(self, denkovi_host: str, ssh_host: str, ssh_user: str,
                 port: int = 80, password: str = "admin") -> None:
        self._denkovi_host = denkovi_host
        self._port = port
        self._password = password
        self._ssh_host = ssh_host
        self._ssh_user = ssh_user

    def _run(self, extra_params: dict | None = None, cfg: dict | None = None) -> dict:
        script = (
            f"device_host = {self._denkovi_host!r}\n"
            f"device_port = {self._port}\n"
            f"device_password = {self._password!r}\n"
            f"device_name = {(cfg or {}).get('name', self._denkovi_host)!r}\n"
            f"channel_labels = {[ch['label'] for ch in (cfg or {}).get('channels', [])]!r}\n"
            f"extra_params = {extra_params or {}!r}\n"
            + _SSH_SCRIPT
        )
        return ssh_exec(self._ssh_host, self._ssh_user, script, timeout=10)

    def check(self, cfg: dict) -> dict:
        try:
            return self._run(cfg=cfg)
        except Exception as exc:
            labels = [ch["label"] for ch in cfg.get("channels", [])]
            return dict(name=cfg["name"], host=self._denkovi_host,
                        layer=0, status="unknown", error=str(exc),
                        diagnosis="SSH to bench machine failed",
                        channel_states=[], channel_labels=labels)

    def toggle(self, ch_idx: int) -> dict:
        if ch_idx < 0 or ch_idx >= _NUM_CHANNELS:
            raise ValueError(f"Channel {ch_idx} out of range")
        result = self._run()
        states = result.get("channel_states", [False] * _NUM_CHANNELS)
        new_state = not states[ch_idx] if ch_idx < len(states) else True
        relay_num = ch_idx + 1
        self._run(extra_params={f"Relay{relay_num}": "1" if new_state else "0"})
        return {"channel": ch_idx, "new_state": new_state}

    def set_channel(self, ch_idx: int, state: bool) -> dict:
        if ch_idx < 0 or ch_idx >= _NUM_CHANNELS:
            raise ValueError(f"Channel {ch_idx} out of range")
        relay_num = ch_idx + 1
        self._run(extra_params={f"Relay{relay_num}": "1" if state else "0"})
        return {"channel": ch_idx, "new_state": state}
