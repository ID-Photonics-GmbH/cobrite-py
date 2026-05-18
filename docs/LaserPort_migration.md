# Migration guide

## Active-port properties → `LaserPort`

The `set_active_port` / property API is deprecated and will be removed in a
future release.  Every use emits a `DeprecationWarning`.  Migrate to
`cb.port(chassis, slot, device)`, which returns a
[`LaserPort`][cobrite.LaserPort] object that exposes the same properties
without global mutable state.

### Side-by-side reference

| Old | New |
|-----|-----|
| `cb.set_active_port(1, 1, 1)` | `port = cb.port(1, 1, 1)` |
| `cb.wavelength` | `port.wavelength` |
| `cb.wavelength = 1550.0` | `port.wavelength = 1550.0` |
| `cb.frequency` | `port.frequency` |
| `cb.power` | `port.power` |
| `cb.actual_power` | `port.actual_power` |
| `cb.offset` | `port.offset` |
| `cb.state` | `port.state` |
| `cb.dither` | `port.dither` |
| `cb.limits` | `port.limits` |
| `cb.wavelength_limits` | `port.wavelength_limits` |
| `cb.frequency_limits` | `port.frequency_limits` |
| `cb.power_limits` | `port.power_limits` |
| `cb.offset_limits` | `port.offset_limits` |
| `cb.laser_alarm` | `port.laser_alarm` |
| `cb.monitor` | `port.monitor` |
| `cb.laser_config` | `port.laser_config` |
| `cb.laser_config = {...}` | `port.laser_config = {...}` |
| `cb.trigger_out_active` | `port.trigger_out_active` |
| `cb.trigger_config` | `port.trigger_config` |
| `cb.trigger_config = {...}` | `port.trigger_config = {...}` |

### Basic read/write

```python
# Before
cb.set_active_port(1, 1, 1)
cb.wavelength = 1550.0
print(cb.wavelength)

# After
port = cb.port(1, 1, 1)
port.wavelength = 1550.0
print(port.wavelength)
```

### Context manager

`LaserPort` supports use as a context manager.  This is the recommended pattern
when a port is only needed inside a block — it makes the scope explicit and
reads like a `with open(...)` file handle:

```python
# Before
cb.set_active_port(1, 1, 1)
cb.wavelength = 1550.0
cb.state = True
cb.busy_wait(1, 1, 1)
print(cb.actual_power)

# After
with cb.port(1, 1, 1) as port:
    port.wavelength = 1550.0
    port.state = True
    cb.busy_wait(1, 1, 1)
    print(port.actual_power)
```

### Multiple simultaneous ports

`LaserPort` objects are independent — you can hold several at once.  The old
API had a single global active port, so accessing two ports required repeated
`set_active_port` calls.

```python
# Before
cb.set_active_port(1, 1, 1)
wl1 = cb.wavelength
cb.set_active_port(1, 1, 2)
wl2 = cb.wavelength

# After
p1 = cb.port(1, 1, 1)
p2 = cb.port(1, 1, 2)
wl1 = p1.wavelength
wl2 = p2.wavelength
```

### Atomic config

`laser_config` and `trigger_config` accept a dict, so the round-trip from
`get_config` works directly:

```python
# Before
cb.set_active_port(1, 1, 1)
cfg = cb.laser_config          # dict
cfg["power"] = 11.0
cb.laser_config = cfg

# After
port = cb.port(1, 1, 1)
cfg = port.laser_config
cfg["power"] = 11.0
port.laser_config = cfg
```

---

## `port=` → `tcp_port=`

The `port` constructor argument was renamed to `tcp_port` to free the name for
the `port()` method.

```python
# Before
cb = CoBrite(address="192.168.1.99", port=2000)

# After
cb = CoBrite(address="192.168.1.99", tcp_port=2000)
```

The default value (2000) is unchanged — code that relied on the default does
not need updating.
