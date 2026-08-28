"""Pure HTML parsing for the HORACO / OEM managed-switch CGI interface.

Everything here is a plain function over an HTML string, so it can be tested
against captured CGI fixtures without Home Assistant or a live switch.

Two different page layouts exist in this firmware family:

**Layout A — "info table"** (HORACO HC-SWTGW218AS, HC-SWTGW215AS)
    ``/info.cgi`` carries a key/value grid *and* a per-port link table, with
    columns ``Port | Link | Duplex | Speed | Flow Control``.

**Layout B — "port status table"** (HORACO ZX-SWTG124AS, keepLink KP9000)
    ``/info.cgi`` carries the key/value grid *only* — no port table and no
    ``Sys Uptime`` row. Port state lives in the third table of ``/port.cgi``,
    behind a two-row header::

        Port | State | Speed/Duplex        | Flow Control
             |       | Config   | Actual   | Config | Actual

    The ``Actual`` column combines speed and duplex into one token
    (``1000Full``, ``10GFull``) or reads ``Link Down``.

Layout is detected from the pages themselves rather than from the model
string, because the same model ships with differing firmware builds.

Column indices for layout A follow the published device template in
byte4geek/switch-dashboard (``device-templates/HC-SWTGW218AS.yaml``); the
layout B reader is a port of that project's "KeepLink style" fallback.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .const import (
    PORT_STATUS_DISABLED,
    PORT_STATUS_DOWN,
    PORT_STATUS_UP,
)
from .models import PortData

_LOGGER = logging.getLogger(__name__)

# Labels in the /info.cgi key/value grid → SwitchData field names.
_INFO_FIELDS: tuple[tuple[str, str], ...] = (
    ("Sys Uptime", "uptime"),
    ("MAC Address", "mac"),
    ("IP Address", "ip"),
    ("Netmask", "netmask"),
    ("Subnet Mask", "netmask"),
    ("Gateway", "gateway"),
    ("Firmware Version", "firmware"),
    ("Firmware Date", "firmware_date"),
    ("Hardware Version", "hardware"),
    ("Device Model", "model"),
    ("Device Name", "model"),
)

_DISABLED_WORDS = frozenset({"disable", "disabled"})


# ──────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────

def parse_counter(val: str) -> int:
    """Parse hex (``0x…``), split-64 (``hi-lo``) or decimal counter values."""
    val = (val or "").strip()
    if not val:
        return 0
    if val.lower().startswith("0x"):
        try:
            return int(val, 16)
        except ValueError:
            return 0
    parts = val.split("-")
    if len(parts) == 2:
        try:
            return int(parts[0]) * 4_294_967_296 + int(parts[1])
        except ValueError:
            return 0
    try:
        return int(val)
    except ValueError:
        return 0


def format_uptime(raw: str) -> str:
    """``1Day2Hour3Minute4Second`` → ``1d 2h 3m 4s``."""
    m = re.match(
        r"(?:(\d+)Day)?(?:(\d+)Hour)?(?:(\d+)Minute)?(?:(\d+)Second)?", raw or ""
    )
    if m:
        parts = [f"{v}{s}" for v, s in zip(m.groups(), "dhms") if v]
        return " ".join(parts) if parts else raw
    return raw


def split_speed_duplex(token: str) -> tuple[str, str]:
    """Split a combined negotiated speed/duplex token into ``(speed, duplex)``.

    ``1000Full`` → ``("1000M", "Full")`` · ``10GFull`` → ``("10G", "Full")``
    ``2500Full`` → ``("2.5G", "Full")`` · unparseable input is returned as-is
    with an empty duplex, never guessed.
    """
    token = (token or "").strip()
    if not token:
        return "", ""

    m = re.match(r"(\d+)\s*(G|M)?\s*(Full|Half)?$", token, re.IGNORECASE)
    if not m:
        return token, ""

    number, unit, duplex = m.group(1), (m.group(2) or "").upper(), m.group(3) or ""

    if unit == "G":
        speed = f"{number}G"
    else:
        # Bare numbers are megabits. Normalise the multi-gig rates that this
        # firmware reports as 2500/5000 into the units printed on the chassis.
        speed = {"2500": "2.5G", "5000": "5G", "10000": "10G"}.get(
            number, f"{number}M"
        )

    return speed, duplex.capitalize()


def _port_number(text: str) -> str:
    """``"Port 3"`` → ``"3"``; falls back to the raw label."""
    m = re.search(r"(\d+)", text or "")
    return m.group(1) if m else (text or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# /info.cgi — device information
# ──────────────────────────────────────────────────────────────────────────

def parse_device_info(info_html: str) -> dict[str, str]:
    """Parse the ``/info.cgi`` key/value grid.

    Only keys the device actually prints are returned, so callers can tell a
    missing field (this firmware has no ``Sys Uptime``) from an empty one.
    """
    out: dict[str, str] = {}
    if not info_html:
        return out

    tables = BeautifulSoup(info_html, "html.parser").find_all("table")
    if not tables:
        return out

    for row in tables[0].find_all("tr"):
        cells = row.find_all(["td", "th"])
        for i in range(0, len(cells) - 1, 2):
            label = cells[i].get_text(strip=True).rstrip(":")
            value = cells[i + 1].get_text(strip=True)
            if not label:
                continue
            for needle, key in _INFO_FIELDS:
                if needle in label:
                    out[key] = format_uptime(value) if key == "uptime" else value
                    break

    return out


# ──────────────────────────────────────────────────────────────────────────
# Port discovery
# ──────────────────────────────────────────────────────────────────────────

def _find_status_table(port_cfg_html: str):
    """Locate the layout-B status table inside ``/port.cgi``.

    Identified by content rather than position: the config *forms* on the same
    page contain ``<select>`` elements, the status table does not, and only the
    status table carries both ``Config`` and ``Actual`` headers.
    """
    for table in BeautifulSoup(port_cfg_html, "html.parser").find_all("table"):
        if table.find("select"):
            continue
        text = table.get_text()
        if "Config" in text and "Actual" in text and re.search(r"Port\s*1\b", text):
            if len(table.find_all("tr")) >= 3:
                return table
    return None


def _parse_admin_states(port_cfg_html: str) -> dict[str, str]:
    """Layout-A helper: admin enable/disable per port from ``/port.cgi``."""
    states: dict[str, str] = {}
    if not port_cfg_html:
        return states

    soup = BeautifulSoup(port_cfg_html, "html.parser")
    heading = soup.find(
        lambda tag: tag.name in ("h3", "legend") and "Port List" in tag.get_text()
    )
    table = heading.find_next("table") if heading else None

    if table is None:
        for candidate in soup.find_all("table"):
            if candidate.find("select"):
                continue
            headers = [th.get_text(strip=True).lower() for th in candidate.find_all("th")]
            if "port" in headers and "state" in headers:
                table = candidate
                break

    if table is None:
        return states

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True)
            if re.search(r"\d", label):
                states[_port_number(label)] = cells[1].get_text(strip=True).lower()

    return states


def parse_ports_from_status_table(port_cfg_html: str) -> list[PortData]:
    """Layout B — read ports from the ``/port.cgi`` status table.

    Columns: ``Port | State | Config | Actual | Flow Config | Flow Actual``.
    """
    ports: list[PortData] = []
    if not port_cfg_html:
        return ports

    table = _find_status_table(port_cfg_html)
    if table is None:
        return ports

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue
        label = cells[0].get_text(strip=True)
        if not re.match(r"Port\s*\d+", label):
            continue  # skip the two header rows

        port_num = _port_number(label)
        admin = cells[1].get_text(strip=True).lower()
        actual = cells[3].get_text(strip=True)
        # Prefer the negotiated flow-control value, fall back to configured.
        flow = cells[5].get_text(strip=True) if len(cells) > 5 else (
            cells[4].get_text(strip=True) if len(cells) > 4 else ""
        )

        if admin in _DISABLED_WORDS:
            ports.append(
                PortData(
                    port=port_num,
                    status=PORT_STATUS_DISABLED,
                    link="Disabled",
                    speed="Disabled",
                    duplex="",
                    flow_control=flow,
                    speed_config=cells[2].get_text(strip=True),
                )
            )
            continue

        is_down = "down" in actual.lower() or not actual
        if is_down:
            ports.append(
                PortData(
                    port=port_num,
                    status=PORT_STATUS_DOWN,
                    link="Link Down",
                    speed="",
                    duplex="",
                    flow_control=flow,
                    speed_config=cells[2].get_text(strip=True),
                )
            )
            continue

        speed, duplex = split_speed_duplex(actual)
        ports.append(
            PortData(
                port=port_num,
                status=PORT_STATUS_UP,
                link="Link Up",
                speed=speed,
                duplex=duplex,
                flow_control=flow,
                speed_config=cells[2].get_text(strip=True),
            )
        )

    return ports


def parse_ports_from_info_table(info_html: str, port_cfg_html: str) -> list[PortData]:
    """Layout A — read ports from the second table of ``/info.cgi``.

    Columns: ``Port | Link | Duplex | Speed | Flow Control``.
    """
    ports: list[PortData] = []
    if not info_html:
        return ports

    tables = BeautifulSoup(info_html, "html.parser").find_all("table")
    if len(tables) < 2:
        return ports

    admin_states = _parse_admin_states(port_cfg_html)

    for row in tables[1].find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        port_num = _port_number(cells[0].get_text(strip=True))

        if admin_states.get(port_num, "enable") in _DISABLED_WORDS:
            ports.append(
                PortData(
                    port=port_num,
                    status=PORT_STATUS_DISABLED,
                    link="Disabled",
                    speed="Disabled",
                    duplex="",
                    flow_control="",
                )
            )
            continue

        link_txt = cells[1].get_text(strip=True)
        ports.append(
            PortData(
                port=port_num,
                status=PORT_STATUS_UP if "Up" in link_txt else PORT_STATUS_DOWN,
                link=link_txt,
                speed=cells[3].get_text(strip=True),
                duplex=cells[2].get_text(strip=True),
                flow_control=cells[4].get_text(strip=True) if len(cells) > 4 else "",
            )
        )

    return ports


def parse_ports(info_html: str, port_cfg_html: str) -> list[PortData]:
    """Discover ports using whichever layout this firmware presents."""
    ports = parse_ports_from_info_table(info_html, port_cfg_html)
    if ports:
        return ports

    ports = parse_ports_from_status_table(port_cfg_html)
    if ports:
        _LOGGER.debug("Ports resolved from the /port.cgi status table (layout B)")
    return ports


# ──────────────────────────────────────────────────────────────────────────
# /port.cgi?page=stats — counters
# ──────────────────────────────────────────────────────────────────────────

def _resolve_stats_columns(headers: list[str]) -> dict[str, int]:
    """Map counter names to column indices from the stats table header.

    Byte columns are only reported when the header really names them. Devices
    that expose packet counters alone (ZX-SWTG124AS) must not end up with
    invented byte totals.
    """
    found: dict[str, int] = {}
    for idx, header in enumerate(headers):
        is_bytes = any(t in header for t in ("byte", "octet"))
        is_bad = any(t in header for t in ("bad", "err", "drop", "discard"))

        if is_bytes:
            if "tx" in header and "tx_bytes" not in found:
                found["tx_bytes"] = idx
            elif "rx" in header and "rx_bytes" not in found:
                found["rx_bytes"] = idx
            continue

        if not any(t in header for t in ("pkt", "packet")):
            continue

        if is_bad:
            if "tx" in header and "tx_errors" not in found:
                found["tx_errors"] = idx
            elif "rx" in header and "rx_errors" not in found:
                found["rx_errors"] = idx
        else:
            if "tx" in header and "tx_packets" not in found:
                found["tx_packets"] = idx
            elif "rx" in header and "rx_packets" not in found:
                found["rx_packets"] = idx

    return found


def parse_stats(stats_html: str, ports: list[PortData]) -> dict[str, bool]:
    """Fill counters on ``ports`` in place.

    Counters are left at ``None`` for any port this page does not cover, and for
    every port when the page could not be read at all. A failed read must not
    publish 0: to Home Assistant's ``TOTAL_INCREASING`` handling a drop below
    90% of the previous value is a counter reset, so a single dropped request
    would otherwise add the whole counter onto the long-term sum a second time.

    Returns capability flags describing which counter families the device
    actually reported.
    """
    caps = {"has_byte_counters": False, "has_error_counters": False}
    if not stats_html or not ports:
        return caps

    table = BeautifulSoup(stats_html, "html.parser").find("table")
    if table is None:
        return caps

    rows = table.find_all("tr")
    if not rows:
        return caps

    headers = [c.get_text(strip=True).lower() for c in rows[0].find_all(["td", "th"])]
    cols = _resolve_stats_columns(headers)
    if "tx_packets" not in cols and "tx_bytes" not in cols:
        _LOGGER.debug("Stats table header not recognised: %s", headers)
        return caps

    by_port = {p.port: p for p in ports}

    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        port = by_port.get(_port_number(cells[0].get_text(strip=True)))
        if port is None:
            continue
        for field_name, idx in cols.items():
            if idx < len(cells):
                setattr(port, field_name, parse_counter(cells[idx].get_text(strip=True)))

    caps["has_byte_counters"] = "tx_bytes" in cols or "rx_bytes" in cols
    caps["has_error_counters"] = "tx_errors" in cols or "rx_errors" in cols
    return caps


# ──────────────────────────────────────────────────────────────────────────
# /panel.cgi — port media type
# ──────────────────────────────────────────────────────────────────────────

def parse_port_media(panel_html: str, port_count: int) -> dict[str, str]:
    """Best-effort copper/fibre typing from the front-panel graphic.

    The panel draws one image per port in physical order, named ``RJ45_*`` or
    ``Fiber_*``. Only the media type is read: the link state encoded in those
    filenames is inconsistent between the copper and fibre variants, and the
    authoritative link state is already available from ``/port.cgi``.

    Returns ``{}`` unless the image count matches the discovered port count, so
    an unfamiliar panel layout degrades to "unknown" instead of mislabelling.
    """
    if not panel_html or port_count <= 0:
        return {}

    media: list[str] = []
    for img in BeautifulSoup(panel_html, "html.parser").find_all("img"):
        src = (img.get("src") or "").rsplit("/", 1)[-1].lower()
        if src.startswith("rj45"):
            media.append("copper")
        elif src.startswith("fiber") or src.startswith("fibre") or src.startswith("sfp"):
            media.append("fiber")

    if len(media) != port_count:
        _LOGGER.debug(
            "Panel image count (%d) does not match port count (%d); "
            "skipping media detection",
            len(media),
            port_count,
        )
        return {}

    return {str(i): kind for i, kind in enumerate(media, start=1)}


# ──────────────────────────────────────────────────────────────────────────
# /fwd.cgi?page=jumboframe — configured maximum frame size
# ──────────────────────────────────────────────────────────────────────────

def _option_own_text(option) -> str:
    """Text belonging to this ``<option>`` only.

    This firmware never closes its ``<option>`` tags, so the parser nests each
    one inside the previous and ``get_text()`` on an option returns its own
    label concatenated with every option after it — ``"9216"`` comes back as
    ``"921616383"``. Reading only the direct string children avoids that.
    """
    return "".join(c for c in option.contents if isinstance(c, str)).strip()


def parse_jumbo_frame(jumbo_html: str) -> int | None:
    """Read the configured maximum frame size, in bytes.

    The page is a bare ``<select name="jumboframe">`` listing the sizes the
    switch supports, with one option marked ``selected``. There is no separate
    enable checkbox — the selected size *is* the configured maximum, so it is
    read from the device rather than assumed, and it tracks whatever the user
    picks.

    Returns ``None`` if the page is missing or no option is marked selected;
    callers must not substitute a default, because the value bounds frame-size
    dependent calculations.
    """
    if not jumbo_html:
        return None

    soup = BeautifulSoup(jumbo_html, "html.parser")
    select = soup.find("select", {"name": "jumboframe"})
    if select is None:
        return None

    for option in select.find_all("option"):
        if option.has_attr("selected"):
            text = _option_own_text(option)
            m = re.search(r"\d+", text)
            if m:
                return int(m.group(0))
            _LOGGER.debug("Selected jumbo-frame option has no number: %r", text)
            return None

    _LOGGER.debug("No jumbo-frame option marked selected")
    return None


def parse_jumbo_frame_options(jumbo_html: str) -> list[int]:
    """Every frame size this switch offers, for diagnostics."""
    if not jumbo_html:
        return []
    soup = BeautifulSoup(jumbo_html, "html.parser")
    select = soup.find("select", {"name": "jumboframe"})
    if select is None:
        return []
    sizes = []
    for option in select.find_all("option"):
        m = re.search(r"\d+", _option_own_text(option))
        if m:
            sizes.append(int(m.group(0)))
    return sizes
