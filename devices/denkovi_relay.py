"""Denkovi smartDEN IP-16R relay health check and control — direct HTTP from laptop."""
from __future__ import annotations

import socket

import requests

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
