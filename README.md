# CoBrite

Python driver for [ID Photonics](https://id-photonics.com) [CoBrite](https://id-photonics.com/products-solutions/cobrite-tunable-laser/) tunable laser controllers (DX, DX2, MX). Wraps the SCPI-over-TCP interface exposed on port 2000.

## Installation

```bash
pip install cobrite
uv add cobrite
```

## Quick start

```python
from cobrite import CoBrite

cb = CoBrite(address="192.168.1.99", port=2000, timeout=20)
cb.open()

print(cb.idn())
print(cb.format_layout())

cb.set_wavelength(1550.0, chassis=1, slot=1, device=1)
cb.set_power(11.0, chassis=1, slot=1, device=1)
cb.set_state(True, chassis=1, slot=1, device=1)
cb.busy_wait(chassis=1, slot=1, device=1)

print(cb.get_actual_power(1, 1, 1)[0][-1])

cb.close()
```

## Connecting

```python
CoBrite(
    address="cobrite.local",  # hostname or IP
    port=2000,
    timeout=10,               # seconds; must exceed laser tuning time
    max_retries=3,            # parse retries per response
    open=False,               # True to call open() immediately
)
```

`open()` resolves the hostname, opens a PyVISA TCPIP socket, fetches the device layout, and resets the session parameters (`INTI`). `close()` disables all laser ports and disconnects.

## Port addressing (CSD)

Most commands take `chassis`, `slot`, and `device` integers. Passing `0` (the default) expands to all known ports at that level — this is called *CSD interpolation*.

```python
# Target one specific port
cb.set_state(True, chassis=1, slot=1, device=1)

# Enable every port in slot 1 of chassis 1
cb.set_state(True, chassis=1, slot=1, device=0)

# Enable every port on the unit
cb.set_state(True)           # all default to 0
cb.set_state(True, 0, 0, 0) # equivalent

# Query all ports — returns a tuple of (chassis, slot, device, value) tuples
for c, s, d, pwr in cb.get_power():
    print(f"  {c},{s},{d}: {pwr:.2f} dBm")
```

The device layout is discovered automatically via `layout()` during `open()`. Zero is expanded recursively using the cached layout, so address resolution never hits the device at query time.

## API styles

### Explicit CSD style

Every command is a regular method call with positional or keyword CSD arguments. Query methods return `tuple[tuple[int, int, int, T], ...]` — one entry per matched port.

```python
cb.set_wavelength(1550.0, 1, 1, 1)
cb.set_power(11.0, 1, 1, 1)

wav   = cb.get_wavelength(1, 1, 1)[0][-1]   # float, nm
freq  = cb.get_frequency(1, 1, 1)[0][-1]    # float, THz
pwr   = cb.get_power(1, 1, 1)[0][-1]        # float, dBm

limits = cb.get_limits(1, 1, 1)[0][-1]
# {'freq_min': ..., 'freq_max': ..., 'offset_range': ..., 'pow_min': ..., 'pow_max': ...}

mon = cb.get_monitor(1, 1, 1)[0][-1]
# {'ld_chip_temp': ..., 'base_temp': ..., 'ld_current_ma': ..., 'tec_current_ma': ...}
```

### Active port + property style

Select a port once with `set_active_port()`, then use Python properties.

```python
cb.set_active_port(1, 1, 1)

cb.wavelength = 1550.0
cb.power = 11.0
cb.offset = 0.0
cb.state = True
cb.busy_wait(1, 1, 1)

print(cb.wavelength)       # float, nm
print(cb.frequency)        # float, THz
print(cb.actual_power)     # measured output, dBm
print(cb.monitor)          # dict with thermal and current readings
print(cb.laser_alarm)      # int alarm code
```

Read-only properties: `actual_power`, `wavelength_limits`, `frequency_limits`, `power_limits`, `offset_limits`, `limits`, `monitor`, `laser_alarm`.

Read-write properties: `wavelength`, `frequency`, `power`, `offset`, `state`, `dither`, `laser_config`, `trigger_out_active`, `trigger_config`.

### Atomic config

Set all laser parameters in a single SCPI command:

```python
# Explicit CSD
cb.set_config(
    frequency=193.1,
    offset=0.0,
    power=11.0,
    state=False,
    dither=-1,
    chassis=1, slot=1, device=1,
)

# Property (active port must be set first)
cb.laser_config = {
    "frequency": 193.1,
    "offset": 0.0,
    "power": 11.0,
    "state": False,
    "dither": -1,
}
```

`get_config()` / `cb.laser_config` return a dict with keys `frequency`, `offset`, `power`, `state`, `busy`, `dither`.

## Waiting for tuning

```python
# Server-side blocking wait — preferred
cb.busy_wait(1, 1, 1)

# Client-side poll (used internally by set_* methods unless wait=False)
cb.wait(1, 1, 1)
```

Pass `wait=False` to skip the poll and batch commands manually:

```python
cb.set_wavelength(1550.0, 1, 1, 1, wait=False)
cb.set_power(11.0, 1, 1, 1, wait=False)
cb.busy_wait(1, 1, 1)
```

## Level-1 commands

Some commands require a password (user level 1). The library prompts for the password automatically the first time a level-1 method is called in a session, then caches the authentication until `close()` or `init_interface()` is called.

```python
print(cb.get_trigger_delay())  # ms — no auth required
cb.set_trigger_delay(10)       # level 1 — prompts once, caches for session

cb.set_lockout(True)           # block other sessions from writing
cb.set_lockout(False)

cb.default_settings()          # factory laser defaults (not network)
# cb.reset()                   # warm restart — drops the connection
```

### Logging in without a prompt

For automated scripts, store the password in a file (one password per line, only the first non-empty line is read) and call `login_from_file()` before using any level-1 commands:

```python
cb.login_from_file("/run/secrets/cobrite_password")  # level=1 by default
cb.set_trigger_delay(10)   # no prompt
cb.set_lockout(True)
```

The file should contain only the password, with no other content:

```text
s3cr3tpassword
```

`login_from_file()` returns the granted user level (same as `login()`). It sets the same internal cache, so subsequent level-1 calls in the same session will not prompt or re-read the file.

Level-1 system commands: `reset`, `clear_status`, `default_settings`, `default_ip_config`, `set_dhcp`, `set_ip_address`, `set_netmask`, `set_gateway_ip`, `set_dns_ip`, `set_lockout`, `set_start_default`, `set_enable_autostart`, `set_trigger_delay`, `set_trigger_polarity`, `set_password`.

Level-1 port commands: `set_trigger_out_active`, `set_trigger_config`.

## Retry on parse failure

When the device returns a malformed response, the library retries the query up to `max_retries` times (default 3) before raising `RuntimeError`. This covers both type-conversion failures and wrong field counts in multi-value responses.

You can apply the same retry logic to your own methods:

```python
@CoBrite.retry
def my_query(self: CoBrite) -> float:
    ...

# Or with an explicit limit, overriding self.max_retries
@CoBrite.retry(max_retries=5)
def my_query(self: CoBrite) -> float:
    ...
```

## Diagnostics

```python
cb.idn()             # identification string
cb.format_layout()   # human-readable chassis/slot/device tree
cb.full_info()       # layout + current freq / power / state per port

cb.get_alarm()       # system alarm code (int)
cb.get_error()       # last error string
cb.get_interlock()   # False = interlock OK, laser can be enabled
cb.get_temp()        # {'chassis', 'slot', 'device', 'temp'} — hottest laser
cb.get_fan()         # fan level string

cb.manual()          # open the CoBrite manual in the browser
```

## Logging

Uses the standard `logging` module under the `cobrite` logger at `WARNING` level by default. To see raw SCPI traffic:

```python
import logging
logging.getLogger("cobrite").setLevel(logging.DEBUG)
```
