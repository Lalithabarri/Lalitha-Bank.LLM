import os
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from werkzeug.serving import BaseWSGIServer, make_server

from legacy_bank import create_app
from legacy_bank.faults import FaultMode

# --- Milestone 6: the normal suite makes zero network calls -------------------------------------

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class NetworkAccessRefused(RuntimeError):
    pass


@pytest.fixture(autouse=True, scope="session")
def _no_external_network():
    """Every socket connection from the test process must stay on loopback (the in-process
    Legacy Bank). A provider call from a test — even by mistake — fails loudly here. The live
    E01 proof opts out explicitly with ``CUA_LIVE_API=1``."""
    if os.environ.get("CUA_LIVE_API") == "1":
        yield
        return
    original = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if isinstance(host, str) and host not in _LOOPBACK:
            raise NetworkAccessRefused(f"test tried to reach {host!r}; the suite is offline")
        return original(self, address)

    socket.socket.connect = guarded  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]


# --- Milestone 1: Flask test client fixtures ---------------------------------------------------


@pytest.fixture
def bank_app():
    return create_app(fault_mode=None)


@pytest.fixture
def bank_app_ambiguous():
    return create_app(fault_mode=FaultMode.AMBIGUOUS_SAVINGS)


@pytest.fixture
def client(bank_app):
    return bank_app.test_client()


@pytest.fixture
def client_ambiguous(bank_app_ambiguous):
    return bank_app_ambiguous.test_client()


# --- Milestone 2: live server + real browser fixtures ------------------------------------------


@dataclass
class LiveBank:
    base_url: str
    fault_mode: FaultMode | None
    _server: BaseWSGIServer

    def url(self, path: str) -> str:
        return self.base_url + path


def _serve(fault_mode: FaultMode | None) -> Iterator[LiveBank]:
    server = make_server("127.0.0.1", 0, create_app(fault_mode=fault_mode), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LiveBank(f"http://127.0.0.1:{server.server_port}", fault_mode, server)
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def live_bank() -> Iterator[LiveBank]:
    yield from _serve(None)


@pytest.fixture(scope="module")
def live_bank_ambiguous() -> Iterator[LiveBank]:
    yield from _serve(FaultMode.AMBIGUOUS_SAVINGS)


def _open_surface(**kwargs):
    from cua.surface.playwright_surface import PlaywrightSurface

    surface = PlaywrightSurface(headless=True, **kwargs)
    try:
        surface.open()
    except Exception as exc:  # noqa: BLE001 - any launch failure means "no browser here"
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip(f"Chromium not installed: run `uv run playwright install chromium` ({exc})")
        raise
    return surface


@pytest.fixture
def surface():
    """A headless PlaywrightSurface; skips with a clear reason if Chromium is not installed.

    Adapter-level: no dispatch listener. Replay tests use ``recorded_surface`` instead.
    """
    surface = _open_surface()
    try:
        yield surface
    finally:
        surface.close()


# --- Milestone 5: evidence composition for live replays -----------------------------------------


@pytest.fixture
def evidence_store(tmp_path):
    from cua.evidence import EvidenceStore

    return EvidenceStore(tmp_path / "evidence")


@pytest.fixture
def evidence_recorder(evidence_store):
    """A recorder that writes real JSONL under ``tmp_path`` (the production sink)."""
    from cua.evidence import EvidenceRecorder

    return EvidenceRecorder(evidence_store.open_run)


@pytest.fixture
def recorded_surface(evidence_recorder):
    """A headless PlaywrightSurface wired to ``evidence_recorder`` — the production wiring."""
    surface = _open_surface(listener=evidence_recorder)
    try:
        yield surface
    finally:
        surface.close()
