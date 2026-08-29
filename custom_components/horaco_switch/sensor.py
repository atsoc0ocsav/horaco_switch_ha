"""Sensor platform for HORACO Managed Switch.

Architecture:
  • One "parent" Device  → the physical switch  (model, firmware, uptime, MAC, ports summary)
  • One "child" Device per port  → Port N (link, speed, duplex, TX/RX counters,
                                            flow control)

This way the UI groups everything per port instead of exposing a flat list of
disconnected entities.

Entities are created only for data the switch actually reports. Firmware in
this family differs in what it exposes — the ZX-SWTG124AS has no ``Sys Uptime``
row and no byte counters, only packet counters — so an entity that could never
have a real value is not created at all rather than sitting at "unknown".

Counters are published as ``TOTAL_INCREASING``, which is what lets Home
Assistant cope with the switch clearing its statistics — on a reboot, or via the
Clear button on the switch's own statistics page. HA treats a drop below 90% of
the previous value as a meter reset and starts a new cycle rather than recording
a negative delta, so the long-term sum survives.

That same behaviour is why an unread counter must be published as ``None``
(unknown) and never as 0: a 0 would be indistinguishable from a reset, and the
next good reading would then be added onto the long-term sum a second time.
Non-numeric states are filtered out before HA's reset check, so unknown is safe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HoracoCoordinator
from .const import (
    DOMAIN,
    MANUFACTURER,
    PORT_MEDIA_COPPER,
    PORT_MEDIA_FIBER,
    PORT_STATUS_UP,
)
from .scraper import PortData, SwitchData

_LOGGER = logging.getLogger(__name__)

_MEDIA_LABELS = {
    PORT_MEDIA_COPPER: "Copper (RJ45)",
    PORT_MEDIA_FIBER: "Fibre (SFP)",
}


# ────────────────────────────────────────────────────────────────────────────
# Device helpers
# ────────────────────────────────────────────────────────────────────────────

def switch_device_info(coordinator: HoracoCoordinator) -> DeviceInfo:
    """DeviceInfo for the physical switch (parent device)."""
    d = coordinator.data
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.scraper.ip)},
        name=f"Switch {coordinator.scraper.ip}",
        manufacturer=MANUFACTURER,
        model=d.model if d else "Unknown",
        sw_version=d.firmware if d else None,
        hw_version=(d.hardware or None) if d else None,
        connections={("mac", d.mac)} if d and d.mac else set(),
        configuration_url=f"http://{coordinator.scraper.ip}",
    )


def port_device_info(coordinator: HoracoCoordinator, port_num: str) -> DeviceInfo:
    """DeviceInfo for a single port (child device, via_device → switch)."""
    media = ""
    if coordinator.data:
        port = next(
            (p for p in coordinator.data.ports if p.port == port_num), None
        )
        if port is not None:
            media = port.media

    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.scraper.ip}_port{port_num}")},
        name=f"Port {port_num}",
        manufacturer=MANUFACTURER,
        model=_MEDIA_LABELS.get(media, f"Port {port_num}"),
        via_device=(DOMAIN, coordinator.scraper.ip),
    )


# ────────────────────────────────────────────────────────────────────────────
# Switch-level sensor descriptors
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class SwitchSensorDesc(SensorEntityDescription):
    value_fn: Callable[[SwitchData], Any] | None = None
    # Whether this device exposes the underlying data at all.
    exists_fn: Callable[[SwitchData], bool] = lambda d: True
    attrs_fn: Callable[[SwitchData], dict[str, Any]] | None = None


SWITCH_SENSORS: tuple[SwitchSensorDesc, ...] = (
    SwitchSensorDesc(
        key="uptime",
        name="Uptime",
        icon="mdi:timer-outline",
        value_fn=lambda d: d.uptime or None,
        # ZX-SWTG124AS firmware does not report uptime anywhere.
        exists_fn=lambda d: d.has_uptime,
    ),
    SwitchSensorDesc(
        key="firmware",
        name="Firmware",
        icon="mdi:chip",
        value_fn=lambda d: d.firmware or None,
    ),
    SwitchSensorDesc(
        key="mac_address",
        name="MAC Address",
        icon="mdi:identifier",
        value_fn=lambda d: d.mac or None,
    ),
    SwitchSensorDesc(
        key="ports_up",
        name="Ports Up",
        icon="mdi:ethernet",
        native_unit_of_measurement="ports",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.ports if p.status == PORT_STATUS_UP),
    ),
    SwitchSensorDesc(
        key="ports_total",
        name="Ports Total",
        icon="mdi:ethernet",
        native_unit_of_measurement="ports",
        value_fn=lambda d: len(d.ports),
    ),
    SwitchSensorDesc(
        # Named after the switch's own "Jumbo Frame (Bytes)" field so it is
        # findable by the term the device's web UI uses. The key stays
        # max_frame_size to keep unique_ids stable.
        key="max_frame_size",
        name="Jumbo Frame Size",
        icon="mdi:package-variant-closed",
        native_unit_of_measurement="B",
        # Read from the switch's jumbo-frame page, so it follows whatever size
        # is configured there rather than assuming a default.
        value_fn=lambda d: d.jumbo_frame,
        exists_fn=lambda d: d.jumbo_frame is not None,
        attrs_fn=lambda d: (
            {"available_sizes": d.jumbo_frame_options}
            if d.jumbo_frame_options
            else {}
        ),
    ),
)


# ────────────────────────────────────────────────────────────────────────────
# Per-port sensor descriptors
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class PortSensorDesc(SensorEntityDescription):
    value_fn: Callable[[PortData], Any] | None = None
    exists_fn: Callable[[SwitchData], bool] = lambda d: True


PORT_SENSORS: tuple[PortSensorDesc, ...] = (
    PortSensorDesc(
        key="speed",
        name="Speed",
        icon="mdi:speedometer",
        value_fn=lambda p: p.speed or None,
    ),
    PortSensorDesc(
        key="duplex",
        name="Duplex",
        icon="mdi:transfer",
        value_fn=lambda p: p.duplex or None,
    ),
    PortSensorDesc(
        key="tx_bytes",
        name="TX",
        icon="mdi:upload-network-outline",
        native_unit_of_measurement="B",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.tx_bytes,
        exists_fn=lambda d: d.has_byte_counters,
    ),
    PortSensorDesc(
        key="rx_bytes",
        name="RX",
        icon="mdi:download-network-outline",
        native_unit_of_measurement="B",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.rx_bytes,
        exists_fn=lambda d: d.has_byte_counters,
    ),
    PortSensorDesc(
        key="tx_packets",
        name="TX Packets",
        icon="mdi:arrow-up-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.tx_packets,
    ),
    PortSensorDesc(
        key="rx_packets",
        name="RX Packets",
        icon="mdi:arrow-down-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.rx_packets,
    ),
    # Frame rate, not bit rate. This firmware exposes no byte counters, so a
    # throughput figure in bits per second cannot be derived — only frames per
    # second, which is exact.
    PortSensorDesc(
        key="tx_pps",
        name="TX Rate",
        icon="mdi:upload-network-outline",
        native_unit_of_measurement="packets/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.tx_pps,
    ),
    PortSensorDesc(
        key="rx_pps",
        name="RX Rate",
        icon="mdi:download-network-outline",
        native_unit_of_measurement="packets/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.rx_pps,
    ),
    PortSensorDesc(
        key="tx_errors",
        name="TX Errors",
        icon="mdi:alert-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.tx_errors,
        exists_fn=lambda d: d.has_error_counters,
    ),
    PortSensorDesc(
        key="rx_errors",
        name="RX Errors",
        icon="mdi:alert-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.rx_errors,
        exists_fn=lambda d: d.has_error_counters,
    ),
    PortSensorDesc(
        key="flow_control",
        name="Flow Control",
        icon="mdi:swap-horizontal",
        value_fn=lambda p: p.flow_control or None,
    ),
)


# ────────────────────────────────────────────────────────────────────────────
# Platform setup
# ────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HoracoCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    entities: list[SensorEntity] = []

    for desc in SWITCH_SENSORS:
        if data is None or desc.exists_fn(data):
            entities.append(SwitchLevelSensor(coordinator, desc))

    if data:
        for port in data.ports:
            for desc in PORT_SENSORS:
                if desc.exists_fn(data):
                    entities.append(PortLevelSensor(coordinator, port.port, desc))

        if not data.has_byte_counters:
            _LOGGER.debug(
                "[%s] Firmware reports packet counters only; TX/RX byte "
                "sensors were not created",
                coordinator.scraper.ip,
            )

    async_add_entities(entities)


# ────────────────────────────────────────────────────────────────────────────
# Entity classes
# ────────────────────────────────────────────────────────────────────────────

class SwitchLevelSensor(CoordinatorEntity[HoracoCoordinator], SensorEntity):
    """Sensor attached to the parent switch device."""

    entity_description: SwitchSensorDesc

    def __init__(self, coordinator: HoracoCoordinator, desc: SwitchSensorDesc) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._attr_unique_id = f"{DOMAIN}_{coordinator.scraper.ip}_{desc.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = switch_device_info(coordinator)

    @property
    def native_value(self) -> Any:
        return (
            self.entity_description.value_fn(self.coordinator.data)
            if self.coordinator.data
            else None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is None or not self.coordinator.data:
            return {}
        return attrs_fn(self.coordinator.data)


class PortLevelSensor(CoordinatorEntity[HoracoCoordinator], SensorEntity):
    """Sensor attached to a per-port child device."""

    entity_description: PortSensorDesc

    def __init__(
        self,
        coordinator: HoracoCoordinator,
        port_num: str,
        desc: PortSensorDesc,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._port_num = port_num
        self._attr_unique_id = f"{DOMAIN}_{coordinator.scraper.ip}_port{port_num}_{desc.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = port_device_info(coordinator, port_num)

    def _port(self) -> PortData | None:
        if not self.coordinator.data:
            return None
        return next(
            (p for p in self.coordinator.data.ports if p.port == self._port_num), None
        )

    @property
    def native_value(self) -> Any:
        p = self._port()
        return self.entity_description.value_fn(p) if p else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self._port()
        if not p:
            return {}
        attrs: dict[str, Any] = {
            "status": p.status,
            "link": p.link,
        }
        if p.media:
            attrs["media"] = p.media
        if p.speed_config:
            attrs["speed_config"] = p.speed_config
        return attrs
