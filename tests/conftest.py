import os

import pytest

from cobrite import CoBrite
from cobrite._testing import FakeTransport

# ── fake response sets ───────────────────────────────────────────────────

OPEN_1x1x1 = {
    "LAY?": "LAY,1,1,DEV1",
    "TYP? 1,1,1": "GC",
    "INTI": "OK",
}

OPEN_1x1x3 = {
    "LAY?": "LAY,1,1,DEV3",
    "TYP? 1,1,1": "GC",
    "TYP? 1,1,2": "GC",
    "TYP? 1,1,3": "GC",
    "INTI": "OK",
}

OPEN_MX = {
    "LAY?": (
        "SYSTEM CBMA48SL,EMP,EMP,EMP\n"
        "1,1,TLS4\n"
        "1,2,EMP\n"
        "1,3,TLS4\n"
        "1,4,TLS4\n"
        "1,5,EMP\n"
        "1,6,TLS1\n"
        "1,7,EMP\n"
        "1,8,TLS2\n"
        "1,9,EMP\n"
        "1,10,TLS2\n"
        "1,11,EMP\n"
        "1,12,EMP"
    ),
    "TYP? 1,1,1": "NC",
    "TYP? 1,1,2": "NC",
    "TYP? 1,1,3": "NC",
    "TYP? 1,1,4": "NC",
    "TYP? 1,3,1": "NC",
    "TYP? 1,3,2": "NC",
    "TYP? 1,3,3": "NC",
    "TYP? 1,3,4": "NC",
    "TYP? 1,4,1": "NC",
    "TYP? 1,4,2": "NC",
    "TYP? 1,4,3": "NC",
    "TYP? 1,4,4": "NC",
    "TYP? 1,6,1": "NC",
    "TYP? 1,8,1": "NC",
    "TYP? 1,8,2": "NC",
    "TYP? 1,10,1": "NC",
    "TYP? 1,10,2": "NC",
    "INTI": "OK",
}

BUSY_1x1x1 = {"BUSY? 1,1,1": "0"}
BUSY_1x1x3 = {"BUSY? 1,1,1": "0", "BUSY? 1,1,2": "0", "BUSY? 1,1,3": "0"}

AUTH = {"PASS IDP": "OK", "PASS?": "1", "PASS 0": "OK"}


# ── pytest option ────────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--cobrite-address",
        default=None,
        help="Real CoBrite device address. Enables integration tests.",
    )


@pytest.fixture(scope="session")
def cobrite_address(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("--cobrite-address") or os.environ.get("COBRITE_ADDRESS")


# ── helpers ──────────────────────────────────────────────────────────────


def make_cb(responses: dict[str, str]) -> CoBrite:
    """Open a CoBrite backed by FakeTransport."""
    cb = CoBrite(_transport=FakeTransport(responses))
    cb.open()
    return cb
