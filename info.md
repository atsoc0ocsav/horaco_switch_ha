# HORACO Managed Switch

Native Home Assistant integration for HORACO HC-SWTGW218AS, HC-SWTGW215AS,
ZX-SWTG124AS, keepLink KP9000 and compatible OEM managed switches.

**No extra app or service needed** — talks directly to the switch CGI interface.

### What you get

- Per-port child devices with link status, speed, duplex and traffic counters
- Remote reboot button
- Fully local, no cloud

Firmware in this family differs in what it exposes, so the integration detects
the page layout and creates entities only for data the switch actually reports —
never an estimated value.

### Setup

Enter your switch IP, port (default 80) and credentials (default: admin / admin).
