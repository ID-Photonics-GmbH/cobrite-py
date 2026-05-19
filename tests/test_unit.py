"""Unit tests — always run via FakeTransport, no hardware required."""

import warnings
from unittest.mock import patch

import pytest
from conftest import AUTH, BUSY_1x1x1, BUSY_1x1x3, OPEN_1x1x1, OPEN_1x1x3, make_cb

from cobrite import CoBrite, CoBriteError
from cobrite._testing import FakeTransport
from cobrite.cobrite import _parse_layout_response, _split_n

# ── connection / connect_guard ───────────────────────────────────────────


def test_connect_guard_raises_before_open() -> None:
    cb = CoBrite()
    with pytest.raises(ConnectionError):
        cb.query("*IDN?")


def test_open_sets_connected() -> None:
    cb = make_cb(OPEN_1x1x1)
    assert cb._connected is True
    cb.close(disable=False)


def test_close_resets_state() -> None:
    cb = make_cb(OPEN_1x1x1)
    cb.close(disable=False)
    assert cb._connected is False
    assert cb._transport is None
    assert cb._user_level == 0


def test_open_true_in_init() -> None:
    cb = CoBrite(_transport=FakeTransport(OPEN_1x1x1), open=True)
    assert cb._connected is True
    cb.close(disable=False)


def test_close_disable_true_disables_ports() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,0": "OK"})
    cb.close(disable=True)
    assert cb._connected is False


def test_close_still_cleans_up_when_set_state_fails() -> None:
    cb = make_cb(OPEN_1x1x1)  # no STAT response — set_state will raise
    cb.close(disable=True)    # must not raise; transport must be cleaned up
    assert cb._connected is False
    assert cb._transport is None


def test_context_manager() -> None:
    responses = {**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,0": "OK"}
    transport = FakeTransport(responses)
    with CoBrite(_transport=transport) as cb:
        assert cb._connected is True
    assert cb._connected is False


def test_query_raises_on_err_response() -> None:
    cb = make_cb({**OPEN_1x1x1, "*IDN?": "ERR 100, unknown command"})
    with pytest.raises(CoBriteError, match="unknown command"):
        cb.query("*IDN?")
    cb.close(disable=False)

def test_error_not_matched() -> None:
    cb = make_cb({**OPEN_1x1x1, "*IDN?": "ERR 95, unknown command"})
    with pytest.raises(CoBriteError, match="unknown command"):
        cb.query("*IDN?")
    cb.close(disable=False)


def test_connect_guard_returns_value_when_exception_false() -> None:
    @CoBrite.connect_guard(exception=False, value="disconnected")
    def probe(self: CoBrite) -> str:
        return "connected"

    cb = CoBrite()
    assert probe(cb) == "disconnected"


def test_split_n_raises_on_wrong_count() -> None:
    with pytest.raises(ValueError, match="Expected 3"):
        _split_n("a,b", ",", 3)


def test_retry_bare_decorator_retries_on_failure() -> None:
    attempts = 0

    class MyDevice(CoBrite):
        @CoBrite.retry
        def flaky(self) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise RuntimeError("transient")
            return "ok"

    cb = MyDevice(_transport=FakeTransport(OPEN_1x1x1))
    cb.open()
    cb.max_retries = 2
    assert cb.flaky() == "ok"
    assert attempts == 2
    cb.close(disable=False)


def test_retry_decorator_factory_retries_on_failure() -> None:
    attempts = 0

    class MyDevice(CoBrite):
        @CoBrite.retry(max_retries=2)
        def flaky(self) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient")
            return "ok"

    cb = MyDevice(_transport=FakeTransport(OPEN_1x1x1))
    cb.open()
    assert cb.flaky() == "ok"
    assert attempts == 3
    cb.close(disable=False)


def test_retry_decorator_factory_raises_after_max() -> None:
    class MyDevice(CoBrite):
        @CoBrite.retry(max_retries=1)
        def always_fails(self) -> str:
            raise RuntimeError("boom")

    cb = MyDevice(_transport=FakeTransport(OPEN_1x1x1))
    cb.open()
    with pytest.raises(RuntimeError, match="boom"):
        cb.always_fails()
    cb.close(disable=False)


def test_manual_opens_browser() -> None:
    with patch("cobrite.cobrite.webbrowser.open") as mock_open:
        CoBrite.manual()
    mock_open.assert_called_once()


# ── FakeTransport ────────────────────────────────────────────────────────


def test_fake_transport_raises_on_unknown_command() -> None:
    cb = make_cb(OPEN_1x1x1)
    with pytest.raises(RuntimeError, match="unexpected command"):
        cb.query("NOEXIST")
    cb.close(disable=False)


# ── layout ───────────────────────────────────────────────────────────────


def test_layout_single_port() -> None:
    cb = make_cb(OPEN_1x1x1)
    assert cb._layout == {1: {1: {1: "GC"}}}
    cb.close(disable=False)


def test_layout_multi_port() -> None:
    cb = make_cb(OPEN_1x1x3)
    assert cb._layout == {1: {1: {1: "GC", 2: "GC", 3: "GC"}}}
    cb.close(disable=False)


def test_format_layout() -> None:
    cb = make_cb(OPEN_1x1x1)
    s = cb.format_layout()
    assert "Chassis 1" in s
    assert "Slot 1" in s
    assert "Device 1: GC" in s
    cb.close(disable=False)

def test_chassis_count() -> None:
    cb = make_cb(OPEN_1x1x1)
    s = cb._chassis_count()
    assert s == 1
    cb.close(disable=False)

def test_slot_count() -> None:
    cb = make_cb(OPEN_1x1x1)
    s = cb._slot_count(1)
    assert s == 1
    cb.close(disable=False)

def test_device_count() -> None:
    cb = make_cb(OPEN_1x1x1)
    s = cb._device_count(1,1)
    assert s == 1
    cb.close(disable=False)

def test_format_layout_reloads_when_layout_none() -> None:
    cb = make_cb(OPEN_1x1x1)
    cb._layout = None  # type: ignore[assignment]
    s = cb.format_layout()
    assert "Chassis 1" in s
    cb.close(disable=False)

def test_full_info_reloads_when_layout_none() -> None:
    responses = {**OPEN_1x1x1, "*IDN?": "CoBrite", "CONF? 1,1,1": "193.4,0.0,3.0,1,0,0"}
    cb = make_cb(responses)
    cb._layout = None  # type: ignore[assignment]
    s = cb.full_info()
    assert "CoBrite" in s
    cb.close(disable=False)

def test_chassis_count_reloads_when_layout_none() -> None:
    cb = make_cb(OPEN_1x1x1)
    cb._layout = None  # type: ignore[assignment]
    assert cb._chassis_count() == 1
    cb.close(disable=False)

def test_slot_count_reloads_when_layout_none() -> None:
    cb = make_cb(OPEN_1x1x1)
    cb._layout = None  # type: ignore[assignment]
    assert cb._slot_count(1) == 1
    cb.close(disable=False)

def test_device_count_reloads_when_layout_none() -> None:
    cb = make_cb(OPEN_1x1x1)
    cb._layout = None  # type: ignore[assignment]
    assert cb._device_count(1, 1) == 1
    cb.close(disable=False)


def test_parse_layout_response_too_few_fields() -> None:
    with pytest.raises(ValueError, match="expected ≥4"):
        _parse_layout_response("only,three,fields")


def test_parse_layout_response_non_integer_chassis() -> None:
    with pytest.raises(ValueError, match="non-integer chassis/slot"):
        _parse_layout_response("X,notint,1,DEV1")


def test_parse_layout_response_bad_device_count() -> None:
    with pytest.raises(ValueError, match="cannot parse device count"):
        _parse_layout_response("X,1,1,DEVbad")


def test_layout_raises_cobrite_error_on_bad_response() -> None:
    cb = make_cb({**OPEN_1x1x1})
    cb._transport._responses["LAY?"] = "malformed"  # type: ignore[union-attr]
    cb._layout = None  # type: ignore[assignment]
    with pytest.raises(CoBriteError):
        cb.layout()
    cb.close(disable=False)


# ── auth / requires_level ────────────────────────────────────────────────


def test_requires_level_raises_when_unauthenticated() -> None:
    cb = make_cb(OPEN_1x1x1)
    with pytest.raises(PermissionError, match="Level 1 required"):
        cb.reset()  # no login called
    cb.close(disable=False)


def test_requires_level_passes_when_authenticated() -> None:
    cb = make_cb({**OPEN_1x1x1, "*RST": "OK"})
    cb._user_level = 1
    cb.reset()
    # reset() sets _connected = False — no close() needed


def test_login_sets_user_level() -> None:
    cb = make_cb({**OPEN_1x1x1, **AUTH})
    level = cb.login(1)
    assert level == 1
    assert cb._user_level == 1
    cb.close(disable=False)


def test_login_with_custom_password() -> None:
    cb = make_cb({**OPEN_1x1x1, "PASS custom": "OK", "PASS?": "1"})
    level = cb.login(1, "custom")
    assert level == 1
    cb.close(disable=False)


def test_login_from_file_sets_user_level(tmp_path: pytest.TempPathFactory) -> None:
    pw = tmp_path / "pw.txt"  # type: ignore[operator]
    pw.write_text("IDP\n")
    cb = make_cb({**OPEN_1x1x1, **AUTH})
    level = cb.login_from_file(pw, level=1)
    assert level == 1
    assert cb._user_level == 1
    cb.close(disable=False)


# ── level-0 system commands ──────────────────────────────────────────────


def test_query_typed_raises_after_exhausting_retries() -> None:
    cb = make_cb({**OPEN_1x1x1, "ALAR?": "not_an_int"})
    cb.max_retries = 0
    with pytest.raises(RuntimeError, match="Failed to parse"):
        cb.get_alarm()
    cb.close(disable=False)


def test_idn() -> None:
    cb = make_cb({**OPEN_1x1x1, "*IDN?": "ID Photonics,CoBrite-DX,SN001,1.0"})
    assert "CoBrite" in cb.idn()
    cb.close(disable=False)


def test_info() -> None:
    cb = make_cb({**OPEN_1x1x1, "INFO?": "ID Photonics,CoBrite-DX,SN001,1.0"})
    assert "CoBrite" in cb.info()
    cb.close(disable=False)


def test_opc_wait() -> None:
    cb = make_cb({**OPEN_1x1x1, "*WAI": "OK"})
    cb.opc_wait()
    cb.close(disable=False)


def test_get_alarm() -> None:
    cb = make_cb({**OPEN_1x1x1, "ALAR?": "0"})
    assert cb.get_alarm() == 0
    cb.close(disable=False)


def test_get_interlock() -> None:
    cb = make_cb({**OPEN_1x1x1, "INTL?": "0"})
    assert cb.get_interlock() is False
    cb.close(disable=False)


# ── level-0 port commands ────────────────────────────────────────────────


def test_get_wavelength() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAV? 1,1,1": "1550.0"})
    result = cb.get_wavelength(1, 1, 1)
    assert result == ((1, 1, 1, 1550.0),)
    cb.close(disable=False)


def test_set_wavelength() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "WAV 1,1,1,1550.0": "OK"})
    cb.set_wavelength(1550.0, 1, 1, 1)
    cb.close(disable=False)


def test_get_frequency() -> None:
    cb = make_cb({**OPEN_1x1x1, "FREQ? 1,1,1": "193.4"})
    result = cb.get_frequency(1, 1, 1)
    assert result == ((1, 1, 1, 193.4),)
    cb.close(disable=False)


def test_set_frequency() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "FREQ 1,1,1,193.4": "OK"})
    cb.set_frequency(193.4, 1, 1, 1)
    cb.close(disable=False)


def test_get_power() -> None:
    cb = make_cb({**OPEN_1x1x1, "POW? 1,1,1": "3.0"})
    result = cb.get_power(1, 1, 1)
    assert result == ((1, 1, 1, 3.0),)
    cb.close(disable=False)


def test_set_power() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "POW 1,1,1,3.0": "OK"})
    cb.set_power(3.0, 1, 1, 1)
    cb.close(disable=False)


def test_get_state_enabled() -> None:
    cb = make_cb({**OPEN_1x1x1, "STAT? 1,1,1": "1"})
    result = cb.get_state(1, 1, 1)
    assert result == ((1, 1, 1, True),)
    cb.close(disable=False)


def test_set_state() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,1": "OK"})
    cb.set_state(True, 1, 1, 1)
    cb.close(disable=False)


def test_set_state_no_arg_warns() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,0": "OK"})
    with pytest.warns(UserWarning, match="explicit state argument"):
        cb.set_state()
    cb.close(disable=False)


def test_close_disable_does_not_warn() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,0": "OK"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cb.close(disable=True)


def test_get_offset() -> None:
    cb = make_cb({**OPEN_1x1x1, "OFF? 1,1,1": "0.5"})
    result = cb.get_offset(1, 1, 1)
    assert result == ((1, 1, 1, 0.5),)
    cb.close(disable=False)


def test_get_config() -> None:
    cb = make_cb({**OPEN_1x1x1, "CONF? 1,1,1": "193.4,0.0,3.0,1,0,0"})
    result = cb.get_config(1, 1, 1)
    cfg = result[0][-1]
    assert cfg["frequency"] == 193.4
    assert cfg["power"] == 3.0
    assert cfg["state"] is True
    assert cfg["busy"] is False
    cb.close(disable=False)


def test_get_monitor() -> None:
    cb = make_cb({**OPEN_1x1x1, "MON? 1,1,1": "25.1,22.5,100.0,50.0"})
    result = cb.get_monitor(1, 1, 1)
    mon = result[0][-1]
    assert mon["ld_chip_temp"] == 25.1
    assert mon["ld_current_ma"] == 100.0
    cb.close(disable=False)


def test_get_limits() -> None:
    cb = make_cb({**OPEN_1x1x1, "LIM? 1,1,1": "191.5,196.25,3.0,0.0,13.0"})
    result = cb.get_limits(1, 1, 1)
    lim = result[0][-1]
    assert lim["freq_min"] == 191.5
    assert lim["freq_max"] == 196.25
    assert lim["pow_max"] == 13.0
    cb.close(disable=False)


def test_get_wavelength_limits() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAV:LIM? 1,1,1": "1528.77,1566.31"})
    result = cb.get_wavelength_limits(1, 1, 1)
    lim = result[0][-1]
    assert lim["min"] == 1528.77
    assert lim["max"] == 1566.31
    cb.close(disable=False)


def test_get_actual_power() -> None:
    cb = make_cb({**OPEN_1x1x1, "APOW? 1,1,1": "2.9"})
    result = cb.get_actual_power(1, 1, 1)
    assert result == ((1, 1, 1, 2.9),)
    cb.close(disable=False)


def test_busy_wait() -> None:
    cb = make_cb({**OPEN_1x1x1, "BWAI 1,1,1": "OK"})
    cb.busy_wait(1, 1, 1)
    cb.close(disable=False)


# ── CSD zero-expansion ───────────────────────────────────────────────────


def test_csd_zero_expands_all_ports() -> None:
    cb = make_cb({**OPEN_1x1x3, "WAV? 1,1,1": "1550.0", "WAV? 1,1,2": "1551.0", "WAV? 1,1,3": "1552.0"})
    result = cb.get_wavelength()
    assert len(result) == 3
    assert result[0] == (1, 1, 1, 1550.0)
    assert result[1] == (1, 1, 2, 1551.0)
    assert result[2] == (1, 1, 3, 1552.0)
    cb.close(disable=False)


def test_csd_specific_port_returns_one() -> None:
    cb = make_cb({**OPEN_1x1x3, "WAV? 1,1,2": "1551.0"})
    result = cb.get_wavelength(1, 1, 2)
    assert len(result) == 1
    assert result[0] == (1, 1, 2, 1551.0)
    cb.close(disable=False)


def test_set_command_zero_expands_all_ports() -> None:
    cb = make_cb({
        **OPEN_1x1x3,
        **BUSY_1x1x3,
        "WAV 1,1,1,1550.0": "OK",
        "WAV 1,1,2,1550.0": "OK",
        "WAV 1,1,3,1550.0": "OK",
    })
    cb.set_wavelength(1550.0)
    cb.close(disable=False)


# ── retry on parse failure ───────────────────────────────────────────────


class _FlakeyTransport(FakeTransport):
    """Returns garbage once for a given command, then the real response."""

    def __init__(self, responses: dict[str, str], fail_once: set[str]) -> None:
        super().__init__(responses)
        self._fail_once: set[str] = set(fail_once)

    def query(self, command: str) -> str:
        if command in self._fail_once:
            self._fail_once.discard(command)
            return "not_a_float"
        return super().query(command)


def test_retry_on_parse_failure() -> None:
    t = _FlakeyTransport(
        {**OPEN_1x1x1, "WAV? 1,1,1": "1550.0"},
        fail_once={"WAV? 1,1,1"},
    )
    cb = CoBrite(_transport=t, max_retries=3)
    cb.open()
    result = cb.get_wavelength(1, 1, 1)
    assert result == ((1, 1, 1, 1550.0),)
    cb.close(disable=False)


def test_retry_exhausted_raises() -> None:
    t = _FlakeyTransport(
        {**OPEN_1x1x1, "WAV? 1,1,1": "1550.0"},
        fail_once={"WAV? 1,1,1"},
    )
    cb = CoBrite(_transport=t, max_retries=0)
    cb.open()
    with pytest.raises(RuntimeError, match="Failed to parse"):
        cb.get_wavelength(1, 1, 1)
    cb.close(disable=False)


# ── property API ─────────────────────────────────────────────────────────


def test_property_requires_active_port() -> None:
    cb = make_cb(OPEN_1x1x1)
    with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError, match="No active port"):
        _ = cb.wavelength
    cb.close(disable=False)


def test_wavelength_property_get() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAV? 1,1,1": "1550.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.wavelength == 1550.0
    cb.close(disable=False)


def test_wavelength_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "WAV 1,1,1,1550.0": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.wavelength = 1550.0
    cb.close(disable=False)


def test_frequency_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "FREQ? 1,1,1": "193.4"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.frequency == 193.4
    cb.close(disable=False)


def test_power_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "POW? 1,1,1": "3.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.power == 3.0
    cb.close(disable=False)


def test_actual_power_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "APOW? 1,1,1": "2.9"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.actual_power == 2.9
    cb.close(disable=False)


def test_state_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "STAT? 1,1,1": "0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.state is False
    cb.close(disable=False)


def test_offset_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "OFF? 1,1,1": "1.5"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.offset == 1.5
    cb.close(disable=False)


def test_dither_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "DIT? 1,1,1": "1"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.dither is True
    cb.close(disable=False)


def test_laser_config_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "CONF? 1,1,1": "193.4,0.0,3.0,1,0,0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cfg = cb.laser_config
    assert cfg["frequency"] == 193.4
    assert cfg["state"] is True
    cb.close(disable=False)


def test_monitor_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "MON? 1,1,1": "25.1,22.5,100.0,50.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        mon = cb.monitor
    assert mon["ld_chip_temp"] == 25.1
    cb.close(disable=False)


def test_get_active_port() -> None:
    cb = make_cb(OPEN_1x1x1)
    with pytest.warns(DeprecationWarning):
        assert cb.get_active_port() is None
        cb.set_active_port(1, 1, 1)
        port = cb.get_active_port()
    assert port is not None
    assert (port.chassis, port.slot, port.device) == (1, 1, 1)
    cb.close(disable=False)


def test_laser_port_context_manager() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAV? 1,1,1": "1550.0"})
    with cb.port(1, 1, 1) as port:
        assert (port.chassis, port.slot, port.device) == (1, 1, 1)
        assert port.wavelength == 1550.0
    cb.close(disable=False)


def test_laser_port_repr() -> None:
    cb = make_cb(OPEN_1x1x1)
    port = cb.port(1, 1, 1)
    assert repr(port) == f"LaserPort({cb!r}, 1, 1, 1)"
    cb.close(disable=False)


def test_execute_set_read_only_raises() -> None:
    cb = make_cb(OPEN_1x1x1)
    with pytest.raises(AttributeError, match="read-only"):
        cb._execute_set("wavelength_limits", 1550.0)
    cb.close(disable=False)


# ── property setters ─────────────────────────────────────────────────────


def test_frequency_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "FREQ 1,1,1,193.4": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.frequency = 193.4
    cb.close(disable=False)


def test_power_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "POW 1,1,1,3.0": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.power = 3.0
    cb.close(disable=False)


def test_state_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "STAT 1,1,1,1": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.state = True
    cb.close(disable=False)


def test_offset_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "OFF 1,1,1,1.0": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.offset = 1.0
    cb.close(disable=False)


def test_dither_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "DIT 1,1,1,1": "OK"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.dither = True
    cb.close(disable=False)


def test_laser_config_property_set() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        **BUSY_1x1x1,
        "CONF 1,1,1,193.4,0.0,3.0,1,0": "OK",
    })
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.laser_config = {"frequency": 193.4, "offset": 0.0, "power": 3.0, "state": True, "dither": 0}
    cb.close(disable=False)


def test_trigger_out_active_property_set() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "TRIOUTACT 1,1,1,1": "OK"})
    cb._user_level = 1
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.trigger_out_active = True
    cb.close(disable=False)


def test_trigger_config_property_set() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        **BUSY_1x1x1,
        "TRICONF 1,1,1,193.4,0.0,3.0,1,0": "OK",
    })
    cb._user_level = 1
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cb.trigger_config = {"frequency": 193.4, "offset": 0.0, "power": 3.0, "state": True, "dither": 0}
    cb.close(disable=False)


# ── level-0 system commands (additional) ─────────────────────────────────


def test_get_trigger_polarity() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIPOL?": "IN,1"})
    result = cb.get_trigger_polarity()
    assert result["direction"] == "IN"
    assert result["polarity"] == 1
    cb.close(disable=False)


def test_get_temp() -> None:
    cb = make_cb({**OPEN_1x1x1, "TEMP?": "1,1,1,45.3"})
    result = cb.get_temp()
    assert result["chassis"] == 1
    assert result["temp"] == 45.3
    cb.close(disable=False)


def test_get_fan() -> None:
    cb = make_cb({**OPEN_1x1x1, "FAN?": "50"})
    assert cb.get_fan() == "50"
    cb.close(disable=False)


def test_get_echo() -> None:
    cb = make_cb({**OPEN_1x1x1, "ECHO?": "0"})
    assert cb.get_echo() is False
    cb.close(disable=False)


def test_set_echo() -> None:
    cb = make_cb({**OPEN_1x1x1, "ECHO 1": "OK"})
    cb.set_echo(True)
    cb.close(disable=False)


def test_get_time() -> None:
    cb = make_cb({**OPEN_1x1x1, "TIME?": "1746000000"})
    assert cb.get_time() == 1746000000
    cb.close(disable=False)


def test_set_time() -> None:
    cb = make_cb({**OPEN_1x1x1, "TIME 1746000000": "OK"})
    cb.set_time(1746000000)
    cb.close(disable=False)


def test_get_error() -> None:
    cb = make_cb({**OPEN_1x1x1, "ERR?": "No error"})
    assert cb.get_error() == "No error"
    cb.close(disable=False)


def test_opc() -> None:
    cb = make_cb({**OPEN_1x1x1, "*OPC?": "1"})
    assert cb.opc() is True
    cb.close(disable=False)


def test_get_lockout() -> None:
    cb = make_cb({**OPEN_1x1x1, "LOCK?": "0"})
    assert cb.get_lockout() is False
    cb.close(disable=False)


def test_get_remote() -> None:
    cb = make_cb({**OPEN_1x1x1, "REMO?": "1"})
    assert cb.get_remote() is True
    cb.close(disable=False)


def test_get_param_refresh() -> None:
    cb = make_cb({**OPEN_1x1x1, "PREF?": "42"})
    assert cb.get_param_refresh() == 42
    cb.close(disable=False)


def test_identify() -> None:
    cb = make_cb({**OPEN_1x1x1, "IDENT 1": "OK"})
    cb.identify(True)
    cb.close(disable=False)


def test_abort() -> None:
    cb = make_cb({**OPEN_1x1x1, "ABOR": "OK"})
    cb.abort()
    cb.close(disable=False)


# ── level-1 system commands ───────────────────────────────────────────────


def test_clear_status() -> None:
    cb = make_cb({**OPEN_1x1x1, "*CLS": "OK"})
    cb._user_level = 1
    cb.clear_status()
    cb.close(disable=False)


def test_set_lockout() -> None:
    cb = make_cb({**OPEN_1x1x1, "LOCK 1": "OK"})
    cb._user_level = 1
    cb.set_lockout(True)
    cb.close(disable=False)


def test_set_trigger_delay() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIDEL 10": "OK"})
    cb._user_level = 1
    cb.set_trigger_delay(10)
    cb.close(disable=False)


def test_set_trigger_polarity() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIPOL IN,1": "OK"})
    cb._user_level = 1
    cb.set_trigger_polarity("IN", 1)
    cb.close(disable=False)


def test_set_dhcp() -> None:
    cb = make_cb({**OPEN_1x1x1, "DHCP on": "OK"})
    cb._user_level = 1
    cb.set_dhcp(True)
    cb.close(disable=False)


def test_set_ip_address() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR 10.0.0.1": "OK"})
    cb._user_level = 1
    cb.set_ip_address("10.0.0.1")
    cb.close(disable=False)


def test_set_ip_address_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR1 10.0.0.1": "OK"})
    cb._user_level = 1
    cb.set_ip_address_1("10.0.0.1")
    cb.close(disable=False)


def test_set_ip_address_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR2 10.0.1.1": "OK"})
    cb._user_level = 1
    cb.set_ip_address_2("10.0.1.1")
    cb.close(disable=False)


def test_default_settings() -> None:
    cb = make_cb({**OPEN_1x1x1, "DEFAULT": "OK"})
    cb._user_level = 1
    cb.default_settings()
    cb.close(disable=False)


# ── wait() busy loop ─────────────────────────────────────────────────────


class _BusyThenIdleTransport(FakeTransport):
    """Returns '1' (busy) once for BUSY?, then '0' (idle) thereafter."""

    def __init__(self, responses: dict[str, str], busy_once: set[str]) -> None:
        super().__init__(responses)
        self._busy_once: set[str] = set(busy_once)

    def query(self, command: str) -> str:
        if command in self._busy_once:
            self._busy_once.discard(command)
            return "1"
        return super().query(command)


def test_wait_loops_until_not_busy() -> None:
    t = _BusyThenIdleTransport(
        {**OPEN_1x1x1, "BUSY? 1,1,1": "0"},
        busy_once={"BUSY? 1,1,1"},
    )
    cb = CoBrite(_transport=t)
    cb.open()
    cb.wait(1, 1, 1)
    cb.close(disable=False)


# ── additional port commands ──────────────────────────────────────────────


def test_get_dither() -> None:
    cb = make_cb({**OPEN_1x1x1, "DIT? 1,1,1": "0"})
    result = cb.get_dither(1, 1, 1)
    assert result == ((1, 1, 1, False),)
    cb.close(disable=False)


def test_get_dither_not_supported() -> None:
    # -1 means hardware does not support disabling dither (always-on) -> True
    cb = make_cb({**OPEN_1x1x1, "DIT? 1,1,1": "-1"})
    result = cb.get_dither(1, 1, 1)
    assert result == ((1, 1, 1, True),)
    cb.close(disable=False)


def test_set_dither() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "DIT 1,1,1,0": "OK"})
    cb.set_dither(False, 1, 1, 1)
    cb.close(disable=False)


def test_set_offset() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "OFF 1,1,1,1.5": "OK"})
    cb.set_offset(1.5, 1, 1, 1)
    cb.close(disable=False)


def test_get_offset_limits() -> None:
    cb = make_cb({**OPEN_1x1x1, "OFF:LIM? 1,1,1": "3.0"})
    result = cb.get_offset_limits(1, 1, 1)
    assert result == ((1, 1, 1, 3.0),)
    cb.close(disable=False)


def test_get_laser_alarm() -> None:
    cb = make_cb({**OPEN_1x1x1, "LALAR? 1,1,1": "0"})
    result = cb.get_laser_alarm(1, 1, 1)
    assert result == ((1, 1, 1, 0),)
    cb.close(disable=False)


def test_get_trigger_out_active() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIOUTACT? 1,1,1": "0"})
    result = cb.get_trigger_out_active(1, 1, 1)
    assert result == ((1, 1, 1, False),)
    cb.close(disable=False)


def test_get_trigger_config() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRICONF? 1,1,1": "193.4,0.0,3.0,0,0,0"})
    result = cb.get_trigger_config(1, 1, 1)
    cfg = result[0][-1]
    assert cfg["frequency"] == 193.4
    assert cfg["state"] is False
    cb.close(disable=False)


def test_set_config() -> None:
    cb = make_cb({**OPEN_1x1x1, **BUSY_1x1x1, "CONF 1,1,1,193.4,0.0,3.0,1,0": "OK"})
    cb.set_config(193.4, 0.0, 3.0, True, 0, chassis=1, slot=1, device=1)
    cb.close(disable=False)


def test_set_config_dict_form() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        **BUSY_1x1x1,
        "CONF? 1,1,1": "193.4,0.0,3.0,1,0,0",
        "CONF 1,1,1,193.4,0.0,3.0,1,0": "OK",
    })
    cfg = cb.get_config(1, 1, 1)[0][-1]
    cb.set_config(cfg, chassis=1, slot=1, device=1)
    cb.close(disable=False)


def test_get_frequency_limits() -> None:
    cb = make_cb({**OPEN_1x1x1, "FREQ:LIM? 1,1,1": "191.5,196.25"})
    result = cb.get_frequency_limits(1, 1, 1)
    lim = result[0][-1]
    assert lim["min"] == 191.5
    assert lim["max"] == 196.25
    cb.close(disable=False)


def test_set_trigger_config() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        **BUSY_1x1x1,
        "TRICONF 1,1,1,193.4,0.0,3.0,0,0": "OK",
    })
    cb._user_level = 1
    cb.set_trigger_config(193.4, 0.0, 3.0, False, 0, chassis=1, slot=1, device=1)
    cb.close(disable=False)


def test_set_trigger_config_dict_form() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        **BUSY_1x1x1,
        "TRICONF? 1,1,1": "193.4,0.0,3.0,0,0,0",
        "TRICONF 1,1,1,193.4,0.0,3.0,0,0": "OK",
    })
    cb._user_level = 1
    cfg = cb.get_trigger_config(1, 1, 1)[0][-1]
    cb.set_trigger_config(cfg, chassis=1, slot=1, device=1)
    cb.close(disable=False)


# ── level-0 system commands (network / misc) ──────────────────────────────


def test_get_ip_address() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR?": "192.168.1.10"})
    assert cb.get_ip_address() == "192.168.1.10"
    cb.close(disable=False)


def test_get_ip_address_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR1?": "192.168.1.11"})
    assert cb.get_ip_address_1() == "192.168.1.11"
    cb.close(disable=False)


def test_get_ip_address_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPADDR2?": "192.168.2.10"})
    assert cb.get_ip_address_2() == "192.168.2.10"
    cb.close(disable=False)


def test_get_netmask() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK?": "255.255.255.0"})
    assert cb.get_netmask() == "255.255.255.0"
    cb.close(disable=False)


def test_get_netmask_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK1?": "255.255.255.0"})
    assert cb.get_netmask_1() == "255.255.255.0"
    cb.close(disable=False)


def test_get_netmask_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK2?": "255.255.0.0"})
    assert cb.get_netmask_2() == "255.255.0.0"
    cb.close(disable=False)


def test_get_gateway_ip() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP?": "192.168.1.1"})
    assert cb.get_gateway_ip() == "192.168.1.1"
    cb.close(disable=False)


def test_get_gateway_ip_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP1?": "192.168.1.1"})
    assert cb.get_gateway_ip_1() == "192.168.1.1"
    cb.close(disable=False)


def test_get_gateway_ip_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP2?": "192.168.2.1"})
    assert cb.get_gateway_ip_2() == "192.168.2.1"
    cb.close(disable=False)


def test_get_dns_ip() -> None:
    cb = make_cb({**OPEN_1x1x1, "DNSIP?": "8.8.8.8"})
    assert cb.get_dns_ip() == "8.8.8.8"
    cb.close(disable=False)


def test_get_mac_address() -> None:
    cb = make_cb({**OPEN_1x1x1, "MACADDRESS?": "00:11:22:33:44:55"})
    assert cb.get_mac_address() == "00:11:22:33:44:55"
    cb.close(disable=False)


def test_get_mac_address_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "MACADDRESS1?": "AA:BB:CC:DD:EE:01"})
    assert cb.get_mac_address_1() == "AA:BB:CC:DD:EE:01"
    cb.close(disable=False)


def test_get_mac_address_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "MACADDRESS2?": "AA:BB:CC:DD:EE:02"})
    assert cb.get_mac_address_2() == "AA:BB:CC:DD:EE:02"
    cb.close(disable=False)


def test_get_usb_ip_address() -> None:
    cb = make_cb({**OPEN_1x1x1, "USBIPADDR?": "169.254.0.1"})
    assert cb.get_usb_ip_address() == "169.254.0.1"
    cb.close(disable=False)


def test_get_usb_netmask() -> None:
    cb = make_cb({**OPEN_1x1x1, "USBNETMASK?": "255.255.0.0"})
    assert cb.get_usb_netmask() == "255.255.0.0"
    cb.close(disable=False)


def test_get_ip_config_changed() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPCCH?": "1"})
    assert cb.get_ip_config_changed() is True
    cb.close(disable=False)


def test_get_dhcp() -> None:
    cb = make_cb({**OPEN_1x1x1, "DHCP?": "on"})
    assert cb.get_dhcp() == "on"
    cb.close(disable=False)


def test_get_start_default() -> None:
    cb = make_cb({**OPEN_1x1x1, "STADEF?": "0"})
    assert cb.get_start_default() is False
    cb.close(disable=False)


def test_get_enable_autostart() -> None:
    cb = make_cb({**OPEN_1x1x1, "ENABAUTOSTA?": "1"})
    assert cb.get_enable_autostart() is True
    cb.close(disable=False)


def test_get_trigger_delay() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIDEL?": "10"})
    assert cb.get_trigger_delay() == 10
    cb.close(disable=False)


def test_init_interface_resets_user_level() -> None:
    cb = make_cb({**OPEN_1x1x1, "INTI": "OK"})
    cb._user_level = 1
    cb.init_interface()
    assert cb._user_level == 0
    cb.close(disable=False)


def test_get_card_info() -> None:
    cb = make_cb({**OPEN_1x1x1, "CARD:INFO? 1,1": "GC,SN001"})
    assert cb.get_card_info(1, 1) == "GC,SN001"
    cb.close(disable=False)


def test_wait_ms() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAITMS 100": "OK"})
    cb.wait_ms(100)
    cb.close(disable=False)


def test_login_logout() -> None:
    cb = make_cb({**OPEN_1x1x1, "PASS 0": "OK", "PASS?": "0"})
    cb._user_level = 1
    level = cb.login(0)
    assert level == 0
    assert cb._user_level == 0
    cb.close(disable=False)


def test_full_info() -> None:
    cb = make_cb({
        **OPEN_1x1x1,
        "*IDN?": "ID Photonics,CoBrite-DX,SN001,1.0",
        "CONF? 1,1,1": "193.4,0.0,3.0,1,0,0",
    })
    info = cb.full_info()
    assert "CoBrite" in info
    assert "193.4000 THz" in info
    assert "1550.12 nm" in info
    assert "3.00 dBm" in info
    assert "ENABLED" in info
    cb.close(disable=False)


# ── level-1 system commands (network / misc) ──────────────────────────────


def test_default_ip_config() -> None:
    cb = make_cb({**OPEN_1x1x1, "IPCDEF": "OK"})
    cb._user_level = 1
    cb.default_ip_config()
    cb.close(disable=False)


def test_set_netmask() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK 255.255.255.0": "OK"})
    cb._user_level = 1
    cb.set_netmask("255.255.255.0")
    cb.close(disable=False)


def test_set_netmask_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK1 255.255.255.0": "OK"})
    cb._user_level = 1
    cb.set_netmask_1("255.255.255.0")
    cb.close(disable=False)


def test_set_netmask_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "NETMASK2 255.255.0.0": "OK"})
    cb._user_level = 1
    cb.set_netmask_2("255.255.0.0")
    cb.close(disable=False)


def test_set_gateway_ip() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP 192.168.1.1": "OK"})
    cb._user_level = 1
    cb.set_gateway_ip("192.168.1.1")
    cb.close(disable=False)


def test_set_gateway_ip_1() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP1 192.168.1.1": "OK"})
    cb._user_level = 1
    cb.set_gateway_ip_1("192.168.1.1")
    cb.close(disable=False)


def test_set_gateway_ip_2() -> None:
    cb = make_cb({**OPEN_1x1x1, "GATEWAYIP2 192.168.2.1": "OK"})
    cb._user_level = 1
    cb.set_gateway_ip_2("192.168.2.1")
    cb.close(disable=False)


def test_set_dns_ip() -> None:
    cb = make_cb({**OPEN_1x1x1, "DNSIP 8.8.8.8": "OK"})
    cb._user_level = 1
    cb.set_dns_ip("8.8.8.8")
    cb.close(disable=False)


def test_set_start_default() -> None:
    cb = make_cb({**OPEN_1x1x1, "STADEF 1": "OK"})
    cb._user_level = 1
    cb.set_start_default(True)
    cb.close(disable=False)


def test_set_enable_autostart() -> None:
    cb = make_cb({**OPEN_1x1x1, "ENABAUTOSTA 1": "OK"})
    cb._user_level = 1
    cb.set_enable_autostart(True)
    cb.close(disable=False)


def test_set_password() -> None:
    cb = make_cb({**OPEN_1x1x1, "SPASS newpass": "OK"})
    cb._user_level = 1
    cb.set_password("newpass")
    cb.close(disable=False)


# ── port commands (additional) ────────────────────────────────────────────


def test_get_power_limits() -> None:
    cb = make_cb({**OPEN_1x1x1, "POW:LIM? 1,1,1": "0.0,13.0"})
    result = cb.get_power_limits(1, 1, 1)
    lim = result[0][-1]
    assert lim["min"] == 0.0
    assert lim["max"] == 13.0
    cb.close(disable=False)


# ── property getters (remaining) ──────────────────────────────────────────


def test_wavelength_limits_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "WAV:LIM? 1,1,1": "1528.77,1566.31"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        lim = cb.wavelength_limits
    assert lim["min"] == 1528.77
    assert lim["max"] == 1566.31
    cb.close(disable=False)


def test_frequency_limits_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "FREQ:LIM? 1,1,1": "191.5,196.25"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        lim = cb.frequency_limits
    assert lim["min"] == 191.5
    assert lim["max"] == 196.25
    cb.close(disable=False)


def test_power_limits_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "POW:LIM? 1,1,1": "0.0,13.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        lim = cb.power_limits
    assert lim["min"] == 0.0
    assert lim["max"] == 13.0
    cb.close(disable=False)


def test_offset_limits_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "OFF:LIM? 1,1,1": "3.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.offset_limits == 3.0
    cb.close(disable=False)


def test_limits_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "LIM? 1,1,1": "191.5,196.25,3.0,0.0,13.0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        lim = cb.limits
    assert lim["freq_min"] == 191.5
    assert lim["pow_max"] == 13.0
    cb.close(disable=False)


def test_laser_alarm_property() -> None:
    cb = make_cb({**OPEN_1x1x1, "LALAR? 1,1,1": "0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.laser_alarm == 0
    cb.close(disable=False)


def test_trigger_out_active_property_get() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRIOUTACT? 1,1,1": "1"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        assert cb.trigger_out_active is True
    cb.close(disable=False)


def test_trigger_config_property_get() -> None:
    cb = make_cb({**OPEN_1x1x1, "TRICONF? 1,1,1": "193.4,0.0,3.0,0,0,0"})
    with pytest.warns(DeprecationWarning):
        cb.set_active_port(1, 1, 1)
        cfg = cb.trigger_config
    assert cfg["frequency"] == 193.4
    assert cfg["state"] is False
    cb.close(disable=False)
