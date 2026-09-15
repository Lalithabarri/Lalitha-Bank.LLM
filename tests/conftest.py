import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from werkzeug.serving import BaseWSGIServer, make_server

from legacy_bank import create_app
from legacy_bank.faults import FaultMode

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


@pytest.fixture
def surface():
    """A headless PlaywrightSurface; skips with a clear reason if Chromium is not installed."""
    from cua.surface.playwright_surface import PlaywrightSurface

    surface = PlaywrightSurface(headless=True)
    try:
        surface.open()
    except Exception as exc:  # noqa: BLE001 - any launch failure means "no browser here"
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip(f"Chromium not installed: run `uv run playwright install chromium` ({exc})")
        raise
    try:
        yield surface
    finally:
        surface.close()
