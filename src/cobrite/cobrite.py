"""Python driver for ID Photonics CoBrite tunable laser controllers."""

import enum
import logging
import re
import socket
import webbrowser
from collections.abc import Callable, Generator
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast, overload

from pyvisa import ResourceManager

# from pyvisa.errors import VisaIOError
from pyvisa.resources import MessageBasedResource


class ScpiStatus(enum.IntEnum):
    """SCPI status codes returned by the device firmware (mirrors ``scpi_status_t``)."""

    COMPLETED_SUCCESSFULLY = 0
    IN_PROGRESS = 1
    COMMAND_ERROR = 100
    COMMAND_ERROR_WRONG_ARG_COUNT = 101
    COMMAND_ERROR_POWER_DOWN = 104
    EXEC_ERROR_UNAUTHORIZED = 201
    EXEC_ERROR_COMMLOCK = 204


_ERR_RE = re.compile(r"^ERR\s*(\d+),\s*(.*)", re.DOTALL)


class CoBriteError(RuntimeError):
    """Raised when the device returns an ``ERR`` response.

    Attributes:
        code: Structured SCPI status code from the device.
        detail: Raw detail string (encoded command + error message) from the device.
    """

    code: ScpiStatus | int
    detail: str

    def __init__(self, code: ScpiStatus | int, detail: str) -> None:
        try:
            code = ScpiStatus(code)
        except ValueError:
            pass
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


# logging
logger = logging.getLogger("cobrite")
logger.setLevel(logging.WARNING)

_ch = logging.StreamHandler()
_ch.setLevel(logger.level)

_formatter = logging.Formatter(
    "{asctime:15s} | {levelname:4.4s} | {filename:>20s}:{lineno:<5d} | {name:<15.15s} | {message}",
    style="{",
)

_ch.setFormatter(_formatter)
logger.addHandler(_ch)


F = TypeVar("F", bound=Callable[..., object])
T = TypeVar("T")


class cbProto(Protocol):
    _connected: bool


class cbRetryProto(Protocol):
    max_retries: int


class cbLevelProto(Protocol):
    _user_level: int


class Transport(Protocol):
    """Wire-level transport for CoBrite.

    Implement this Protocol to provide a custom or fake transport.
    The `_transport` parameter of `CoBrite.__init__` accepts any object
    satisfying this interface — intended for testing only.
    """

    def query(self, command: str) -> str:
        """Send a SCPI command string and return the stripped response."""
        ...

    def close(self) -> None:
        """Release the underlying connection."""
        ...


class _VisaTransport:  # pragma: no cover
    """PyVISA adapter satisfying the Transport Protocol."""

    def __init__(self, inst: MessageBasedResource) -> None:
        self._inst: MessageBasedResource = inst

    def query(self, command: str) -> str:
        return self._inst.query(command, delay=0.2).strip()

    def close(self) -> None:
        self._inst.close()


# --- parse helpers for multi-value responses ---


def _split_n(raw: str, sep: str, n: int) -> list[str]:
    parts = raw.split(sep)
    if len(parts) != n:
        raise ValueError(f"Expected {n} values, got {len(parts)}: {raw!r}")
    return parts


def _parse_min_max(raw: str) -> dict[str, float]:
    a, b = _split_n(raw, ",", 2)
    return {"min": float(a), "max": float(b)}


def _parse_config_str(raw: str) -> dict[str, float | bool | int]:
    parts = _split_n(raw, ",", 6)
    return {
        "frequency": float(parts[0]),
        "offset": float(parts[1]),
        "power": float(parts[2]),
        "state": bool(int(parts[3])),
        "busy": bool(int(parts[4])),
        "dither": int(parts[5]),
    }


def _parse_limits(raw: str) -> dict[str, float]:
    parts = _split_n(raw, ",", 5)
    return {
        "freq_min": float(parts[0]),
        "freq_max": float(parts[1]),
        "offset_range": float(parts[2]),
        "pow_min": float(parts[3]),
        "pow_max": float(parts[4]),
    }


def _parse_monitor(raw: str) -> dict[str, float]:
    parts = _split_n(raw, ",", 4)
    return {
        "ld_chip_temp": float(parts[0]),
        "base_temp": float(parts[1]),
        "ld_current_ma": float(parts[2]),
        "tec_current_ma": float(parts[3]),
    }


def _parse_trigger_polarity(raw: str) -> dict[str, str | int]:
    direction, polarity = _split_n(raw, ",", 2)
    return {"direction": direction.strip(), "polarity": int(polarity)}


def _parse_temp(raw: str) -> dict[str, int | float]:
    parts = _split_n(raw, ",", 4)
    return {
        "chassis": int(parts[0]),
        "slot": int(parts[1]),
        "device": int(parts[2]),
        "temp": float(parts[3]),
    }


class CoBrite:
    """Driver for an ID Photonics CoBrite tunable laser controller.

    Connect with `open()`, control lasers with port commands (explicit CSD or
    property API), and disconnect with `close()`.  Level-1 commands require a
    password; supply it via `login(1)` or `login_from_file(path)`.

    **CSD addressing** — most port commands accept `chassis`, `slot`, and
    `device` integers.  Passing `0` (the default) expands to every known port
    at that level, so `set_state(True)` enables all lasers on the unit.
    Multi-port queries return a tuple of `(chassis, slot, device, value)`
    tuples, one per matched port.

    **Property API** — call `set_active_port(c, s, d)` once, then read and
    write laser parameters as plain Python attributes (`cb.wavelength`,
    `cb.power`, etc.).

    Example:
        ```python
        cb = CoBrite(address="192.168.1.99", timeout=20)
        cb.open()

        # Explicit CSD style
        cb.set_wavelength(1550.0, 1, 1, 1)
        wav = cb.get_wavelength(1, 1, 1)[0][-1]

        # Property style
        cb.set_active_port(1, 1, 1)
        cb.wavelength = 1550.0
        print(cb.wavelength)

        cb.close()
        ```
    """

    @staticmethod
    @overload
    def connect_guard(_func: F) -> F: ...

    @staticmethod
    @overload
    def connect_guard(
        _func: None = None, *, value: str = ..., exception: bool = ...
    ) -> Callable[[F], F]: ...

    @staticmethod
    def connect_guard(
        _func: F | None = None,
        *,
        value: str = "call open() to connect.",
        exception: bool = True,
    ) -> F | Callable[[F], F]:
        """Decorator that raises `ConnectionError` if the socket is not open.

        Can be used bare (`@connect_guard`) or with keyword arguments
        (`@connect_guard(exception=False)`).

        Args:
            _func: The function being decorated when used bare.
            value: Message for the `ConnectionError` (or return value when
                `exception=False`).
            exception: When `False`, return `value` instead of raising.

        Returns:
            Decorated function or decorator factory.
        """

        def decorator(func: F) -> F:
            @wraps(func)
            def wrapper(self: cbProto, *args: object, **kwargs: object) -> object:
                if not self._connected:  # pyright: ignore[reportPrivateUsage]
                    if exception:
                        raise ConnectionError(value)
                    return value
                return func(self, *args, **kwargs)  # type: ignore[call-arg]

            return cast(F, wrapper)

        if _func is not None:
            return decorator(_func)
        return decorator

    @staticmethod
    @overload
    def retry(_func: F) -> F: ...

    @staticmethod
    @overload
    def retry(_func: None = None, *, max_retries: int | None = None) -> Callable[[F], F]: ...

    @staticmethod
    def retry(
        _func: F | None = None,
        *,
        max_retries: int | None = None,
    ) -> F | Callable[[F], F]:
        """Decorator that retries the wrapped function on any exception.

        Reads `self.max_retries` unless `max_retries` is given explicitly.
        Can be used bare (`@CoBrite.retry`) or with a keyword argument
        (`@CoBrite.retry(max_retries=5)`).

        Args:
            _func: The function being decorated when used bare.
            max_retries: Override for `self.max_retries`.  When `None`,
                the instance attribute is used at call time.

        Returns:
            Decorated function or decorator factory.

        Example:
            ```python
            class MyDevice(CoBrite):
                @CoBrite.retry
                def read_sensor(self) -> float:
                    ...

                @CoBrite.retry(max_retries=5)
                def critical_read(self) -> float:
                    ...
            ```
        """

        def decorator(func: F) -> F:
            @wraps(func)
            def wrapper(self: cbRetryProto, *args: object, **kwargs: object) -> object:
                retries = max_retries if max_retries is not None else self.max_retries
                for attempt in range(retries + 1):
                    try:
                        return func(self, *args, **kwargs)  # type: ignore[call-arg]
                    except Exception:
                        if attempt >= retries:
                            raise
                raise AssertionError("unreachable")  # pragma: no cover

            return cast(F, wrapper)

        if _func is not None:
            return decorator(_func)
        return decorator

    @staticmethod
    def requires_level(level: int) -> Callable[[F], F]:
        """Decorator that raises `PermissionError` if the session user level is insufficient.

        Call `login(1)` or `login_from_file(path)` before invoking level-1 methods.
        Can be used on subclass methods: `@CoBrite.requires_level(1)`.

        Args:
            level: Minimum required user level.

        Returns:
            Decorator factory.

        Example:
            ```python
            class MyDevice(CoBrite):
                @CoBrite.requires_level(1)
                def my_protected_method(self) -> None:
                    ...
            ```
        """

        def decorator(func: F) -> F:
            @wraps(func)
            def wrapper(self: cbLevelProto, *args: object, **kwargs: object) -> object:
                if self._user_level < level:  # pyright: ignore[reportPrivateUsage]
                    raise PermissionError(
                        f"Level {level} required. Call login({level}) or login_from_file(path) first."
                    )
                return func(self, *args, **kwargs)  # type: ignore[call-arg]

            return cast(F, wrapper)

        return decorator

    @staticmethod
    def manual() -> None:
        """Open the [CoBrite user manual](https://id-photonics.com/download/cobrite-manual/) in the default web browser."""
        webbrowser.open("https://id-photonics.com/download/cobrite-manual/")

    @staticmethod
    def _redact_pass(cmd: str) -> str:
        if "PASS" in cmd and len(cmd.split(" ")) > 1 and len(cmd.split(" ")[1]) > 1:
            return "PASS <redacted>"
        return cmd

    def __init__(
        self,
        address: str = "cobrite.local",
        port: int = 2000,
        timeout: int = 10,
        max_retries: int = 3,
        open: bool = False,  # noqa: A002
        _transport: Transport | None = None,
    ):
        """Create a CoBrite driver instance.

        Args:
            address: Hostname or IP address of the CoBrite unit.
            port: TCP port number exposed by the unit (default 2000).
            timeout: Socket timeout in seconds.  Must be longer than the
                maximum laser tuning time (typically 10-30 s).
            max_retries: How many times to retry a command when the device
                returns an unparsable response before raising `RuntimeError`.
            open: When `True`, call `open()` immediately after construction.
            _transport: **For testing only.** Inject a custom `Transport`
                instead of opening a real PyVISA connection.  When set,
                `open()` skips hostname resolution and PyVISA entirely.

        Example:
            ```python
            # Lazy open
            cb = CoBrite(address="192.168.1.99", timeout=20)
            cb.open()

            # Eager open
            cb = CoBrite(address="192.168.1.99", timeout=20, open=True)
            ```
        """
        self.address: str = address
        self.port: int = port
        self.timeout: int = timeout
        self.max_retries: int = max_retries
        self._layout: dict[int, dict[int, dict[int, str]]] = {}
        self._connected: bool = False
        self._transport: Transport | None = None
        self._injected_transport: Transport | None = _transport
        self._user_level: int = 0
        self._active_port: tuple[int, int, int] | None = None
        if open:
            self.open()

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    def open(self) -> None:
        """Open the TCP connection to the CoBrite unit.

        Resolves `address` to an IP, opens a PyVISA TCPIP socket, fetches the
        device layout, and resets session parameters with `INTI`.  When a
        `_transport` was injected at construction time, the PyVISA step is
        skipped and the injected transport is used directly.

        Raises:
            socket.gaierror: If `address` cannot be resolved (real transport only).
            pyvisa.errors.VisaIOError: If the socket cannot be opened (real transport only).
        """
        if self._injected_transport is not None:
            self._transport = self._injected_transport
        else:  # pragma: no cover
            ip = socket.gethostbyname(self.address)
            resource = f"TCPIP::{ip}::{self.port}::SOCKET"
            logger.info(f"Opening connection to {resource}")
            rm = ResourceManager()
            inst = rm.open_resource(
                resource,
                read_termination=";",
                write_termination=";",
                timeout=self.timeout * 1000,
            )
            self._transport = _VisaTransport(cast(MessageBasedResource, inst))
        self._connected = True
        self.layout()
        self.init_interface()

    @connect_guard
    def query(self, command: str) -> str:
        """Send a SCPI command and return the raw response string.

        Args:
            command: SCPI command string (without terminator).

        Returns:
            Stripped response string from the device.

        Raises:
            ConnectionError: If `open()` has not been called.
            RuntimeError: If the response contains `"ERR"`.
        """
        logger.debug(f">> {self._redact_pass(command)}")
        assert self._transport
        reply = self._transport.query(command)
        logger.debug(f"<< {reply}")
        m = _ERR_RE.match(reply)
        if m:
            raise CoBriteError(int(m.group(1)), m.group(2))
        return reply

    def _query_typed(
        self, command: str, ret_type: Callable[[Any], T], retries: int | None = None
    ) -> T:
        max_retries = retries if retries is not None else self.max_retries
        for attempt in range(max_retries + 1):
            raw = self.query(command)
            try:
                return ret_type(raw)
            except Exception as e:
                if attempt >= max_retries:
                    raise RuntimeError(
                        f"Failed to parse '{raw}' after {max_retries + 1} attempts"
                    ) from e
        raise AssertionError("unreachable")  # pragma: no cover

    def write(self, command: str) -> None:
        """Send a SCPI command and discard the response.

        Internally calls `query()`, so the same error checking applies.

        Args:
            command: SCPI command string (without terminator).

        Raises:
            ConnectionError: If `open()` has not been called.
            RuntimeError: If the device returns an error response.
        """
        _ = self.query(command)

    def close(self, disable: bool = True) -> None:
        """Close the TCP connection.

        Args:
            disable: When `True` (default), disable all laser ports before
                closing by calling `set_state(False)` for every port.
        """
        logger.info("Closing instrument connection")
        if disable:
            self.set_state()
        if self._transport:
            self._transport.close()
        self._transport = None
        self._connected = False
        self._user_level = 0

    # -------------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------------

    def _login_with_password(self, password: str, level: int) -> int:
        if level > 0:
            self.write(f"PASS {password}")
        else:
            self.write("PASS 0")
        self._user_level = int(self.query("PASS?"))
        return self._user_level

    def login(self, level: int = 0, password: str = "IDP") -> int:
        """Authenticate with the device.

        The granted level is cached for the session; subsequent level-1 calls
        succeed without re-authenticating until `close()` or
        `init_interface()` resets it.  For non-interactive scripts prefer
        `login_from_file()`.

        Args:
            level: Target user level.  `0` (default) logs out.  `1` grants
                access to level-1 write commands.
            password: Password string (default `"IDP"`).  Ignored when
                `level` is `0`.

        Returns:
            The user level confirmed by the device after authentication.
        """
        return self._login_with_password(password, level)

    def login_from_file(self, path: str | Path, level: int = 1) -> int:
        """Authenticate using a password stored in a file.

        Reads the first non-empty line of `path` and uses it as the password.
        The granted level is cached exactly as with `login()`.

        Args:
            path: Path to a plain-text file whose first line is the password.
            level: Target user level (default 1).

        Returns:
            The user level confirmed by the device after authentication.

        Example:
            ```python
            cb.login_from_file("/run/secrets/cobrite_password")
            cb.set_trigger_delay(10)   # no prompt
            ```
        """
        password = Path(path).read_text().splitlines()[0].strip()
        return self._login_with_password(password, level)

    # -------------------------------------------------------------------------
    # Layout / info
    # -------------------------------------------------------------------------

    def _interpolate_csd(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> Generator[tuple[int, int, int]]:
        if chassis == 0:
            for c in range(1, self._chassis_count() + 1):
                yield from self._interpolate_csd(c, slot, device)
        elif slot == 0:
            for s in range(1, self._slot_count(chassis) + 1):
                yield from self._interpolate_csd(chassis, s, device)
        elif device == 0:
            for d in range(1, self._device_count(chassis, slot) + 1):
                yield from self._interpolate_csd(chassis, slot, d)
        else:
            yield chassis, slot, device

    def layout(self) -> dict[int, dict[int, dict[int, str]]]:
        """Fetch and cache the device layout.

        Queries `LAY?` and then `TYP?` for every discovered port.  The result
        is cached internally and used to expand wildcard (0) CSD addresses.
        Called automatically by `open()`.

        Returns:
            Nested dict `{chassis: {slot: {device: type_string}}}`.
        """
        resp: str = self.query("LAY?")
        self._layout = {}
        for line in resp.splitlines():
            _, chassis_nr, slot_nr, device_desc, *_ = line.split(",")
            chassis_nr = int(chassis_nr)
            slot_nr = int(slot_nr)
            dc = 0
            if len(device_desc) > 3:
                dc = int(device_desc[3:])
            if chassis_nr not in self._layout:
                self._layout[chassis_nr] = {}
            if slot_nr not in self._layout[chassis_nr]:
                self._layout[chassis_nr][slot_nr] = {}
            for device_nr in range(1, dc + 1):
                self._layout[chassis_nr][slot_nr][device_nr] = self.query(
                    f"TYP? {chassis_nr},{slot_nr},{device_nr}"
                )
        return self._layout

    def format_layout(self, indent: int = 2) -> str:
        """Return the device layout as an indented human-readable string.

        Args:
            indent: Number of spaces per indentation level.

        Returns:
            Multi-line string listing chassis, slots, and devices.

        Example:
            ```
            Chassis 1:
              Slot 1:
                Device 1: GC
            ```
        """
        if not self._layout:
            self.layout()
        ret = ""
        for chassis_nr, slots in self._layout.items():
            ret += f"{' ' * indent * 0}Chassis {chassis_nr}:\n"
            for slot_nr, devices in slots.items():
                ret += f"{' ' * indent * 1}Slot {slot_nr}:\n"
                for device_nr, device_type in devices.items():
                    ret += f"{' ' * indent * 2}Device {device_nr}: {device_type}\n"
        return ret[:-1]

    def full_info(self, indent: int = 2) -> str:
        """Return identification, layout, and per-port laser state as a string.

        Queries frequency, wavelength, power, and enable state for every port
        in the cached layout.

        Args:
            indent: Number of spaces per indentation level.

        Returns:
            Multi-line string suitable for console display.
        """
        if not self._layout:
            self.layout()
        ret = self.idn()
        ret += "\n"
        for chassis_nr, slots in self._layout.items():
            ret += f"{' ' * indent * 1}Chassis {chassis_nr}:\n"
            for slot_nr, devices in slots.items():
                ret += f"{' ' * indent * 2}Slot {slot_nr}:\n"
                for device_nr, device_type in devices.items():
                    pwr = self.get_power(chassis_nr, slot_nr, device_nr)[0][-1]
                    freq = self.get_frequency(chassis_nr, slot_nr, device_nr)[0][-1]
                    wl = self.get_wavelength(chassis_nr, slot_nr, device_nr)[0][-1]
                    state = self.get_state(chassis_nr, slot_nr, device_nr)[0][-1]
                    state_str = "ENABLED" if state else "disabled"
                    pwr_str = f"{pwr:.2f} dBm"
                    freq_str = f"{freq:.4f} THz"
                    wl_str = f"{wl:.2f} nm"
                    ret += (
                        f"{' ' * indent * 3}Device {device_nr}: {device_type}"
                        f" - {freq_str} ({wl_str}) @ {pwr_str}: {state_str}\n"
                    )
        return ret[:-1]

    def _chassis_count(self) -> int:
        if not self._layout:
            self.layout()
        return len(self._layout)

    def _slot_count(self, chassis: int) -> int:
        if not self._layout:
            self.layout()
        return len(self._layout[chassis])

    def _device_count(self, chassis: int, slot: int) -> int:
        if not self._layout:
            self.layout()
        return len(self._layout[chassis][slot])

    # -------------------------------------------------------------------------
    # CSD command engine
    # -------------------------------------------------------------------------

    def _cmd_csd(
        self,
        fmt: str,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        ret_type: Callable[[Any], T] = str,
        wait: bool = True,
        **kwargs: Any,
    ) -> tuple[tuple[int, int, int, T], ...]:
        retval: list[tuple[int, int, int, T]] = []
        for c, s, d in self._interpolate_csd(chassis, slot, device):
            cmd = fmt.format(csd=f"{c},{s},{d}", **kwargs)
            val = self._query_typed(cmd, ret_type)
            retval.append((c, s, d, val))
        if wait:
            self.wait(chassis, slot, device)
        return tuple(retval)

    def wait(self, chassis: int = 0, slot: int = 0, device: int = 0) -> None:
        """Poll `BUSY?` until all matched ports finish tuning.

        Called automatically by set methods unless `wait=False` is passed.
        For a server-side blocking wait without polling, use `busy_wait()`.

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
        """
        busy = False
        while True:
            for c, s, d in self._interpolate_csd(chassis, slot, device):
                busy_repl = self.query("BUSY? {csd}".format(csd=f"{c},{s},{d}"))
                busy_parse = True
                if len(busy_repl) > 0:
                    busy_parse = bool(int(busy_repl))
                busy = busy or busy_parse
            if not busy:
                break
            busy = False

    # -------------------------------------------------------------------------
    # Active port
    # -------------------------------------------------------------------------

    def set_active_port(self, chassis: int, slot: int, device: int) -> None:
        """Set the port used by all property accessors.

        Args:
            chassis: Chassis number (must be non-zero).
            slot: Slot number (must be non-zero).
            device: Device number (must be non-zero).
        """
        self._active_port = (chassis, slot, device)

    def get_active_port(self) -> tuple[int, int, int] | None:
        """Return the currently active port, or `None` if not set.

        Returns:
            `(chassis, slot, device)` tuple, or `None`.
        """
        return self._active_port

    def _require_active_port(self) -> tuple[int, int, int]:
        if self._active_port is None:
            raise RuntimeError("No active port set. Call set_active_port() first.")
        return self._active_port

    # -------------------------------------------------------------------------
    # Level-0 system commands
    # -------------------------------------------------------------------------

    def idn(self) -> str:
        """Return the identification string of the unit (`*IDN?`).

        Returns:
            Identification string, e.g. `"ID Photonics,CoBrite-DX,..."`.
        """
        return self.query("*IDN?")

    def info(self) -> str:
        """Return the system information string (`INFO?`).

        Returns the same identification and version information as `idn()` but
        via the system-level `INFO?` command.

        Returns:
            System type and software version string.
        """
        return self.query("INFO?")

    def abort(self) -> None:
        """Abort the current operation (`ABOR`)."""
        self.write("ABOR")

    def opc(self) -> bool:
        """Return `True` when all pending operations are complete (`*OPC?`).

        Returns:
            `True` if the device is idle.
        """
        return self._query_typed("*OPC?", lambda x: bool(int(x)))

    def opc_wait(self) -> None:
        """Block until all pending operations complete (`*WAI`).

        The device holds the TCP connection open until `*OPC?` would return
        `1`, eliminating the need for a client-side polling loop.
        """
        self.write("*WAI")

    def get_alarm(self) -> int:
        """Return the system alarm register (`ALAR?`).

        Returns:
            Integer alarm code; `0` means no alarm.
        """
        return self._query_typed("ALAR?", int)

    def get_error(self) -> str:
        """Return the last error string (`ERR?`).

        Returns:
            Error description string from the device.
        """
        return self.query("ERR?")

    def get_interlock(self) -> bool:
        """Return the hardware interlock state (`INTL?`).

        Returns:
            `False` when the interlock is satisfied and lasers can be enabled;
            `True` when the interlock is open (lasers blocked).
        """
        return self._query_typed("INTL?", lambda x: bool(int(x)))

    def get_remote(self) -> bool:
        """Return `True` if the unit is in remote control mode (`REMO?`).

        Returns:
            `True` = remote mode active.
        """
        return self._query_typed("REMO?", lambda x: bool(int(x)))

    def get_ip_address(self) -> str:
        """Return the Ethernet IP address (`IPADDR?`). DX and DX2 only.

        For MX: [`get_ip_address_1`][cobrite.CoBrite.get_ip_address_1] /
        [`get_ip_address_2`][cobrite.CoBrite.get_ip_address_2].

        Returns:
            Dotted-decimal IP address string.
        """
        return self.query("IPADDR?")

    def get_ip_address_1(self) -> str:
        """Return the front-panel Ethernet IP address (`IPADDR1?`). MX only.

        For DX/DX2: [`get_ip_address`][cobrite.CoBrite.get_ip_address].

        Returns:
            Dotted-decimal IP address string.
        """
        return self.query("IPADDR1?")

    def get_ip_address_2(self) -> str:
        """Return the rear-panel Ethernet IP address (`IPADDR2?`). MX only.

        For DX/DX2: [`get_ip_address`][cobrite.CoBrite.get_ip_address].

        Returns:
            Dotted-decimal IP address string.
        """
        return self.query("IPADDR2?")

    def get_netmask(self) -> str:
        """Return the Ethernet netmask (`NETMASK?`). DX and DX2 only.

        For MX: [`get_netmask_1`][cobrite.CoBrite.get_netmask_1] /
        [`get_netmask_2`][cobrite.CoBrite.get_netmask_2].

        Returns:
            Dotted-decimal netmask string.
        """
        return self.query("NETMASK?")

    def get_netmask_1(self) -> str:
        """Return the front-panel Ethernet netmask (`NETMASK1?`). MX only.

        For DX/DX2: [`get_netmask`][cobrite.CoBrite.get_netmask].

        Returns:
            Dotted-decimal netmask string.
        """
        return self.query("NETMASK1?")

    def get_netmask_2(self) -> str:
        """Return the rear-panel Ethernet netmask (`NETMASK2?`). MX only.

        For DX/DX2: [`get_netmask`][cobrite.CoBrite.get_netmask].

        Returns:
            Dotted-decimal netmask string.
        """
        return self.query("NETMASK2?")

    def get_gateway_ip(self) -> str:
        """Return the gateway IP address (`GATEWAYIP?`). DX and DX2 only.

        For MX: [`get_gateway_ip_1`][cobrite.CoBrite.get_gateway_ip_1] /
        [`get_gateway_ip_2`][cobrite.CoBrite.get_gateway_ip_2].

        Returns:
            Dotted-decimal gateway IP string.
        """
        return self.query("GATEWAYIP?")

    def get_gateway_ip_1(self) -> str:
        """Return the front-panel gateway IP address (`GATEWAYIP1?`). MX only.

        For DX/DX2: [`get_gateway_ip`][cobrite.CoBrite.get_gateway_ip].

        Returns:
            Dotted-decimal gateway IP string.
        """
        return self.query("GATEWAYIP1?")

    def get_gateway_ip_2(self) -> str:
        """Return the rear-panel gateway IP address (`GATEWAYIP2?`). MX only.

        For DX/DX2: [`get_gateway_ip`][cobrite.CoBrite.get_gateway_ip].

        Returns:
            Dotted-decimal gateway IP string.
        """
        return self.query("GATEWAYIP2?")

    def get_dns_ip(self) -> str:
        """Return the primary DNS server IP address (`DNSIP?`).

        Returns:
            Dotted-decimal DNS IP string.
        """
        return self.query("DNSIP?")

    def get_mac_address(self) -> str:
        """Return the Ethernet MAC address (`MACADDRESS?`). DX and DX2 only.

        For MX: [`get_mac_address_1`][cobrite.CoBrite.get_mac_address_1] /
        [`get_mac_address_2`][cobrite.CoBrite.get_mac_address_2].

        Returns:
            MAC address string.
        """
        return self.query("MACADDRESS?")

    def get_mac_address_1(self) -> str:
        """Return the front-panel Ethernet MAC address (`MACADDRESS1?`). MX only.

        For DX/DX2: [`get_mac_address`][cobrite.CoBrite.get_mac_address].

        Returns:
            MAC address string.
        """
        return self.query("MACADDRESS1?")

    def get_mac_address_2(self) -> str:
        """Return the rear-panel Ethernet MAC address (`MACADDRESS2?`). MX only.

        For DX/DX2: [`get_mac_address`][cobrite.CoBrite.get_mac_address].

        Returns:
            MAC address string.
        """
        return self.query("MACADDRESS2?")

    def get_usb_ip_address(self) -> str:
        """Return the IP address of the virtual Ethernet interface over USB (`USBIPADDR?`).

        Returns:
            Dotted-decimal IP address string.
        """
        return self.query("USBIPADDR?")

    def get_usb_netmask(self) -> str:
        """Return the netmask of the virtual Ethernet interface over USB (`USBNETMASK?`).

        Returns:
            Dotted-decimal netmask string.
        """
        return self.query("USBNETMASK?")

    def get_ip_config_changed(self) -> bool:
        """Return `True` if network config has changed since last reboot (`IPCCH?`).

        Returns:
            `True` when a reboot is required for network changes to take effect.
        """
        return self._query_typed("IPCCH?", lambda x: bool(int(x)))

    def get_dhcp(self) -> str:
        """Return the DHCP setting for the Ethernet interface (`DHCP?`).

        Returns:
            `"on"` if DHCP is enabled, `"off"` if static IP is configured.
        """
        return self.query("DHCP?")

    def get_lockout(self) -> bool:
        """Return the write-lockout state (`LOCK?`).

        Returns:
            `True` when another session holds the write lock.
        """
        return self._query_typed("LOCK?", lambda x: bool(int(x)))

    def get_param_refresh(self) -> int:
        """Return the parameter-refresh change counter (`PREF?`).

        The counter increments each time any parameter changes on the device.
        Poll this to detect changes without querying every parameter.

        Returns:
            Integer change counter.
        """
        return self._query_typed("PREF?", int)

    def get_start_default(self) -> bool:
        """Return `True` if the unit starts with factory defaults on boot (`STADEF?`).

        Returns:
            `True` = factory defaults applied on next start.
        """
        return self._query_typed("STADEF?", lambda x: bool(int(x)))

    def get_enable_autostart(self) -> bool:
        """Return `True` if laser on/off state is preserved across reboots (`ENABAUTOSTA?`).

        Returns:
            `True` = autostart enabled.
        """
        return self._query_typed("ENABAUTOSTA?", lambda x: bool(int(x)))

    def get_trigger_delay(self) -> int:
        """Return the hardware trigger delay in milliseconds (`TRIDEL?`).

        Returns:
            Trigger delay in ms.
        """
        return self._query_typed("TRIDEL?", int)

    def get_trigger_polarity(self) -> dict[str, str | int]:
        """Return the hardware trigger polarity settings (`TRIPOL?`).

        Returns:
            Dict with keys:

            - `direction` (`str`): `"IN"` or `"OUT"`.
            - `polarity` (`int`): `0` or `1`.
        """
        return self._query_typed("TRIPOL?", _parse_trigger_polarity)

    def get_temp(self) -> dict[str, int | float]:
        """Return the location and temperature of the hottest laser (`TEMP?`).

        Returns:
            Dict with keys:

            - `chassis` (`int`)
            - `slot` (`int`)
            - `device` (`int`)
            - `temp` (`float`): Temperature in °C.
        """
        return self._query_typed("TEMP?", _parse_temp)

    def get_fan(self) -> str:
        """Return the current fan level as a percentage string (`FAN?`).

        Returns:
            Fan level string (device-dependent format).
        """
        return self.query("FAN?")

    def get_echo(self) -> bool:
        """Return `True` if command echo is enabled (`ECHO?`).

        Returns:
            `True` = echo on.
        """
        return self._query_typed("ECHO?", lambda x: bool(int(x)))

    def set_echo(self, enable: bool) -> None:
        """Enable or disable command echo (`ECHO`).

        Args:
            enable: `True` to enable echo, `False` to disable.
        """
        self.write(f"ECHO {int(enable)}")

    def get_time(self) -> int:
        """Return the system time as a Unix timestamp (`TIME?`).

        The clock is volatile; it must be set after each cold start.

        Returns:
            Unix timestamp (seconds since epoch).
        """
        return self._query_typed("TIME?", int)

    def set_time(self, t: int) -> None:
        """Set the system time (`TIME`).

        Stored in volatile memory; must be set after each cold start.

        Args:
            t: Unix timestamp (seconds since epoch).
        """
        self.write(f"TIME {t}")

    def identify(self, enable: bool) -> None:
        """Blink a visible indicator to identify the unit in a multi-unit setup (`IDENT`).

        Args:
            enable: `True` to start blinking, `False` to stop.
        """
        self.write(f"IDENT {int(enable)}")

    def init_interface(self) -> None:
        """Reset session parameters to defaults (`INTI`).

        Resets ECHO, PASS, FORMAT, LINLOG, and EVENT to their default values.
        Also resets the cached user level to 0.  Called automatically by
        `open()`.
        """
        self.write("INTI")
        self._user_level = 0

    def get_card_info(self, chassis: int, slot: int) -> str:
        """Return card information for a specific slot (`CARD:INFO?`).

        Args:
            chassis: Chassis number.
            slot: Slot number.

        Returns:
            Card information string from the device.
        """
        return self.query(f"CARD:INFO? {chassis},{slot}")

    def wait_ms(self, ms: int) -> None:
        """Insert a server-side delay before processing the next buffered command (`WAITMS`).

        Args:
            ms: Delay in milliseconds.
        """
        self.write(f"WAITMS {ms}")

    # -------------------------------------------------------------------------
    # Level-0 port commands
    # -------------------------------------------------------------------------

    def set_state(
        self,
        state: bool = False,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Enable or disable laser output (`STAT`).

        Property equivalent: [`state`][cobrite.CoBrite.state].

        Args:
            state: `True` to enable, `False` to disable.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd("STAT {csd},{state}", chassis, slot, device, state=int(state), wait=wait)

    def get_state(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, bool], ...]:
        """Return the enable state of matched laser ports (`STAT?`).

        Property equivalent: [`state`][cobrite.CoBrite.state].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, enabled)` per matched port.
        """
        return self._cmd_csd(
            "STAT? {csd}", chassis, slot, device, ret_type=lambda x: bool(int(x)), wait=False
        )

    def set_wavelength(
        self,
        wavelength: float,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Set the target wavelength in nm (`WAV`).

        Property equivalent: [`wavelength`][cobrite.CoBrite.wavelength].

        Args:
            wavelength: Target wavelength in nanometres.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd("WAV {csd},{wav}", chassis, slot, device, wav=wavelength, wait=wait)

    def get_wavelength(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the target wavelength in nm (`WAV?`).

        Property equivalent: [`wavelength`][cobrite.CoBrite.wavelength].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, wavelength_nm)` per matched port.
        """
        return self._cmd_csd("WAV? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_wavelength_limits(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float]], ...]:
        """Return the tunable wavelength range in nm (`WAV:LIM?`).

        Property equivalent: [`wavelength_limits`][cobrite.CoBrite.wavelength_limits].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, limits)` where `limits` is
            `{"min": float, "max": float}` in nanometres.
        """
        return self._cmd_csd(
            "WAV:LIM? {csd}", chassis, slot, device, ret_type=_parse_min_max, wait=False
        )

    def set_frequency(
        self,
        frequency: float,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Set the target frequency in THz (`FREQ`).

        Property equivalent: [`frequency`][cobrite.CoBrite.frequency].

        Args:
            frequency: Target frequency in terahertz.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd("FREQ {csd},{freq}", chassis, slot, device, freq=frequency, wait=wait)

    def get_frequency(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the target frequency in THz (`FREQ?`).

        Property equivalent: [`frequency`][cobrite.CoBrite.frequency].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, frequency_thz)` per matched port.
        """
        return self._cmd_csd("FREQ? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_frequency_limits(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float]], ...]:
        """Return the tunable frequency range in THz (`FREQ:LIM?`).

        Property equivalent: [`frequency_limits`][cobrite.CoBrite.frequency_limits].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, limits)` where `limits` is
            `{"min": float, "max": float}` in terahertz.
        """
        return self._cmd_csd(
            "FREQ:LIM? {csd}", chassis, slot, device, ret_type=_parse_min_max, wait=False
        )

    def set_power(
        self,
        power: float,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Set the target output power in dBm (`POW`).

        Property equivalent: [`power`][cobrite.CoBrite.power].

        Args:
            power: Target output power in dBm.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd("POW {csd},{pow}", chassis, slot, device, pow=power, wait=wait)

    def get_power(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the target output power in dBm (`POW?`).

        Property equivalent: [`power`][cobrite.CoBrite.power].
        For the actual measured power, use [`get_actual_power`][cobrite.CoBrite.get_actual_power].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, power_dbm)` per matched port.
        """
        return self._cmd_csd("POW? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_actual_power(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the actual measured output power in dBm (`APOW?`).

        Property equivalent: [`actual_power`][cobrite.CoBrite.actual_power].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, actual_power_dbm)` per matched port.
        """
        return self._cmd_csd("APOW? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_power_limits(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float]], ...]:
        """Return the output power range in dBm (`POW:LIM?`).

        Property equivalent: [`power_limits`][cobrite.CoBrite.power_limits].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, limits)` where `limits` is
            `{"min": float, "max": float}` in dBm.
        """
        return self._cmd_csd(
            "POW:LIM? {csd}", chassis, slot, device, ret_type=_parse_min_max, wait=False
        )

    def set_offset(
        self,
        offset: float,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Set the frequency offset in GHz (`OFF`).

        Property equivalent: [`offset`][cobrite.CoBrite.offset].
        The offset is added to the nominal frequency set by [`set_frequency`][cobrite.CoBrite.set_frequency].
        Use [`get_offset_limits`][cobrite.CoBrite.get_offset_limits] for the allowed range (symmetric, ±limit).

        Args:
            offset: Frequency offset in GHz.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd("OFF {csd},{off}", chassis, slot, device, off=offset, wait=wait)

    def get_offset(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the current frequency offset in GHz (`OFF?`).

        Property equivalent: [`offset`][cobrite.CoBrite.offset].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, offset_ghz)` per matched port.
        """
        return self._cmd_csd("OFF? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_offset_limits(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, float], ...]:
        """Return the symmetric frequency offset limit in GHz (`OFF:LIM?`).

        Property equivalent: [`offset_limits`][cobrite.CoBrite.offset_limits].
        The allowed offset range is `[-limit, +limit]`.

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, offset_limit_ghz)` per matched port.
        """
        return self._cmd_csd("OFF:LIM? {csd}", chassis, slot, device, ret_type=float, wait=False)

    def get_limits(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float]], ...]:
        """Return all tuning limits in one query (`LIM?`).

        Property equivalent: [`limits`][cobrite.CoBrite.limits].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, limits)` where `limits` is a dict
            with keys:

            - `freq_min`, `freq_max` (`float`): Frequency range in THz.
            - `offset_range` (`float`): Symmetric offset limit in GHz.
            - `pow_min`, `pow_max` (`float`): Power range in dBm.
        """
        return self._cmd_csd(
            "LIM? {csd}", chassis, slot, device, ret_type=_parse_limits, wait=False
        )

    def get_config(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float | bool | int]], ...]:
        """Return the full laser configuration in one query (`CONF?`).

        Property equivalent: [`laser_config`][cobrite.CoBrite.laser_config].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, config)` where `config` is a dict
            with keys:

            - `frequency` (`float`): Target frequency in THz.
            - `offset` (`float`): Frequency offset in GHz.
            - `power` (`float`): Target output power in dBm.
            - `state` (`bool`): Laser enabled.
            - `busy` (`bool`): Tuning in progress.
            - `dither` (`int`): Dither state (`1` = on, `0` = off, `-1` = not supported).
        """
        return self._cmd_csd(
            "CONF? {csd}", chassis, slot, device, ret_type=_parse_config_str, wait=False
        )

    def set_config(
        self,
        frequency: float,
        offset: float,
        power: float,
        state: bool,
        dither: int,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Set all laser parameters atomically in a single command (`CONF`).

        Property equivalent: [`laser_config`][cobrite.CoBrite.laser_config].

        Args:
            frequency: Target frequency in THz.
            offset: Frequency offset in GHz.
            power: Target output power in dBm.
            state: `True` to enable the laser.
            dither: Dither state (`1` = on, `0` = off, `-1` = not supported).
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until tuning completes.
        """
        self._cmd_csd(
            "CONF {csd},{frequency},{offset},{power},{state},{dither}",
            chassis,
            slot,
            device,
            frequency=frequency,
            offset=offset,
            power=power,
            state=int(state),
            dither=dither,
            wait=wait,
        )

    def get_monitor(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float]], ...]:
        """Return thermal and current monitor readings (`MON?`).

        Property equivalent: [`monitor`][cobrite.CoBrite.monitor].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, monitor)` where `monitor` is a
            dict with keys:

            - `ld_chip_temp` (`float`): Laser diode chip temperature in °C.
            - `base_temp` (`float`): Module base temperature in °C.
            - `ld_current_ma` (`float`): Laser diode drive current in mA.
            - `tec_current_ma` (`float`): TEC current in mA.
        """
        return self._cmd_csd(
            "MON? {csd}", chassis, slot, device, ret_type=_parse_monitor, wait=False
        )

    def get_dither(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, bool], ...]:
        """Return the dither enable state (`DIT?`).

        Property equivalent: [`dither`][cobrite.CoBrite.dither].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, dither_enabled)` per matched port.
        """
        return self._cmd_csd(
            "DIT? {csd}", chassis, slot, device, ret_type=lambda x: bool(int(x)), wait=False
        )

    def set_dither(
        self,
        enable: bool,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Enable or disable dither on matched laser ports (`DIT`).

        Property equivalent: [`dither`][cobrite.CoBrite.dither].

        Args:
            enable: `True` to enable dither, `False` to disable.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until operation completes.
        """
        self._cmd_csd("DIT {csd},{v}", chassis, slot, device, v=int(enable), wait=wait)

    def get_laser_alarm(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, int], ...]:
        """Return the per-port laser alarm code (`LALAR?`).

        Property equivalent: [`laser_alarm`][cobrite.CoBrite.laser_alarm].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, alarm_code)` per matched port.
            `0` means no alarm.
        """
        return self._cmd_csd("LALAR? {csd}", chassis, slot, device, ret_type=int, wait=False)

    def busy_wait(self, chassis: int = 0, slot: int = 0, device: int = 0) -> None:
        """Send a server-side blocking wait command (`BWAI`).

        The device holds the TCP connection open until the laser finishes
        tuning, avoiding the round-trip overhead of client-side `BUSY?`
        polling.  Prefer this over `wait()` for single-port workflows.

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
        """
        self._cmd_csd("BWAI {csd}", chassis, slot, device, wait=False)

    def get_trigger_out_active(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, bool], ...]:
        """Return whether a port contributes to the hardware trigger output (`TRIOUTACT?`).

        Property equivalent: [`trigger_out_active`][cobrite.CoBrite.trigger_out_active].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, active)` per matched port.
        """
        return self._cmd_csd(
            "TRIOUTACT? {csd}", chassis, slot, device, ret_type=lambda x: bool(int(x)), wait=False
        )

    def get_trigger_config(
        self, chassis: int = 0, slot: int = 0, device: int = 0
    ) -> tuple[tuple[int, int, int, dict[str, float | bool | int]], ...]:
        """Return the buffered trigger configuration (`TRICONF?`).

        Property equivalent: [`trigger_config`][cobrite.CoBrite.trigger_config].
        The trigger config is applied when the hardware trigger input fires.
        It has the same structure as the regular laser config from [`get_config`][cobrite.CoBrite.get_config].

        Args:
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.

        Returns:
            Tuple of `(chassis, slot, device, config)` per matched port.
            See [`get_config`][cobrite.CoBrite.get_config] for the dict key descriptions.
        """
        return self._cmd_csd(
            "TRICONF? {csd}", chassis, slot, device, ret_type=_parse_config_str, wait=False
        )

    # -------------------------------------------------------------------------
    # Level-1 system commands
    # -------------------------------------------------------------------------

    @requires_level(1)
    def reset(self) -> None:
        """Perform a warm restart of the controller (`*RST`).

        All open sessions are closed.  Requires level 1.
        """
        self.write("*RST")
        self._connected = False
        self._user_level = 0

    @requires_level(1)
    def clear_status(self) -> None:
        """Clear all status and alarm registers (`*CLS`).

        Requires level 1.
        """
        self.write("*CLS")

    @requires_level(1)
    def default_settings(self) -> None:
        """Reset all laser settings to factory defaults (`DEFAULT`).

        Network configuration is not affected.  Requires level 1.
        """
        self.write("DEFAULT")

    @requires_level(1)
    def default_ip_config(self) -> None:
        """Reset network configuration to factory defaults (`IPCDEF`).

        Changes take effect after the next reboot.  Requires level 1.
        """
        self.write("IPCDEF")

    @requires_level(1)
    def set_dhcp(self, enable: bool) -> None:
        """Enable or disable DHCP on the Ethernet interface (`DHCP`).

        Requires level 1.

        Args:
            enable: `True` to enable DHCP, `False` for static IP.
        """
        self.write(f"DHCP {'on' if enable else 'off'}")

    @requires_level(1)
    def set_ip_address(self, ip: str) -> None:
        """Set the Ethernet IP address (`IPADDR`). DX and DX2 only.

        For MX: [`set_ip_address_1`][cobrite.CoBrite.set_ip_address_1]/
        [`set_ip_address_2`][cobrite.CoBrite.set_ip_address_2].

        Requires level 1.

        Args:
            ip: Dotted-decimal IP address string.
        """
        self.write(f"IPADDR {ip}")

    @requires_level(1)
    def set_ip_address_1(self, ip: str) -> None:
        """Set the front-panel Ethernet IP address (`IPADDR1`). MX only.

        For DX/DX2: [`set_ip_address`][cobrite.CoBrite.set_ip_address].

        Requires level 1.

        Args:
            ip: Dotted-decimal IP address string.
        """
        self.write(f"IPADDR1 {ip}")

    @requires_level(1)
    def set_ip_address_2(self, ip: str) -> None:
        """Set the rear-panel Ethernet IP address (`IPADDR2`). MX only.

        For DX/DX2: [`set_ip_address`][cobrite.CoBrite.set_ip_address].

        Requires level 1.

        Args:
            ip: Dotted-decimal IP address string.
        """
        self.write(f"IPADDR2 {ip}")

    @requires_level(1)
    def set_netmask(self, mask: str) -> None:
        """Set the Ethernet netmask (`NETMASK`). DX and DX2 only.

        For MX: [`set_netmask_1`][cobrite.CoBrite.set_netmask_1]/
        [`set_netmask_2`][cobrite.CoBrite.set_netmask_2].

        Requires level 1.

        Args:
            mask: Dotted-decimal netmask string.
        """
        self.write(f"NETMASK {mask}")

    @requires_level(1)
    def set_netmask_1(self, mask: str) -> None:
        """Set the front-panel Ethernet netmask (`NETMASK1`). MX only.

        For DX/DX2: [`set_netmask`][cobrite.CoBrite.set_netmask].

        Requires level 1.

        Args:
            mask: Dotted-decimal netmask string.
        """
        self.write(f"NETMASK1 {mask}")

    @requires_level(1)
    def set_netmask_2(self, mask: str) -> None:
        """Set the rear-panel Ethernet netmask (`NETMASK2`). MX only.

        For DX/DX2: [`set_netmask`][cobrite.CoBrite.set_netmask].

        Requires level 1.

        Args:
            mask: Dotted-decimal netmask string.
        """
        self.write(f"NETMASK2 {mask}")

    @requires_level(1)
    def set_gateway_ip(self, ip: str) -> None:
        """Set the gateway IP address (`GATEWAYIP`). DX and DX2 only.

        For MX: [`set_gateway_ip_1`][cobrite.CoBrite.set_gateway_ip_1]/
        [`set_gateway_ip_2`][cobrite.CoBrite.set_gateway_ip_2].

        Requires level 1.

        Args:
            ip: Dotted-decimal gateway IP string.
        """
        self.write(f"GATEWAYIP {ip}")

    @requires_level(1)
    def set_gateway_ip_1(self, ip: str) -> None:
        """Set the front-panel gateway IP address (`GATEWAYIP1`). MX only.

        For DX/DX2: [`set_gateway_ip`][cobrite.CoBrite.set_gateway_ip].

        Requires level 1.

        Args:
            ip: Dotted-decimal gateway IP string.
        """
        self.write(f"GATEWAYIP1 {ip}")

    @requires_level(1)
    def set_gateway_ip_2(self, ip: str) -> None:
        """Set the rear-panel gateway IP address (`GATEWAYIP2`). MX only.

        For DX/DX2: [`set_gateway_ip`][cobrite.CoBrite.set_gateway_ip].

        Requires level 1.

        Args:
            ip: Dotted-decimal gateway IP string.
        """
        self.write(f"GATEWAYIP2 {ip}")

    @requires_level(1)
    def set_dns_ip(self, ip: str) -> None:
        """Set the primary DNS server IP address (`DNSIP`).

        Requires level 1.

        Args:
            ip: Dotted-decimal DNS server IP string.
        """
        self.write(f"DNSIP {ip}")

    @requires_level(1)
    def set_lockout(self, enable: bool) -> None:
        """Lock or unlock other sessions from performing write commands (`LOCK`).

        Requires level 1.

        Args:
            enable: `True` to acquire the write lock, `False` to release it.
        """
        self.write(f"LOCK {int(enable)}")

    @requires_level(1)
    def set_start_default(self, enable: bool) -> None:
        """Control whether the unit applies factory defaults on the next boot (`STADEF`).

        Requires level 1.

        Args:
            enable: `True` = apply factory defaults on start.
        """
        self.write(f"STADEF {int(enable)}")

    @requires_level(1)
    def set_enable_autostart(self, enable: bool) -> None:
        """Enable or disable preservation of laser on/off state across reboots (`ENABAUTOSTA`).

        Requires level 1.

        Args:
            enable: `True` to enable autostart.
        """
        self.write(f"ENABAUTOSTA {int(enable)}")

    @requires_level(1)
    def set_trigger_delay(self, ms: int) -> None:
        """Set the hardware trigger delay in milliseconds (`TRIDEL`).

        Requires level 1.

        Args:
            ms: Trigger delay in milliseconds.
        """
        self.write(f"TRIDEL {ms}")

    @requires_level(1)
    def set_trigger_polarity(self, in_out: str, polarity: int) -> None:
        """Set the hardware trigger polarity (`TRIPOL`).

        Requires level 1.

        Args:
            in_out: Direction — `"IN"` or `"OUT"`.
            polarity: Edge polarity — `0` (falling) or `1` (rising).
        """
        self.write(f"TRIPOL {in_out},{polarity}")

    @requires_level(1)
    def set_password(self, password: str) -> None:
        """Change the password for the current user level (`SPASS`).

        Requires level 1.

        Args:
            password: New password string.
        """
        self.write(f"SPASS {password}")

    # -------------------------------------------------------------------------
    # Level-1 port commands
    # -------------------------------------------------------------------------

    @requires_level(1)
    def set_trigger_out_active(
        self,
        active: bool,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Enable or disable a port's contribution to the hardware trigger output (`TRIOUTACT`).

        Property equivalent: [`trigger_out_active`][cobrite.CoBrite.trigger_out_active].
        Requires level 1.

        Args:
            active: `True` to include this port in the trigger output.
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until operation completes.
        """
        self._cmd_csd("TRIOUTACT {csd},{v}", chassis, slot, device, v=int(active), wait=wait)

    @requires_level(1)
    def set_trigger_config(
        self,
        frequency: float,
        offset: float,
        power: float,
        state: bool,
        dither: int,
        chassis: int = 0,
        slot: int = 0,
        device: int = 0,
        wait: bool = True,
    ) -> None:
        """Buffer a configuration to apply when the hardware trigger input fires (`TRICONF`).

        Property equivalent: [`trigger_config`][cobrite.CoBrite.trigger_config].
        Parameters have the same meaning as [`set_config`][cobrite.CoBrite.set_config].
        Requires level 1.

        Args:
            frequency: Target frequency in THz.
            offset: Frequency offset in GHz.
            power: Target output power in dBm.
            state: `True` to enable the laser on trigger.
            dither: Dither state (`1` = on, `0` = off, `-1` = not supported).
            chassis: Chassis number, or `0` for all.
            slot: Slot number, or `0` for all.
            device: Device number, or `0` for all.
            wait: When `True`, poll `BUSY?` until operation completes.
        """
        self._cmd_csd(
            "TRICONF {csd},{frequency},{offset},{power},{state},{dither}",
            chassis,
            slot,
            device,
            frequency=frequency,
            offset=offset,
            power=power,
            state=int(state),
            dither=dither,
            wait=wait,
        )

    # -------------------------------------------------------------------------
    # Properties (act on active port set via set_active_port())
    # -------------------------------------------------------------------------

    @property
    def wavelength(self) -> float:
        """Target wavelength of the active port in nm.

        CSD equivalents: [`get_wavelength`][cobrite.CoBrite.get_wavelength] /
        [`set_wavelength`][cobrite.CoBrite.set_wavelength].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_wavelength(c, s, d)[0][-1]

    @wavelength.setter
    def wavelength(self, value: float) -> None:
        c, s, d = self._require_active_port()
        self.set_wavelength(value, c, s, d)

    @property
    def wavelength_limits(self) -> dict[str, float]:
        """Wavelength range of the active port as `{"min": float, "max": float}` in nm.

        CSD equivalent: [`get_wavelength_limits`][cobrite.CoBrite.get_wavelength_limits].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_wavelength_limits(c, s, d)[0][-1]

    @property
    def frequency(self) -> float:
        """Target frequency of the active port in THz.

        CSD equivalents: [`get_frequency`][cobrite.CoBrite.get_frequency] /
        [`set_frequency`][cobrite.CoBrite.set_frequency].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_frequency(c, s, d)[0][-1]

    @frequency.setter
    def frequency(self, value: float) -> None:
        c, s, d = self._require_active_port()
        self.set_frequency(value, c, s, d)

    @property
    def frequency_limits(self) -> dict[str, float]:
        """Frequency range of the active port as `{"min": float, "max": float}` in THz.

        CSD equivalent: [`get_frequency_limits`][cobrite.CoBrite.get_frequency_limits].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_frequency_limits(c, s, d)[0][-1]

    @property
    def power(self) -> float:
        """Target output power of the active port in dBm.

        CSD equivalents: [`get_power`][cobrite.CoBrite.get_power] /
        [`set_power`][cobrite.CoBrite.set_power].
        For the actual measured power use [`actual_power`][cobrite.CoBrite.actual_power].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_power(c, s, d)[0][-1]

    @power.setter
    def power(self, value: float) -> None:
        c, s, d = self._require_active_port()
        self.set_power(value, c, s, d)

    @property
    def actual_power(self) -> float:
        """Actual measured output power of the active port in dBm.

        CSD equivalent: [`get_actual_power`][cobrite.CoBrite.get_actual_power].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_actual_power(c, s, d)[0][-1]

    @property
    def power_limits(self) -> dict[str, float]:
        """Power range of the active port as `{"min": float, "max": float}` in dBm.

        CSD equivalent: [`get_power_limits`][cobrite.CoBrite.get_power_limits].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_power_limits(c, s, d)[0][-1]

    @property
    def offset(self) -> float:
        """Frequency offset of the active port in GHz.

        CSD equivalents: [`get_offset`][cobrite.CoBrite.get_offset] /
        [`set_offset`][cobrite.CoBrite.set_offset].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_offset(c, s, d)[0][-1]

    @offset.setter
    def offset(self, value: float) -> None:
        c, s, d = self._require_active_port()
        self.set_offset(value, c, s, d)

    @property
    def offset_limits(self) -> float:
        """Symmetric offset limit of the active port in GHz.

        CSD equivalent: [`get_offset_limits`][cobrite.CoBrite.get_offset_limits].
        The allowed range is `[-offset_limits, +offset_limits]`.

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_offset_limits(c, s, d)[0][-1]

    @property
    def limits(self) -> dict[str, float]:
        """All tuning limits of the active port.

        CSD equivalent: [`get_limits`][cobrite.CoBrite.get_limits].
        Returns a dict with keys `freq_min`, `freq_max` (THz),
        `offset_range` (GHz), `pow_min`, `pow_max` (dBm).

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_limits(c, s, d)[0][-1]

    @property
    def state(self) -> bool:
        """Enable state of the active port.

        CSD equivalents: [`get_state`][cobrite.CoBrite.get_state] /
        [`set_state`][cobrite.CoBrite.set_state].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_state(c, s, d)[0][-1]

    @state.setter
    def state(self, value: bool) -> None:
        c, s, d = self._require_active_port()
        self.set_state(value, c, s, d)

    @property
    def dither(self) -> bool:
        """Dither enable state of the active port.

        CSD equivalents: [`get_dither`][cobrite.CoBrite.get_dither] /
        [`set_dither`][cobrite.CoBrite.set_dither].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_dither(c, s, d)[0][-1]

    @dither.setter
    def dither(self, value: bool) -> None:
        c, s, d = self._require_active_port()
        self.set_dither(value, c, s, d)

    @property
    def laser_alarm(self) -> int:
        """Laser alarm code of the active port.  `0` = no alarm.

        CSD equivalent: [`get_laser_alarm`][cobrite.CoBrite.get_laser_alarm].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_laser_alarm(c, s, d)[0][-1]

    @property
    def laser_config(self) -> dict[str, float | bool | int]:
        """Full laser configuration of the active port.

        CSD equivalents: [`get_config`][cobrite.CoBrite.get_config] /
        [`set_config`][cobrite.CoBrite.set_config].
        Keys: `frequency` (THz), `offset` (GHz), `power` (dBm),
        `state` (bool), `busy` (bool), `dither` (int).

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_config(c, s, d)[0][-1]

    @laser_config.setter
    def laser_config(self, value: dict[str, float | bool | int]) -> None:
        c, s, d = self._require_active_port()
        self.set_config(
            float(value["frequency"]),
            float(value["offset"]),
            float(value["power"]),
            bool(value["state"]),
            int(value["dither"]),
            c,
            s,
            d,
        )

    @property
    def monitor(self) -> dict[str, float]:
        """Thermal and current monitor readings of the active port.

        CSD equivalent: [`get_monitor`][cobrite.CoBrite.get_monitor].
        Keys: `ld_chip_temp` (°C), `base_temp` (°C), `ld_current_ma`,
        `tec_current_ma`.

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_monitor(c, s, d)[0][-1]

    @property
    def trigger_out_active(self) -> bool:
        """Whether the active port contributes to the hardware trigger output.

        CSD equivalents: [`get_trigger_out_active`][cobrite.CoBrite.get_trigger_out_active] /
        [`set_trigger_out_active`][cobrite.CoBrite.set_trigger_out_active].

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_trigger_out_active(c, s, d)[0][-1]

    @trigger_out_active.setter
    def trigger_out_active(self, value: bool) -> None:
        c, s, d = self._require_active_port()
        self.set_trigger_out_active(value, c, s, d)

    @property
    def trigger_config(self) -> dict[str, float | bool | int]:
        """Buffered trigger configuration of the active port.

        CSD equivalents: [`get_trigger_config`][cobrite.CoBrite.get_trigger_config] /
        [`set_trigger_config`][cobrite.CoBrite.set_trigger_config].
        Same keys as [`laser_config`][cobrite.CoBrite.laser_config].  Applied when the hardware
        trigger input fires.

        Raises:
            RuntimeError: If no active port has been set.
        """
        c, s, d = self._require_active_port()
        return self.get_trigger_config(c, s, d)[0][-1]

    @trigger_config.setter
    def trigger_config(self, value: dict[str, float | bool | int]) -> None:
        c, s, d = self._require_active_port()
        self.set_trigger_config(
            float(value["frequency"]),
            float(value["offset"]),
            float(value["power"]),
            bool(value["state"]),
            int(value["dither"]),
            c,
            s,
            d,
        )


def main() -> None:  # pragma: no cover
    cobrite = CoBrite(open=True)
    print(cobrite.idn())
    print(cobrite.full_info())
    cobrite.close()


if __name__ == "__main__":  # pragma: no cover
    main()
