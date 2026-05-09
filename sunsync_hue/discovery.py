from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MEETHUE_DISCOVERY_URL = "https://discovery.meethue.com/"
MDNS_SERVICE = "_hue._tcp.local."


@dataclass
class DiscoveredBridge:
    ip: str
    id: str | None = None  # bridge ID if known


def discover() -> list[DiscoveredBridge]:
    """Try mDNS first, then fall back to the meethue cloud discovery endpoint."""
    bridges = _discover_mdns(timeout=4.0)
    if bridges:
        return bridges
    logger.info("No bridge found via mDNS, falling back to discovery.meethue.com")
    return _discover_cloud()


def _discover_mdns(timeout: float = 4.0) -> list[DiscoveredBridge]:
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        logger.warning("zeroconf not installed; skipping mDNS discovery")
        return []

    found: dict[str, DiscoveredBridge] = {}

    class _Listener:
        def add_service(self, zc, type_, name):  # noqa: ANN001
            info = zc.get_service_info(type_, name, timeout=2000)
            if not info:
                return
            for addr_bytes in info.addresses:
                ip = socket.inet_ntoa(addr_bytes)
                bridge_id = None
                if info.properties:
                    bid = info.properties.get(b"bridgeid")
                    if bid:
                        bridge_id = bid.decode("ascii", errors="ignore")
                found[ip] = DiscoveredBridge(ip=ip, id=bridge_id)

        def update_service(self, zc, type_, name):  # noqa: ANN001
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):  # noqa: ANN001
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, MDNS_SERVICE, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()

    return list(found.values())


def _discover_cloud() -> list[DiscoveredBridge]:
    try:
        r = httpx.get(MEETHUE_DISCOVERY_URL, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        return [
            DiscoveredBridge(ip=entry["internalipaddress"], id=entry.get("id"))
            for entry in data
            if "internalipaddress" in entry
        ]
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Cloud discovery failed: %s", e)
        return []


def pair(bridge_ip: str, app_name: str = "sunsync-hue", device_name: str = "pi") -> str:
    """POST to the bridge to mint an app key. Bridge link button must be pressed first.

    Returns the new app key (the `username` field in v1 / `hue-application-key` in v2).
    Raises BridgePairingError on failure.
    """
    url = f"https://{bridge_ip}/api"
    payload = {"devicetype": f"{app_name}#{device_name}", "generateclientkey": True}
    try:
        r = httpx.post(url, json=payload, verify=False, timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise BridgePairingError(f"pairing request failed: {e}") from e

    body = r.json()
    if not isinstance(body, list) or not body:
        raise BridgePairingError(f"unexpected response: {body!r}")

    entry = body[0]
    if "error" in entry:
        err = entry["error"]
        # type 101 = link button not pressed
        raise BridgePairingError(
            err.get("description", "unknown bridge error"),
            error_type=err.get("type"),
        )
    if "success" not in entry or "username" not in entry["success"]:
        raise BridgePairingError(f"missing username in response: {body!r}")

    return entry["success"]["username"]


class BridgePairingError(RuntimeError):
    def __init__(self, msg: str, error_type: int | None = None):
        super().__init__(msg)
        self.error_type = error_type

    @property
    def link_button_not_pressed(self) -> bool:
        return self.error_type == 101
