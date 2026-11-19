import asyncio

import pytest

pytest.importorskip("httpx", reason="Peer unit tests run inside the Docker image")

from peer.app import PeerRuntime, PeerStatusRequest, peer_status, runtime


def test_inactive_runtime_cannot_append_packet_activity():
    isolated = PeerRuntime()
    isolated.active = False
    isolated.log_packet("DISCOVERED", "HELLO", "message-1", "peer-b")
    assert list(isolated.packet_log) == []


def test_active_runtime_can_append_packet_activity():
    isolated = PeerRuntime()
    isolated.active = True
    isolated.log_packet("DISCOVERED", "HELLO", "message-1", "peer-b")
    assert isolated.packet_log[0]["action"] == "DISCOVERED"


def test_inactive_peer_ignores_membership_updates():
    original_active = runtime.active
    original_registry = dict(runtime.registry)
    original_log = list(runtime.packet_log)
    try:
        runtime.active = False
        runtime.registry.clear()
        runtime.packet_log.clear()
        response = asyncio.run(
            peer_status(
                PeerStatusRequest(
                    peer_id="peer-b", active=True, timestamp=1.0
                )
            )
        )
        assert response["status"] == "peer_offline"
        assert runtime.registry == {}
        assert list(runtime.packet_log) == []
    finally:
        runtime.active = original_active
        runtime.registry.clear()
        runtime.registry.update(original_registry)
        runtime.packet_log.clear()
        runtime.packet_log.extend(original_log)

