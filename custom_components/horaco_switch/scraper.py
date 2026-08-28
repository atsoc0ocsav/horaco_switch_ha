"""Direct CGI scraper for HORACO / keepLink / OEM managed switches.

Ported from https://github.com/byte4geek/switch-dashboard (scraper.py)
into async aiohttp for native Home Assistant use — no intermediate service.

This module owns the HTTP conversation only; all HTML parsing lives in
``parser.py`` so it can be unit-tested against captured fixtures.

Auth flow:
  1. ``MD5(username + password)`` → ``POST /login.cgi``, also set as the
     ``admin`` cookie (the firmware accepts the cookie on its own afterwards)
  2. ``GET /info.cgi``            → model, firmware, MAC, uptime (when present)
  3. ``GET /port.cgi``            → admin state and, on some firmware, the
                                    full port status table
  4. ``GET /port.cgi?page=stats`` → TX/RX counters
  5. ``GET /panel.cgi``           → copper/fibre port typing (best effort)
  6. ``POST /reboot.cgi {"cmd":"reboot"}`` → remote reboot
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

import aiohttp

from . import parser
from .const import (
    CGI_INFO,
    CGI_LOGIN,
    CGI_PANEL,
    CGI_PORT_CFG,
    CGI_PORT_STATS,
    CGI_REBOOT,
)
from .models import PortData, SwitchData

# Re-exported for the entity platforms, which import them from here.
__all__ = ["HoracoScraper", "PortData", "SwitchData"]

_LOGGER = logging.getLogger(__name__)

# Small inter-request delay to avoid session thrashing the uIP micro-controller
# (same rationale as the original switch-dashboard scraper.py). The embedded
# HTTP server drops connections outright when requests arrive back-to-back.
_REQUEST_DELAY = 0.4

# The firmware's HTTP server intermittently closes a connection with no reply.
_MAX_ATTEMPTS = 3


class HoracoScraper:
    """Async scraper that speaks directly to the switch CGI interface."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ip: str,
        username: str,
        password: str,
        http_port: int = 80,
    ) -> None:
        self._session = session
        self.ip = ip
        self._username = username
        self._password = password
        self._base_url = (
            f"http://{ip}:{http_port}" if http_port != 80 else f"http://{ip}"
        )
        self._cookies: dict[str, str] = {}
        self._logged_in = False
        # Resolved on the first successful scrape, then reused.
        self._panel_supported = True

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        """POST /login.cgi with MD5(user+pass) — mirrors the original scraper."""
        md5hash = hashlib.md5(
            (self._username + self._password).encode()
        ).hexdigest()

        payload = {
            "username": self._username,
            "password": self._password,
            "Response": md5hash,
            "language": "EN",
        }

        try:
            async with self._session.post(
                f"{self._base_url}{CGI_LOGIN}",
                data=payload,
                headers={"Referer": f"{self._base_url}/login.cgi"},
                cookies={"admin": md5hash},
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                self._cookies["admin"] = md5hash
                self._logged_in = True
                _LOGGER.debug("[%s] Login OK", self.ip)
        except Exception as exc:
            self._logged_in = False
            raise RuntimeError(f"Login failed for {self.ip}: {exc}") from exc

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _fetch(self, path: str, *, retry_login: bool = True) -> str | None:
        """GET a CGI page, retrying the firmware's intermittent empty replies."""
        if not self._logged_in:
            await self._login()

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await asyncio.sleep(_REQUEST_DELAY)
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
                    headers={"Referer": f"{self._base_url}/"},
                    cookies=self._cookies,
                    timeout=aiohttp.ClientTimeout(total=20),
                    allow_redirects=True,
                ) as resp:
                    if resp.status in (401, 403):
                        if not retry_login:
                            return None
                        _LOGGER.debug("[%s] Session expired, re-logging in", self.ip)
                        self._logged_in = False
                        await self._login()
                        return await self._fetch(path, retry_login=False)
                    resp.raise_for_status()
                    return await resp.text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 — retried below
                last_exc = exc
                _LOGGER.debug(
                    "[%s] Fetch %s attempt %d/%d failed: %s",
                    self.ip, path, attempt, _MAX_ATTEMPTS, exc,
                )

        _LOGGER.error("[%s] Fetch %s failed: %s", self.ip, path, last_exc)
        return None

    async def _post(self, path: str, data: dict[str, str]) -> str | None:
        if not self._logged_in:
            await self._login()
        await asyncio.sleep(_REQUEST_DELAY)
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                data=data,
                headers={
                    "Referer": f"{self._base_url}{path}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                cookies=self._cookies,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                return await resp.text(encoding="utf-8", errors="replace")
        except Exception as exc:
            _LOGGER.error("[%s] POST %s failed: %s", self.ip, path, exc)
            return None

    # ------------------------------------------------------------------
    # Main scrape
    # ------------------------------------------------------------------

    async def scrape(self) -> SwitchData:
        """Fetch every telemetry page and assemble a full snapshot."""
        try:
            await self._login()
        except Exception as exc:
            _LOGGER.error("[%s] Login error: %s", self.ip, exc)
            return SwitchData(
                ip=self.ip, model="Unknown", mac="", uptime="",
                firmware="", available=False,
            )

        info_html     = await self._fetch(CGI_INFO)
        port_cfg_html = await self._fetch(CGI_PORT_CFG)
        stats_html    = await self._fetch(CGI_PORT_STATS)

        if info_html is None and port_cfg_html is None:
            _LOGGER.error("[%s] No CGI page could be read", self.ip)
            return SwitchData(
                ip=self.ip, model="Unknown", mac="", uptime="",
                firmware="", available=False,
            )

        info = parser.parse_device_info(info_html or "")
        ports = parser.parse_ports(info_html or "", port_cfg_html or "")

        if not ports:
            _LOGGER.warning(
                "[%s] No ports found in /info.cgi or /port.cgi — please report "
                "this with the model name so the layout can be added",
                self.ip,
            )

        caps = parser.parse_stats(stats_html or "", ports)

        # Front-panel media typing is cosmetic; never let it break a scrape.
        if self._panel_supported and ports:
            panel_html = await self._fetch(CGI_PANEL)
            if panel_html:
                media = parser.parse_port_media(panel_html, len(ports))
                for port in ports:
                    port.media = media.get(port.port, "")
            else:
                self._panel_supported = False

        return SwitchData(
            ip=self.ip,
            model=info.get("model", "HORACO/OEM"),
            mac=info.get("mac", ""),
            uptime=info.get("uptime", ""),
            firmware=info.get("firmware", ""),
            firmware_date=info.get("firmware_date", ""),
            hardware=info.get("hardware", ""),
            netmask=info.get("netmask", ""),
            gateway=info.get("gateway", ""),
            ports=ports,
            available=True,
            has_uptime="uptime" in info,
            has_byte_counters=caps["has_byte_counters"],
            has_error_counters=caps["has_error_counters"],
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def reboot(self) -> bool:
        """POST /reboot.cgi {"cmd":"reboot"} — same endpoint as switch-dashboard."""
        try:
            await self._post(CGI_REBOOT, {"cmd": "reboot"})
            _LOGGER.warning("[%s] Reboot command sent", self.ip)
            self._logged_in = False
            return True
        except Exception as exc:
            _LOGGER.error("[%s] Reboot failed: %s", self.ip, exc)
            return False
