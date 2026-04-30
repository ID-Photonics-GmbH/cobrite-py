"""Integration tests — require a real CoBrite device.

Run with:  pytest --cobrite-address 192.168.1.99
Or set:    COBRITE_ADDRESS=192.168.1.99 pytest

All tests are read-only and do not modify laser state.
"""

import pytest

from cobrite import CoBrite


@pytest.fixture
def cb(cobrite_address: str | None) -> CoBrite:  # type: ignore[misc]
    if not cobrite_address:
        pytest.skip("No --cobrite-address / COBRITE_ADDRESS set")
    device = CoBrite(address=cobrite_address)
    device.open()
    yield device  # type: ignore[misc]
    device.close(disable=False)


def test_idn(cb: CoBrite) -> None:
    idn = cb.idn()
    assert len(idn) > 0


def test_layout_non_empty(cb: CoBrite) -> None:
    layout = cb.layout()
    assert len(layout) >= 1
    for chassis, slots in layout.items():
        assert isinstance(chassis, int)
        for slot, devices in slots.items():
            assert isinstance(slot, int)
            for device, typ in devices.items():
                assert isinstance(device, int)
                assert isinstance(typ, str)


def test_get_wavelength_returns_floats(cb: CoBrite) -> None:
    results = cb.get_wavelength()
    assert len(results) >= 1
    for c, s, d, wl in results:
        assert isinstance(wl, float)
        assert 1400.0 < wl < 1700.0


def test_get_frequency_returns_floats(cb: CoBrite) -> None:
    results = cb.get_frequency()
    assert len(results) >= 1
    for c, s, d, freq in results:
        assert isinstance(freq, float)
        assert 175.0 < freq < 215.0


def test_get_power_returns_floats(cb: CoBrite) -> None:
    results = cb.get_power()
    assert len(results) >= 1
    for c, s, d, pwr in results:
        assert isinstance(pwr, float)


def test_get_state_returns_bools(cb: CoBrite) -> None:
    results = cb.get_state()
    assert len(results) >= 1
    for c, s, d, state in results:
        assert isinstance(state, bool)


def test_get_limits_keys(cb: CoBrite) -> None:
    results = cb.get_limits()
    assert len(results) >= 1
    lim = results[0][-1]
    assert {"freq_min", "freq_max", "offset_range", "pow_min", "pow_max"} <= lim.keys()


def test_get_config_keys(cb: CoBrite) -> None:
    results = cb.get_config()
    assert len(results) >= 1
    cfg = results[0][-1]
    assert {"frequency", "offset", "power", "state", "busy", "dither"} <= cfg.keys()


def test_get_monitor_keys(cb: CoBrite) -> None:
    results = cb.get_monitor()
    assert len(results) >= 1
    mon = results[0][-1]
    assert {"ld_chip_temp", "base_temp", "ld_current_ma", "tec_current_ma"} <= mon.keys()


def test_get_alarm_is_int(cb: CoBrite) -> None:
    assert isinstance(cb.get_alarm(), int)


def test_get_interlock_is_bool(cb: CoBrite) -> None:
    assert isinstance(cb.get_interlock(), bool)


def test_property_api_reads(cb: CoBrite) -> None:
    layout = cb.layout()
    c = next(iter(layout))
    s = next(iter(layout[c]))
    d = next(iter(layout[c][s]))
    cb.set_active_port(c, s, d)
    assert isinstance(cb.wavelength, float)
    assert isinstance(cb.frequency, float)
    assert isinstance(cb.power, float)
    assert isinstance(cb.state, bool)
