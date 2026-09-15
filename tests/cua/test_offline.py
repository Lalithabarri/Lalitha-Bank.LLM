"""The normal suite is offline: the session guard in ``tests/conftest.py`` refuses any socket
connection that leaves loopback, so no test can reach a provider by accident."""

import socket

import pytest

from tests.conftest import NetworkAccessRefused


def test_a_connection_outside_loopback_is_refused_before_any_packet():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        with pytest.raises(NetworkAccessRefused, match="offline"):
            sock.connect(("10.255.255.1", 9))  # a literal address: no DNS lookup either


def test_loopback_connections_still_work():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(("127.0.0.1", port))
