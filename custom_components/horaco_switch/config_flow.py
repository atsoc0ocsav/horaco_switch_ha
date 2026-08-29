"""Config flow for HORACO Managed Switch."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_ASSUMED_FRAME_BYTES,
    CONF_ASSUMED_RX_FRAME_BYTES,
    CONF_ASSUMED_TX_FRAME_BYTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_ASSUMED_FRAME_BYTES,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_FRAME_BYTES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .scraper import HoracoScraper, SwitchData

_LOGGER = logging.getLogger(__name__)

# Connection settings live in entry.data; the polling interval lives in
# entry.options so the options flow is the single place that owns it.
CONNECTION_KEYS = (CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)


def _interval_selector() -> NumberSelector:
    """Slider for the polling interval, bounded to what the switch tolerates."""
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=5,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


def _frame_selector() -> NumberSelector:
    """Byte box for an assumed average frame size; 0 disables the estimate."""
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=MAX_FRAME_BYTES,
            step=1,
            unit_of_measurement="B",
            mode=NumberSelectorMode.BOX,
        )
    )


def _legacy_frame_bytes(options) -> int:
    """Honour the pre-split single option as the default for both directions."""
    return int(options.get(CONF_ASSUMED_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES))


STEP_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    vol.Optional(
        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
    ): _interval_selector(),
})


async def _try_connect(hass: HomeAssistant, data: dict[str, Any]) -> SwitchData:
    scraper = HoracoScraper(
        session=async_get_clientsession(hass),
        ip=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        http_port=data.get(CONF_PORT, DEFAULT_PORT),
    )
    result = await scraper.scrape()
    if not result.available:
        raise ConnectionError("cannot_connect")
    return result


class HoracoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                sw = await _try_connect(self.hass, user_input)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                title = f"{sw.model} ({user_input[CONF_HOST]})"
                # Keep credentials in data and the interval in options, so the
                # options flow later edits the same key it was seeded with.
                return self.async_create_entry(
                    title=title,
                    data={k: user_input[k] for k in CONNECTION_KEYS if k in user_input},
                    options={
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        )
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> HoracoOptionsFlow:
        return HoracoOptionsFlow()


class HoracoOptionsFlow(config_entries.OptionsFlow):
    """Edit the polling interval after setup.

    ``config_entry`` is provided by the base class; assigning it here is
    deprecated in current Home Assistant.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                    CONF_ASSUMED_TX_FRAME_BYTES: int(
                        user_input.get(
                            CONF_ASSUMED_TX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                        )
                    ),
                    CONF_ASSUMED_RX_FRAME_BYTES: int(
                        user_input.get(
                            CONF_ASSUMED_RX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                        )
                    ),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _interval_selector(),
                vol.Optional(
                    CONF_ASSUMED_TX_FRAME_BYTES,
                    default=options.get(
                        CONF_ASSUMED_TX_FRAME_BYTES, _legacy_frame_bytes(options)
                    ),
                ): _frame_selector(),
                vol.Optional(
                    CONF_ASSUMED_RX_FRAME_BYTES,
                    default=options.get(
                        CONF_ASSUMED_RX_FRAME_BYTES, _legacy_frame_bytes(options)
                    ),
                ): _frame_selector(),
            }),
        )
