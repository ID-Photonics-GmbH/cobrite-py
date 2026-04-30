"""Testing utilities for cobrite.  Not part of the public API."""


class FakeTransport:
    """Dict-backed Transport for use in unit tests.

    Pass a mapping of SCPI command strings to response strings.  Any command
    not in the mapping raises `RuntimeError`, making unexpected queries visible
    immediately rather than hanging.

    Example:
        ```python
        from cobrite import CoBrite
        from cobrite._testing import FakeTransport

        transport = FakeTransport({
            "INTI": "OK",
            "LAY?": "LAY,1,1,DEV3",
            "TYP? 1,1,1": "GC",
            "TYP? 1,1,2": "GC",
            "TYP? 1,1,3": "GC",
            "WAV? 1,1,1": "1550.0",
        })
        cb = CoBrite(_transport=transport)
        cb.open()
        assert cb.get_wavelength(1, 1, 1)[0][-1] == 1550.0
        ```
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses: dict[str, str] = responses

    def query(self, command: str) -> str:
        try:
            return self._responses[command]
        except KeyError as e:
            raise RuntimeError(f"FakeTransport: unexpected command {command!r}") from e

    def close(self) -> None:
        pass
