"""HORACO / OEM Managed Switch — Home Assistant Integration.

Talks directly to the switch CGI interface, no intermediate service needed.
Based on the scraping logic from https://github.com/byte4geek/switch-dashboard
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .options import clamp_scan_interval
from .scraper import HoracoScraper, SwitchData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


def resolve_scan_interval(entry: ConfigEntry) -> int:
    """Polling interval from the entry's options, clamped to the safe range.

    Read at coordinator construction and again on every options update, so the
    value the user picks takes effect without a restart.
    """
    return clamp_scan_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HORACO Switch from a config entry."""
    scraper = HoracoScraper(
        session=async_get_clientsession(hass),
        ip=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        http_port=entry.data.get(CONF_PORT, 80),
    )

    coordinator = HoracoCoordinator(hass, scraper, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Without this, editing the polling interval appears to work but has no
    # effect until Home Assistant restarts.
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply an options change immediately.

    The interval is adjusted in place so the entry is not torn down and every
    entity keeps its state and counter history.
    """
    coordinator: HoracoCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return

    seconds = resolve_scan_interval(entry)
    if coordinator.update_interval == timedelta(seconds=seconds):
        return

    _LOGGER.debug(
        "[%s] Polling interval changed to %s s", coordinator.scraper.ip, seconds
    )
    coordinator.update_interval = timedelta(seconds=seconds)
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return ok


class HoracoCoordinator(DataUpdateCoordinator[SwitchData]):
    """Central coordinator — polls the switch at the configured interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        scraper: HoracoScraper,
        entry: ConfigEntry,
    ) -> None:
        self.scraper = scraper
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{scraper.ip}",
            update_interval=timedelta(seconds=resolve_scan_interval(entry)),
        )

    async def _async_update_data(self) -> SwitchData:
        data = await self.scraper.scrape()
        if not data.available:
            raise UpdateFailed(f"Switch {self.scraper.ip} is unreachable")
        return data
