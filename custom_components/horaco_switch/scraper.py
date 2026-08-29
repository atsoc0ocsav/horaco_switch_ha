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
  6. ``GET /fwd.cgi?page=jumboframe`` → configured maximum frame size
  7. ``POST /reboot.cgi {"cmd":"reboot"}`` → remote reboot

Steps 5 and 6 read configuration that rarely changes and are cached, so a
steady-state poll issues three requests rather than five.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import aiohttp

from . import parser
from .rates import RateTracker
from .const import (
    CGI_INFO,
    CGI_JUMBO,
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

# Slow-changing configuration (front-panel layout, jumbo-frame size) is
# re-read every Nth poll instead of every poll. At the default 30 s interval
# that is roughly every five minutes.
_STATIC_REFRESH_EVERY = 10


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
        # Configuration that rarely changes is cached and refreshed
        # periodically rather than fetched on every poll, to keep the number of
        # requests per cycle down on this firmware's fragile HTTP server.
        self._poll_count = 0
        self._media: dict[str, str] = {}
        self._jumbo_frame: int | None = None
        self._jumbo_options: list[int] = []
        self._panel_supported = True
        self._jumbo_supported = True
        # Derives per-port frame rates from consecutive polls.
        self._rates = RateTracker()

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
    # Slow-changing configuration
    # ------------------------------------------------------------------

    async def _refresh_static(self, port_count: int) -> None:
        """Refresh cached configuration that rarely changes.

        Fetched on the first poll and every ``_STATIC_REFRESH_EVERY`` polls
        after that, so a user changing the jumbo-frame size is picked up without
        a restart, while a steady-state poll stays at three requests.
        """
        due = (
            self._poll_count == 1
            or self._poll_count % _STATIC_REFRESH_EVERY == 0
            or (self._panel_supported and not self._media)
            or (self._jumbo_supported and self._jumbo_frame is None)
        )
        if not due:
            return

        if self._panel_supported and port_count:
            panel_html = await self._fetch(CGI_PANEL)
            if panel_html:
                media = parser.parse_port_media(panel_html, port_count)
                if media:
                    self._media = media
            elif self._poll_count > 1:
                # Page exists but is unreadable right now; keep what we have.
                pass
            else:
                self._panel_supported = False

        if self._jumbo_supported:
            jumbo_html = await self._fetch(CGI_JUMBO)
            if jumbo_html:
                size = parser.parse_jumbo_frame(jumbo_html)
                options = parser.parse_jumbo_frame_options(jumbo_html)
                if options:
                    self._jumbo_options = options
                if size is not None:
                    if size != self._jumbo_frame:
                        _LOGGER.debug(
                            "[%s] Jumbo frame size is %d bytes (available: %s)",
                            self.ip, size, options,
                        )
                    self._jumbo_frame = size
            elif self._poll_count <= 1:
                self._jumbo_supported = False

    # ------------------------------------------------------------------
    # Main scrape
    # ------------------------------------------------------------------

    async def scrape(self) -> SwitchData:
        """Fetch every telemetry page and assemble a full snapshot."""
        self._poll_count += 1
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

        # Frame rates need two samples; the first poll leaves them unset.
        self._rates.update(ports, time.monotonic())

        await self._refresh_static(len(ports))
        for port in ports:
            port.media = self._media.get(port.port, "")

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
            jumbo_frame=self._jumbo_frame,
            jumbo_frame_options=list(self._jumbo_options),
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
