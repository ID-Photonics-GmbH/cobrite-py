"""
CoBrite basic usage example.

Demonstrates:
- Connecting and querying the device layout
- Querying tuning limits
- Setting wavelength and power using explicit CSD methods
- Using the active port + property API
- Enabling / disabling a laser port with busy_wait
- Level-1 commands (require password)
"""

import sys

from cobrite import CoBrite

# --- constants -----------------------------------------------------------

ADDRESS = "192.168.1.99"  # hostname or IP of the CoBrite unit
PORT = 2000
TIMEOUT = 20  # seconds; must be longer than laser tuning time

CHASSIS = 1
SLOT = 1
DEVICE = 1

TARGET_WAVELENGTH_NM = 1550.0
TARGET_POWER_DBM = 11.0

LEVEL1_ENABLED = False

# --- connect -------------------------------------------------------------

cb = CoBrite(address=ADDRESS, port=PORT, timeout=TIMEOUT)
cb.open()

print(f"Opened connection to {cb.idn()}")
print(f"Layout:\nraw: {cb.layout()}\nFormatted: \n{cb.format_layout()}")

# --- query limits (explicit CSD style) -----------------------------------

print(
    f"Selected port   : {CHASSIS},{SLOT},{DEVICE}"
)

limits = cb.get_limits(CHASSIS, SLOT, DEVICE)[0][-1]
print(
    f"Frequency range : {limits['freq_min']:.4f} - {limits['freq_max']:.4f} THz"
)
print(
    f"Power range     : {limits['pow_min']:.2f} - {limits['pow_max']:.2f} dBm"
)
print(f"Offset range    : ±{limits['offset_range']:.3f} GHz")

wav_lim = cb.get_wavelength_limits(CHASSIS, SLOT, DEVICE)[0][-1]
print(f"Wavelength range: {wav_lim['min']:.2f} - {wav_lim['max']:.2f} nm")

# --- style 1: explicit CSD methods ---------------------------------------

print("\n--- explicit CSD style ---")

cb.set_wavelength(TARGET_WAVELENGTH_NM, CHASSIS, SLOT, DEVICE)
cb.set_power(TARGET_POWER_DBM, CHASSIS, SLOT, DEVICE)

pwr = cb.get_power(CHASSIS, SLOT, DEVICE)[0][-1]
wav = cb.get_wavelength(CHASSIS, SLOT, DEVICE)[0][-1]
freq = cb.get_frequency(CHASSIS, SLOT, DEVICE)[0][-1]
print(f"Set  : {wav:.3f} nm  |  {freq:.4f} THz  |  {pwr:.2f} dBm")

cb.set_state(True, CHASSIS, SLOT, DEVICE)
cb.busy_wait(CHASSIS, SLOT, DEVICE)

apow = cb.get_actual_power(CHASSIS, SLOT, DEVICE)[0][-1]
print(f"Actual output power: {apow:.2f} dBm")

mon = cb.get_monitor(CHASSIS, SLOT, DEVICE)[0][-1]
print(
    f"Monitor: LD {mon['ld_chip_temp']:.1f}°C  base {mon['base_temp']:.1f}°C"
    f"  LD {mon['ld_current_ma']:.1f} mA  TEC {mon['tec_current_ma']:.1f} mA"
)

cb.set_state(False, CHASSIS, SLOT, DEVICE)

# --- style 2: active port + properties -----------------------------------

print("\n--- active port / property style ---")

cb.set_active_port(CHASSIS, SLOT, DEVICE)

print(f"Limits : {cb.limits}")
print(f"Wavelength limits: {cb.wavelength_limits}")

cb.wavelength = TARGET_WAVELENGTH_NM
cb.power = TARGET_POWER_DBM
cb.offset = 0.0

print(f"Wavelength : {cb.wavelength:.3f} nm")
print(f"Frequency  : {cb.frequency:.4f} THz")
print(f"Power      : {cb.power:.2f} dBm")

cb.state = True
cb.busy_wait(CHASSIS, SLOT, DEVICE)

print(f"Actual power: {cb.actual_power:.2f} dBm")
print(f"Monitor     : {cb.monitor}")
print(f"Laser alarm : {cb.laser_alarm}")

cb.state = False

# --- config shorthand (set multiple params atomically) -------------------

print("\n--- atomic set_config / laser_config property ---")

# explicit CSD
cb.set_config(
    frequency=193.1,
    offset=0.0,
    power=TARGET_POWER_DBM,
    state=False,
    dither=-1,
    chassis=CHASSIS,
    slot=SLOT,
    device=DEVICE,
)
cfg = cb.get_config(CHASSIS, SLOT, DEVICE)[0][-1]
print(f"Config via get_config : {cfg}")

# property
cb.set_active_port(CHASSIS, SLOT, DEVICE)
cb.laser_config = {
    "frequency": 193.1,
    "offset": 0.0,
    "power": TARGET_POWER_DBM,
    "state": False,
    "dither": -1,
}
print(f"Config via property   : {cb.laser_config}")

# --- level-1 commands (require password) ---------------------------------

if not LEVEL1_ENABLED:
    cb.close()
    sys.exit(0)

print("\n--- level-1 commands ---")

# login() is called automatically inside each level-1 method the first time.
# You will be prompted for the password once per session.

tridel_before = cb.get_trigger_delay()
print(f"Trigger delay before : {tridel_before} ms")
cb.set_trigger_delay(10)
print(f"Trigger delay after  : {cb.get_trigger_delay()} ms")
cb.set_trigger_delay(tridel_before)

print(f"Lockout: {cb.get_lockout()}")
cb.set_lockout(True)   # block other sessions from writing
cb.set_lockout(False)  # release

# save and restore DHCP (network changes take effect after reboot)
dhcp_state = cb.query("DHCP?")
print(f"DHCP: {dhcp_state}")

# cb.reset()             # warm restart — drops the connection
# cb.default_settings()  # restore factory laser defaults

# --- disconnect ----------------------------------------------------------

cb.close()  # disables all ports, then closes the connection
