"""Parser tests for the HORACO Managed Switch integration.

These run against captured/reconstructed CGI fixtures — no switch and no
Home Assistant install required::

    python3 -m pytest tests/ -v

The ZX-SWTG124AS fixtures are real captures from a HORACO ZX-SWTG124AS
(firmware V1.9, hardware V1.0), with the MAC and IP addresses replaced by
documentation-range values. Fixture provenance is stated in each file.
"""
from __future__ import annotations

import pathlib

import pytest

# conftest.py loads the parsing layer under this stand-in package name so the
# integration's Home-Assistant-dependent __init__ is never executed.
from horaco_switch_parsing import parser  # noqa: E402
from horaco_switch_parsing.models import PortData  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def zx_info() -> str:
    return load("zx_swtg124as", "info.cgi.html")


@pytest.fixture
def zx_port() -> str:
    return load("zx_swtg124as", "port.cgi.html")


@pytest.fixture
def zx_stats() -> str:
    return load("zx_swtg124as", "port_stats.cgi.html")


@pytest.fixture
def zx_panel() -> str:
    return load("zx_swtg124as", "panel.cgi.html")


@pytest.fixture
def hc_info() -> str:
    return load("hc_swtgw218as", "info.cgi.html")


@pytest.fixture
def hc_port() -> str:
    return load("hc_swtgw218as", "port.cgi.html")


# ══════════════════════════════════════════════════════════════════════════
# split_speed_duplex
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1000Full", ("1000M", "Full")),
        ("100Full", ("100M", "Full")),
        ("10Half", ("10M", "Half")),
        ("10GFull", ("10G", "Full")),
        ("2500Full", ("2.5G", "Full")),
        ("5000Full", ("5G", "Full")),
        ("1000full", ("1000M", "Full")),
        ("", ("", "")),
    ],
)
def test_split_speed_duplex(token, expected):
    assert parser.split_speed_duplex(token) == expected


def test_split_speed_duplex_does_not_invent_duplex():
    """An unparseable token is returned verbatim, never guessed as Full."""
    speed, duplex = parser.split_speed_duplex("Link Down")
    assert speed == "Link Down"
    assert duplex == ""


# ══════════════════════════════════════════════════════════════════════════
# parse_counter
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("128996504", 128996504),
        ("0", 0),
        ("0x1E240", 123456),
        ("1-0", 4294967296),
        ("", 0),
        ("n/a", 0),
    ],
)
def test_parse_counter(raw, expected):
    assert parser.parse_counter(raw) == expected


# ══════════════════════════════════════════════════════════════════════════
# format_uptime
# ══════════════════════════════════════════════════════════════════════════

def test_format_uptime():
    assert parser.format_uptime("3Day14Hour22Minute8Second") == "3d 14h 22m 8s"
    assert parser.format_uptime("5Minute") == "5m"


# ══════════════════════════════════════════════════════════════════════════
# Layout B — ZX-SWTG124AS
# ══════════════════════════════════════════════════════════════════════════

def test_zx_device_info(zx_info):
    info = parser.parse_device_info(zx_info)
    assert info["model"] == "ZX-SWTG124AS"
    assert info["mac"] == "AA:BB:CC:00:11:22"
    assert info["firmware"] == "V1.9"
    assert info["firmware_date"] == "Jan 03 2024"
    assert info["hardware"] == "V1.0"
    assert info["netmask"] == "255.255.255.0"


def test_zx_reports_no_uptime(zx_info):
    """This firmware has no Sys Uptime row; the key must be absent, not blank.

    That distinction is what stops an always-unknown Uptime sensor from being
    created for this model.
    """
    assert "uptime" not in parser.parse_device_info(zx_info)


def test_zx_info_cgi_alone_yields_no_ports(zx_info):
    """The regression this change fixes: /info.cgi has no port table here."""
    assert parser.parse_ports_from_info_table(zx_info, "") == []


def test_zx_ports_discovered_from_port_cgi(zx_info, zx_port):
    ports = parser.parse_ports(zx_info, zx_port)
    assert [p.port for p in ports] == ["1", "2", "3", "4", "5", "6"]


def test_zx_port_link_states(zx_info, zx_port):
    """Ports 1 and 6 were up at capture time; the rest were down."""
    ports = {p.port: p for p in parser.parse_ports(zx_info, zx_port)}

    assert ports["1"].status == "up"
    assert ports["1"].speed == "1000M"
    assert ports["1"].duplex == "Full"
    assert ports["1"].link == "Link Up"

    assert ports["6"].status == "up"
    assert ports["6"].speed == "10G"
    assert ports["6"].duplex == "Full"

    for down in ("2", "3", "4", "5"):
        assert ports[down].status == "down"
        assert ports[down].link == "Link Down"
        assert ports[down].speed == ""
        assert ports[down].duplex == ""


def test_zx_config_column_not_mistaken_for_actual(zx_info, zx_port):
    """"Auto" is the configured value and must not leak into speed."""
    ports = parser.parse_ports(zx_info, zx_port)
    assert all(p.speed_config == "Auto" for p in ports)
    assert all(p.speed != "Auto" for p in ports)


def test_zx_config_forms_are_not_parsed_as_ports(zx_port):
    """/port.cgi opens with two <select> config forms; both must be skipped."""
    ports = parser.parse_ports_from_status_table(zx_port)
    assert len(ports) == 6


def test_zx_stats_packet_counters(zx_info, zx_port, zx_stats):
    ports = parser.parse_ports(zx_info, zx_port)
    caps = parser.parse_stats(zx_stats, ports)
    by_port = {p.port: p for p in ports}

    assert by_port["1"].tx_packets == 128996504
    assert by_port["1"].rx_packets == 67761785
    assert by_port["6"].tx_packets == 99895275
    assert by_port["6"].rx_packets == 156041607
    assert caps["has_error_counters"] is True
    assert by_port["1"].tx_errors == 0
    assert by_port["1"].rx_errors == 0


def test_zx_byte_counters_are_not_fabricated(zx_info, zx_port, zx_stats):
    """This device exposes packets only.

    Earlier code estimated bytes as packets * 800. An invented total that is
    wired to a DATA_SIZE sensor and long-term statistics is worse than no
    sensor, so byte counters must stay unreported and unadvertised.
    """
    ports = parser.parse_ports(zx_info, zx_port)
    caps = parser.parse_stats(zx_stats, ports)

    assert caps["has_byte_counters"] is False
    assert all(p.tx_bytes is None and p.rx_bytes is None for p in ports)


def test_unread_counters_stay_none_not_zero(zx_info, zx_port):
    """A failed statistics fetch must not look like a counter reset.

    Counters are TOTAL_INCREASING. Home Assistant treats a drop below 90% of
    the previous value as a meter reset, so publishing 0 for a counter that was
    simply not read would add the whole counter onto the long-term sum again on
    the next good poll. Unread must be None (unknown), which HA filters out.
    """
    ports = parser.parse_ports(zx_info, zx_port)
    assert ports, "ports should still be discovered without the stats page"

    caps = parser.parse_stats("", ports)  # empty == fetch failed

    assert caps == {"has_byte_counters": False, "has_error_counters": False}
    for p in ports:
        assert p.tx_packets is None
        assert p.rx_packets is None
        assert p.tx_errors is None
        assert p.rx_errors is None


def test_real_zero_is_distinguishable_from_unread(zx_info, zx_port, zx_stats):
    """Port 2 genuinely reports 0 packets; that must read as 0, not unknown."""
    ports = parser.parse_ports(zx_info, zx_port)
    parser.parse_stats(zx_stats, ports)
    by_port = {p.port: p for p in ports}

    assert by_port["2"].tx_packets == 0
    assert by_port["2"].rx_packets == 0


def test_port_absent_from_stats_page_keeps_none(zx_info, zx_port):
    """A stats page covering fewer ports must not zero the ones it omits."""
    partial = (
        "<table><tr><th>Port</th><th>TxGoodPkt</th><th>RxGoodPkt</th></tr>"
        "<tr><td>Port 1</td><td>500</td><td>600</td></tr></table>"
    )
    ports = parser.parse_ports(zx_info, zx_port)
    parser.parse_stats(partial, ports)
    by_port = {p.port: p for p in ports}

    assert by_port["1"].tx_packets == 500
    assert by_port["6"].tx_packets is None


def test_zx_panel_media_typing(zx_info, zx_port, zx_panel):
    """Ports 1-4 are RJ45 copper, ports 5-6 are 10G SFP+ fibre."""
    ports = parser.parse_ports(zx_info, zx_port)
    media = parser.parse_port_media(zx_panel, len(ports))

    assert media == {
        "1": "copper", "2": "copper", "3": "copper", "4": "copper",
        "5": "fiber", "6": "fiber",
    }


def test_panel_media_declines_on_count_mismatch(zx_panel):
    """An unfamiliar panel must degrade to unknown, not mislabel ports."""
    assert parser.parse_port_media(zx_panel, 8) == {}
    assert parser.parse_port_media("", 6) == {}


# ══════════════════════════════════════════════════════════════════════════
# Jumbo frame / max frame size
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def zx_jumbo() -> str:
    return load("zx_swtg124as", "jumboframe.cgi.html")


def test_jumbo_frame_reads_selected_size(zx_jumbo):
    """The selected option is the configured maximum — 9216 on this device."""
    assert parser.parse_jumbo_frame(zx_jumbo) == 9216


def test_jumbo_frame_ignores_missing_enable_checkbox(zx_jumbo):
    """There is no enable checkbox on this page.

    Gating on one would report the default 1518 while the switch is actually
    configured for 9216-byte frames.
    """
    assert "enable_jumbo" not in zx_jumbo
    assert parser.parse_jumbo_frame(zx_jumbo) == 9216


def test_jumbo_frame_survives_unclosed_option_tags(zx_jumbo):
    """The firmware never closes <option>, so bs4 nests them.

    get_text() on the selected option yields "921616383"; only its own direct
    text node gives 9216.
    """
    from bs4 import BeautifulSoup

    select = BeautifulSoup(zx_jumbo, "html.parser").find(
        "select", {"name": "jumboframe"}
    )
    selected = next(o for o in select.find_all("option") if o.has_attr("selected"))
    assert selected.get_text(strip=True) == "921616383"  # the trap
    assert parser.parse_jumbo_frame(zx_jumbo) == 9216    # handled


def test_jumbo_frame_options_enumerated(zx_jumbo):
    assert parser.parse_jumbo_frame_options(zx_jumbo) == [
        1522, 1536, 1552, 9216, 16383
    ]


@pytest.mark.parametrize("size", [1522, 1536, 1552, 9216, 16383])
def test_jumbo_frame_tracks_whichever_size_is_selected(size):
    """Any of the switch's sizes must be read back, not just the captured one."""
    options = "".join(
        f'<option value="{i}"{" selected" if s == size else ""}>{s}'
        for i, s in enumerate([1522, 1536, 1552, 9216, 16383])
    )
    html = f'<select name="jumboframe">{options}</select>'
    assert parser.parse_jumbo_frame(html) == size


def test_jumbo_frame_absent_returns_none():
    """No page, no select, or nothing selected must be None, never a default."""
    assert parser.parse_jumbo_frame("") is None
    assert parser.parse_jumbo_frame("<html><body>nope</body></html>") is None
    assert parser.parse_jumbo_frame(
        '<select name="jumboframe"><option value="0">1522</select>'
    ) is None
    assert parser.parse_jumbo_frame_options("") == []


# ══════════════════════════════════════════════════════════════════════════
# Layout A — HC-SWTGW218AS (must not regress)
# ══════════════════════════════════════════════════════════════════════════

def test_hc_device_info(hc_info):
    info = parser.parse_device_info(hc_info)
    assert info["model"] == "HC-SWTGW218AS"
    assert info["firmware"] == "V1.7"
    assert info["uptime"] == "3d 14h 22m 8s"


def test_hc_ports_from_info_table(hc_info, hc_port):
    ports = parser.parse_ports(hc_info, hc_port)
    assert len(ports) == 10

    by_port = {p.port: p for p in ports}
    assert by_port["1"].status == "up"
    assert by_port["1"].speed == "1000M"
    assert by_port["1"].duplex == "Full"
    assert by_port["3"].duplex == "Half"
    assert by_port["3"].speed == "100M"
    assert by_port["9"].speed == "10G"
    assert by_port["2"].status == "down"


def test_hc_admin_disabled_port(hc_info, hc_port):
    """Port 10 is disabled in /port.cgi and must be reported as such."""
    ports = {p.port: p for p in parser.parse_ports(hc_info, hc_port)}
    assert ports["10"].status == "disable"
    assert ports["10"].link == "Disabled"


def test_layout_a_takes_precedence(hc_info, hc_port):
    """When /info.cgi carries a port table it wins over the /port.cgi table."""
    ports = parser.parse_ports(hc_info, hc_port)
    assert len(ports) == 10
    assert any(p.duplex == "Half" for p in ports)


# ══════════════════════════════════════════════════════════════════════════
# Counter capability detection
# ══════════════════════════════════════════════════════════════════════════

def test_byte_columns_are_read_when_present():
    stats = load("synthetic", "port_stats_with_bytes.cgi.html")
    ports = [
        PortData(port="1", status="up", link="Link Up", speed="1000M",
                 duplex="Full", flow_control=""),
        PortData(port="2", status="up", link="Link Up", speed="1000M",
                 duplex="Full", flow_control=""),
    ]
    caps = parser.parse_stats(stats, ports)

    assert caps["has_byte_counters"] is True
    assert caps["has_error_counters"] is True

    by_port = {p.port: p for p in ports}
    assert by_port["1"].tx_bytes == 1500000
    assert by_port["1"].rx_bytes == 2500000
    assert by_port["1"].tx_errors == 2
    assert by_port["1"].rx_errors == 3
    # hex and split-64 encodings
    assert by_port["2"].tx_bytes == 123456
    assert by_port["2"].rx_bytes == 4294967296


def test_bad_packet_columns_not_counted_as_good():
    stats = load("synthetic", "port_stats_with_bytes.cgi.html")
    ports = [PortData(port="1", status="up", link="Link Up", speed="1000M",
                      duplex="Full", flow_control="")]
    parser.parse_stats(stats, ports)
    assert ports[0].tx_packets == 1000
    assert ports[0].rx_packets == 2000


# ══════════════════════════════════════════════════════════════════════════
# Robustness
# ══════════════════════════════════════════════════════════════════════════

def test_empty_and_garbage_input_is_survivable():
    assert parser.parse_device_info("") == {}
    assert parser.parse_device_info("<html><body>nope</body></html>") == {}
    assert parser.parse_ports("", "") == []
    assert parser.parse_ports("<html>", "<html>") == []
    assert parser.parse_stats("", []) == {
        "has_byte_counters": False,
        "has_error_counters": False,
    }


def test_stats_with_unrecognised_header_is_ignored():
    html = "<table><tr><th>Port</th><th>Wibble</th></tr>" \
           "<tr><td>Port 1</td><td>7</td></tr></table>"
    ports = [PortData(port="1", status="up", link="Link Up", speed="",
                      duplex="", flow_control="")]
    caps = parser.parse_stats(html, ports)
    assert caps == {"has_byte_counters": False, "has_error_counters": False}
    assert ports[0].tx_packets is None
