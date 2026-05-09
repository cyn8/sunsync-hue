from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


@dataclass
class Room:
    id: str  # room resource id
    name: str
    light_ids: list[str]  # light resource ids
    grouped_light_id: str | None  # for completeness; we apply per-light


@dataclass
class LightState:
    id: str
    name: str
    on: bool
    brightness: float | None  # 0..100 (only meaningful when on)
    color_mode: str  # "xy" | "ct" | "none"
    xy: tuple[float, float] | None
    mirek: int | None
    supports_color: bool
    supports_color_temperature: bool
    supports_dimming: bool


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    """Thin sync client for Hue CLIP v2 over HTTPS."""

    def __init__(self, ip: str, app_key: str, timeout: float = DEFAULT_TIMEOUT):
        self.ip = ip
        self.app_key = app_key
        # Bridge has a self-signed cert keyed to its bridge ID. Pinning would
        # be ideal but verify=False matches what every other Hue client does.
        self._client = httpx.Client(
            base_url=f"https://{ip}",
            headers={"hue-application-key": app_key},
            verify=False,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ---- raw helpers ------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        try:
            r = self._client.get(path)
        except httpx.HTTPError as e:
            raise BridgeError(f"GET {path} failed: {e}") from e
        if r.status_code != 200:
            raise BridgeError(f"GET {path} -> {r.status_code}: {r.text}")
        return r.json()

    def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.put(path, json=payload)
        except httpx.HTTPError as e:
            raise BridgeError(f"PUT {path} failed: {e}") from e
        if r.status_code != 200:
            raise BridgeError(f"PUT {path} -> {r.status_code}: {r.text}")
        return r.json()

    # ---- public API -------------------------------------------------------

    def get_rooms(self) -> list[Room]:
        rooms_data = self._get("/clip/v2/resource/room")["data"]

        # Build map of room.id -> grouped_light service id
        grouped_by_room: dict[str, str] = {}
        for room in rooms_data:
            for svc in room.get("services", []):
                if svc.get("rtype") == "grouped_light":
                    grouped_by_room[room["id"]] = svc["rid"]

        # device.id -> [light.id] (rooms group devices, not lights directly)
        devices_data = self._get("/clip/v2/resource/device")["data"]
        device_to_lights: dict[str, list[str]] = {}
        for device in devices_data:
            light_ids = [
                svc["rid"]
                for svc in device.get("services", [])
                if svc.get("rtype") == "light"
            ]
            if light_ids:
                device_to_lights[device["id"]] = light_ids

        result: list[Room] = []
        for room in rooms_data:
            name = room.get("metadata", {}).get("name", "<unnamed>")
            light_ids: list[str] = []
            for child in room.get("children", []):
                if child.get("rtype") == "device":
                    light_ids.extend(device_to_lights.get(child["rid"], []))
            result.append(
                Room(
                    id=room["id"],
                    name=name,
                    light_ids=light_ids,
                    grouped_light_id=grouped_by_room.get(room["id"]),
                )
            )
        return result

    def get_lights(self) -> dict[str, LightState]:
        """Return all lights keyed by light id."""
        data = self._get("/clip/v2/resource/light")["data"]
        return {ls.id: ls for ls in (self._parse_light(d) for d in data)}

    def get_room_lights(self, room: Room) -> dict[str, LightState]:
        all_lights = self.get_lights()
        return {lid: all_lights[lid] for lid in room.light_ids if lid in all_lights}

    def put_light_state(
        self,
        light_id: str,
        *,
        on: bool | None = None,
        brightness: float | None = None,
        xy: tuple[float, float] | None = None,
        mirek: int | None = None,
        transition_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if on is not None:
            payload["on"] = {"on": on}
        if brightness is not None and on is not False:
            payload["dimming"] = {"brightness": float(brightness)}
        if xy is not None:
            payload["color"] = {"xy": {"x": float(xy[0]), "y": float(xy[1])}}
        if mirek is not None:
            payload["color_temperature"] = {"mirek": int(mirek)}
        if transition_ms is not None:
            payload["dynamics"] = {"duration": int(transition_ms)}
        if not payload:
            return
        self._put(f"/clip/v2/resource/light/{light_id}", payload)

    # ---- parsing ----------------------------------------------------------

    @staticmethod
    def _parse_light(d: dict[str, Any]) -> LightState:
        on = bool(d.get("on", {}).get("on", False))

        dimming = d.get("dimming")
        brightness = float(dimming["brightness"]) if dimming else None
        supports_dimming = dimming is not None

        color = d.get("color")
        ct = d.get("color_temperature")

        xy: tuple[float, float] | None = None
        if color and "xy" in color:
            xy = (float(color["xy"]["x"]), float(color["xy"]["y"]))

        mirek: int | None = None
        if ct and ct.get("mirek") is not None:
            mirek = int(ct["mirek"])

        # color_mode is reported by the bridge in v2 as one of:
        #   "xy"  | "ct"  | nothing (bulb has no colour control)
        # The reliable way to read current mode is to inspect which side has
        # values: if ct.mirek is not None and ct.mirek_valid, it's ct mode;
        # otherwise if xy is present, xy mode. Bulbs without colour at all are
        # "none".
        color_mode = "none"
        ct_valid = ct and ct.get("mirek_valid", False)
        if ct_valid and mirek is not None:
            color_mode = "ct"
        elif xy is not None:
            # If the bulb supports both ct and xy but ct is invalid,
            # it's currently in xy mode.
            color_mode = "xy"

        return LightState(
            id=d["id"],
            name=d.get("metadata", {}).get("name", "<unnamed>"),
            on=on,
            brightness=brightness if on else None,
            color_mode=color_mode,
            xy=xy,
            mirek=mirek,
            supports_color=color is not None,
            supports_color_temperature=ct is not None,
            supports_dimming=supports_dimming,
        )
