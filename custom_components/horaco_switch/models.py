"""Data models for the HORACO Managed Switch integration.

Kept free of Home Assistant and aiohttp imports so the parsing layer
(``parser.py``) can be unit-tested against captured CGI fixtures without a
Home Assistant install.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PortData:
    """All data for a single physical switch port."""

    port: str                       # "1", "2", … "9"
    status: str                     # "up" | "down" | "disable"
    link: str                       # "Link Up" | "Link Down" | "Disabled"
    speed: str                      # "100M" | "1000M" | "2.5G" | "10G" | "Disabled" | ""
    duplex: str                     # "Full" | "Half" | ""
    flow_control: str               # "On" | "Off" | "Enabled" | "Disabled" | ""
    speed_config: str = ""          # configured (not negotiated) speed/duplex, e.g. "Auto"
    media: str = ""                 # "copper" | "fiber" | "" (unknown)
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_packets: int = 0
    rx_packets: int = 0
    tx_errors: int = 0
    rx_errors: int = 0


@dataclass
class SwitchData:
    """Full snapshot of one managed switch.

    The ``has_*`` flags describe what this particular firmware actually
    exposes. Not every model reports uptime or byte counters, and entities are
    only created for data the device really returns — never for a placeholder.
    """

    ip: str
    model: str
    mac: str
    uptime: str
    firmware: str
    firmware_date: str = ""
    hardware: str = ""
    netmask: str = ""
    gateway: str = ""
    ports: list[PortData] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    available: bool = True

    # Capability flags — resolved during the first successful scrape.
    has_uptime: bool = False
    has_byte_counters: bool = False
    has_error_counters: bool = False
