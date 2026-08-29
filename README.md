# HORACO Managed Switch — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/gtrancillo/horaco_switch_ha)](https://github.com/gtrancillo/horaco_switch_ha/releases)
[![Validate](https://github.com/gtrancillo/horaco_switch_ha/actions/workflows/validate.yml/badge.svg)](https://github.com/gtrancillo/horaco_switch_ha/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)](https://www.home-assistant.io/)

Control and monitor your **HORACO**, **keepLink** and compatible OEM managed switches directly from Home Assistant — **no extra app, no Docker, no intermediate service**. The integration talks straight to the switch's built-in HTTP interface, the same CGI endpoints used by [byte4geek/switch-dashboard](https://github.com/byte4geek/switch-dashboard), implemented natively in HA with full async support.

---

## Supported devices

| Model | Ports | SFP+ | Status |
|-------|-------|------|--------|
| HORACO HC-SWTGW218AS | 8 × GbE | 2 × 10G | ✅ Confirmed |
| HORACO HC-SWTGW215AS | 5 × GbE | — | ✅ Confirmed |
| HORACO ZX-SWTG124AS | 4 × 2.5G | 2 × 10G | ✅ Confirmed |
| keepLink KP9000-9XH-X | 8 × GbE | 1 × 10G | ✅ Confirmed |
| OEM Realtek RTL8373-based switches | varies | — | ✅ Likely |

> If your switch has a browser-accessible web UI on port 80 with user/password login, it will very likely work. Open an issue to get it added to the table.

### Firmware differences

Not every switch in this family exposes the same data, so the integration reads
what the device actually reports and creates entities only for that. Nothing is
estimated or padded — a counter the firmware does not expose produces no entity
rather than a plausible-looking number.

| Capability | HC-SWTGW218AS / 215AS | ZX-SWTG124AS |
|------------|-----------------------|--------------|
| Port table location | `/info.cgi` | `/port.cgi` (status table) |
| `Sys Uptime` | ✅ reported | ❌ absent → no Uptime sensor |
| Byte counters | ✅ when the firmware lists them | ❌ packets only → no TX/RX byte sensors |
| Error counters (`TxBadPkt` / `RxBadPkt`) | firmware dependent | ✅ → TX/RX Errors sensors |
| Copper / fibre port typing | firmware dependent | ✅ from `/panel.cgi` |

Layout is detected from the pages themselves, not from the model string, because
the same model ships with differing firmware builds.

---

## Features

- 🔌 **Per-port child devices** — each port is its own HA device grouping link state, speed, duplex, traffic counters and flow control
- 📊 **Traffic counters** — cumulative sensors compatible with HA long-term statistics
- 🧭 **Layout auto-detection** — adapts to the differing CGI page layouts in this firmware family, and only creates entities for data the switch really reports
- 🔄 **Reboot button** — one-tap remote reboot from any HA dashboard or automation
- ⚡ **Direct LAN polling** — fully local, no cloud, no proxy
- 🔧 **Configurable interval** — 10 to 300 seconds (default 30 s)

---

## Installation

### Via HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. URL: `https://github.com/gtrancillo/horaco_switch_ha` · Type: **Integration**
3. Install **HORACO Managed Switch** and restart HA
4. **Settings → Devices & Services → Add Integration → HORACO Managed Switch**

### Manual

1. Download the latest `horaco_switch.zip` from [Releases](https://github.com/gtrancillo/horaco_switch_ha/releases/latest)
2. Unzip and copy the `horaco_switch/` folder into `<config>/custom_components/`
3. Restart HA and add the integration via the UI

---

## Setup

| Field | Default | Notes |
|-------|---------|-------|
| Switch IP Address | — | e.g. `192.168.1.100` |
| HTTP Port | `80` | Change only if you remapped the web UI |
| Username | `admin` | Default HORACO credential |
| Password | `admin` | Default HORACO credential |
| Polling interval | `30` s | How often to poll, 10–300 s. Set it during setup or change it later |
| Assumed average TX/RX frame size | `0` (off) | Options only, set per direction. A byte size enables the estimated-throughput sensors |

The polling interval can be set while adding the switch and changed afterwards
via **Configure** on the integration card. Changes apply immediately — no
restart, and entities keep their state and counter history.

Lower values react faster to a link going down, at the cost of loading the
switch's small web server harder; it drops requests when polled too
aggressively, which is why 10 s is the floor. 30 s is a sensible default.

---

## Entities

### Switch device

| Entity | Type | Description |
|--------|------|-------------|
| Uptime | Sensor | e.g. `3d 14h 22m` — only when the firmware reports `Sys Uptime` |
| Firmware | Sensor | Firmware version string |
| MAC Address | Sensor | Switch hardware MAC |
| Ports Up | Sensor | Count of active ports |
| Ports Total | Sensor | Total physical port count |
| Jumbo Frame Size | Sensor | Configured maximum frame size in bytes, read from the switch's jumbo-frame page. An `available_sizes` attribute lists the sizes the switch offers |
| **Reboot** | **Button** | Sends `POST /reboot.cgi` to the switch |

### Port N device *(one per physical port)*

| Entity | Type | Description |
|--------|------|-------------|
| Link | Binary Sensor | `ON` = up · `OFF` = down/disabled. Carries all port attrs. |
| Speed | Sensor | `100M` · `1000M` · `2.5G` · `10G` · `Disabled` |
| Duplex | Sensor | `Full` or `Half` |
| TX | Sensor | Total bytes transmitted (cumulative) — only when the firmware exposes byte counters |
| RX | Sensor | Total bytes received (cumulative) — only when the firmware exposes byte counters |
| TX Packets | Sensor | Total packets transmitted |
| RX Packets | Sensor | Total packets received |
| TX Rate | Sensor | Frames per second transmitted, derived from consecutive polls |
| RX Rate | Sensor | Frames per second received, derived from consecutive polls |
| TX Throughput (estimated) | Sensor | Estimated bit rate, only when you set an assumed average frame size |
| RX Throughput (estimated) | Sensor | Estimated bit rate, only when you set an assumed average frame size |
| TX Errors | Sensor | Bad packets transmitted — only when the firmware exposes them |
| RX Errors | Sensor | Bad packets received — only when the firmware exposes them |
| Flow Control | Sensor | Negotiated flow control (`On` / `Off` / `Enabled` / `Disabled`) |

The Link binary sensor also carries `media` (`copper` / `fiber`) and
`speed_config` (the configured rate, usually `Auto`) as attributes when the
switch reports them.

### Counter behaviour

The switch clears its port statistics on reboot, and its statistics page has a
**Clear** button that does the same. Counter sensors are `TOTAL_INCREASING`, so
Home Assistant handles this: a drop below 90% of the previous value is treated
as a meter reset and a new cycle is started, rather than being recorded as a
negative delta. Long-term statistics survive a switch reboot.

For that to stay true, a counter that could not be read is published as
`unknown` rather than `0` — a `0` would be indistinguishable from a reset, and
the next successful poll would then add the whole counter onto the long-term sum
a second time. Home Assistant filters non-numeric states out before its reset
check, so `unknown` is safe. A genuine `0` from the switch (a port that has
never passed traffic) is still reported as `0`.

The integration never clears the switch's counters.

### Rates: frames per second, and an optional bit-rate estimate

**TX Rate** and **RX Rate** give the traffic rate per port in frames per second,
derived from the change in the counters between polls. They are exact.

A rate in *bits* per second cannot be measured. This firmware exposes no byte or
octet counters anywhere — not on the statistics page, and not on any other page
(`bw_ctrl` configures rate *limits*, it does not measure). Converting frames to
bits needs an average frame size the switch never reports.

It can still be **estimated**, if you supply that average. Set **Assumed average
TX frame size** and **Assumed average RX frame size** in the integration options
(0, the default, disables each) and the corresponding ports gain
**TX Throughput (estimated)** and **RX Throughput (estimated)**, computed as
`frames/s × (assumed bytes + 20) × 8` — the 20 bytes being preamble, SFD and
interframe gap, so the figure describes what the link carries rather than payload
alone.

**The two directions are set separately because they genuinely differ.** On a
measured 10G uplink from this switch to an upstream MikroTik, one direction
averaged 1372 bytes per frame while the other averaged 239 — 5.7× apart. A single
assumption cannot fit both.

#### Measuring the right value

If the device at the other end of a link reports byte counters, it gives you the
answer directly: **bytes ÷ packets** over the same interval. Cross-checked
against a MikroTik CRS310 on the far end of one link, over a 33-second window:

| Direction | True average | Assumed | Estimate vs truth |
|---|---|---|---|
| into this switch | 854 B | 64 B | −90% |
| into this switch | 854 B | 1518 B | +80% |
| into this switch | 854 B | 9216 B | +981% |
| into this switch | 854 B | **854 B (measured)** | **+2.3%** |

The residual +2.3% is not error: it is the 20 bytes of preamble and interframe
gap this integration counts and a frame-byte counter does not — exactly
`(854+20)/854`. Subtract it if you are comparing the two figures directly.

The same window also confirmed the **frame counters themselves agree**: this
switch and the MikroTik differed by 1.7% on the identical link, so the TX/RX Rate
sensors are sound independently of any assumption. A later re-measurement across
an MTU change agreed to within 0.9%.

#### The right value drifts

Sampling the same link four times gave four different answers, because the
average frame size follows whatever the traffic happens to be:

| Window | Into the switch | Out of the switch |
|---|---|---|
| lifetime counters | 1372 B | 239 B |
| 33 s sample | 854 B | 973 B |
| 400 s sample | 1143 B | 251 B |
| 22 s sample | 1258 B | 627 B |

The outbound direction moved by a factor of four. A fixed assumption is therefore
wrong most of the time, by a factor that changes as the traffic mix changes. Sample
over a period that represents your normal load rather than a quiet minute, expect
the estimate to be indicative rather than accurate, and do not build alerting
thresholds on it. The TX/RX Rate sensors carry no such caveat — they are exact.

Treat the estimates as what they are: named "(estimated)", carrying
`estimated: true`, `assumed_frame_bytes` and `includes_wire_overhead` as
attributes so the assumption travels with the value, and `MEASUREMENT` rather
than cumulative totals so a wrong assumption cannot contaminate long-term
statistics. Pick the assumption from what the port actually carries — near the
MTU for bulk transfer, far below it for chatty or control traffic. If you need
throughput you can trust, read the byte counters from the attached devices.

A rate is reported as `unknown`, never 0, when it cannot be known: on the first
poll after startup, when a statistics read failed, or for the one interval
spanning a counter reset. An idle port reports a real `0`.

---

## Example automations

### Alert when a port goes down

```yaml
alias: "Switch port 3 disconnected"
trigger:
  - platform: state
    entity_id: binary_sensor.port_3_link
    to: "off"
    for: "00:00:30"
action:
  - service: notify.mobile_app
    data:
      title: "⚠️ Network alert"
      message: "Switch port 3 went down"
```

### Weekly maintenance reboot

```yaml
alias: "Switch reboot Sunday 3 AM"
trigger:
  - platform: time
    at: "03:00:00"
condition:
  - condition: time
    weekday: [sun]
action:
  - service: button.press
    target:
      entity_id: button.switch_192_168_1_100_reboot
```

---

## How it works

1. **Auth** — `MD5(username + password)` → `POST /login.cgi`, cookie jar
2. **Poll** (every N seconds):
   - `GET /info.cgi` → model, firmware, MAC, hardware revision, uptime (when present) and, on some firmware, the port link/speed table
   - `GET /port.cgi` → admin state per port; on firmware without a port table in `/info.cgi`, also the full status table (`Config` / `Actual` columns)
   - `GET /port.cgi?page=stats` → packet, error and (where available) byte counters
   - `GET /panel.cgi` → copper / fibre port typing, best effort
   - `GET /fwd.cgi?page=jumboframe` → configured maximum frame size
3. **Reboot** — `POST /reboot.cgi {"cmd":"reboot"}`

The last two read configuration that rarely changes, so they are cached and re-read every tenth poll (about every five minutes at the default interval). A steady-state poll therefore issues three requests, not five, which matters on this firmware's fragile HTTP server. Changing the jumbo-frame size on the switch is still picked up without restarting Home Assistant.

A 0.4 s delay between sequential requests prevents session thrashing on the switch's uIP micro-controller. That server also drops the occasional connection with no reply, so each read is retried up to three times before being treated as a failure.

---

## Development

The HTML parsing lives in `custom_components/horaco_switch/parser.py` as plain
functions, separate from the HTTP layer in `scraper.py`. It is covered by tests
that run against captured CGI pages, so no switch and no Home Assistant install
is needed:

```bash
pip install pytest beautifulsoup4
python3 -m pytest tests/ -v
```

Fixtures live in `tests/fixtures/<model>/`. Each file states its provenance —
whether it is a capture from real hardware (with addresses replaced by
documentation-range values) or reconstructed to a documented column contract.

**Adding a device whose layout differs?** Capture `/info.cgi`, `/port.cgi`,
`/port.cgi?page=stats` and `/panel.cgi`, scrub the MAC and IP addresses, add
them as a fixture, and extend `parse_ports` with the new layout.

---

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for the full workflow.

Short version: fork → branch → PR → CI checks green → merge.

**Found a compatible device?** Open a [New device issue](https://github.com/gtrancillo/horaco_switch_ha/issues/new?template=new_device.yml) and we'll add it to the table.

---

## Protecting the `main` branch (repo setup guide)

After pushing to GitHub, go to **Settings → Branches → Add rule** and configure:

| Setting | Value |
|---------|-------|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ |
| Require approvals | 1 (or 0 for solo projects) |
| Require status checks to pass | ✅ |
| Status checks required | `HACS validation`, `hassfest`, `tests` |
| Do not allow bypassing the above settings | ✅ (optional but recommended) |

This ensures no commit lands on `main` without the CI validations passing.

---

## License

MIT — see [LICENSE](LICENSE)

## Credits

CGI endpoint knowledge and scraping approach from [byte4geek/switch-dashboard](https://github.com/byte4geek/switch-dashboard).
