"""Constants for the HORACO Managed Switch integration."""

DOMAIN = "horaco_switch"

DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Bounds on the polling interval. The lower bound protects the switch's uIP
# HTTP server, which drops requests when polled too aggressively.
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300

CONF_SCAN_INTERVAL = "scan_interval"

# Optional estimated-throughput feature. The switch reports frames only, so a
# bit rate can only be produced by assuming an average frame size. 0 disables
# it, which is the default: no assumption is made unless the user makes one.
# Separate per direction: the two directions of a link routinely carry very
# different frame sizes. Measured on a real uplink, one direction averaged
# 1372 B while the other averaged 239 B — 5.7x apart — so a single assumption
# cannot fit both. The original single key is still honoured as a fallback.
CONF_ASSUMED_FRAME_BYTES = "assumed_frame_bytes"          # legacy, both directions
CONF_ASSUMED_TX_FRAME_BYTES = "assumed_tx_frame_bytes"
CONF_ASSUMED_RX_FRAME_BYTES = "assumed_rx_frame_bytes"
DEFAULT_ASSUMED_FRAME_BYTES = 0
MIN_FRAME_BYTES = 64            # smallest legal Ethernet frame
MAX_FRAME_BYTES = 16383         # largest this firmware offers

# Preamble + start-of-frame delimiter (8 B) and interframe gap (12 B). Counted
# because the estimate describes what the link carries, not just payload.
WIRE_OVERHEAD_BYTES = 20

# CGI endpoints — same as byte4geek/switch-dashboard
CGI_LOGIN      = "/login.cgi"
CGI_INFO       = "/info.cgi"
CGI_PORT_STATS = "/port.cgi?page=stats"
CGI_PORT_CFG   = "/port.cgi"
CGI_PANEL      = "/panel.cgi"
CGI_JUMBO      = "/fwd.cgi?page=jumboframe"
CGI_REBOOT     = "/reboot.cgi"

# Port status values
PORT_STATUS_UP       = "up"
PORT_STATUS_DOWN     = "down"
PORT_STATUS_DISABLED = "disable"

# Port media types (from the front-panel graphic; may be unknown)
PORT_MEDIA_COPPER = "copper"
PORT_MEDIA_FIBER  = "fiber"

MANUFACTURER = "HORACO"
