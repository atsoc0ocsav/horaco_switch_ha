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
