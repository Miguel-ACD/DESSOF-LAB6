import json
import os
import urllib.error
import urllib.request
from itertools import combinations

import pytest


LIVE = os.environ.get("CAMPO_P2P_LIVE") == "1"
BASE_URL = os.environ.get("CAMPO_P2P_URL", "http://127.0.0.1:8080")
PEERS = [f"peer-{letter}" for letter in "abcdef"]

pytestmark = pytest.mark.skipif(
    not LIVE, reason="Set CAMPO_P2P_LIVE=1 to test the running Docker system"
)


def request(method: str, path: str, body=None):
    return request_url(method, BASE_URL + path, body)


def request_url(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    call = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(call, timeout=25) as response:
        return response.status, json.load(response)


@pytest.fixture(scope="session", autouse=True)
def restore_default_settings():
    yield
    for index, peer_id in enumerate(PEERS):
        request(
            "POST",
            f"/api/peers/{peer_id}/active",
            {"active": index < 4},
        )
    request(
        "PUT",
        "/api/settings",
        {
            "policy": "strict",
            "required_votes": 3,
            "ttl": 5,
            "threshold": 120,
        },
    )
    request("DELETE", "/api/gateway")
    for first, second in combinations([*PEERS, "gateway"], 2):
        request(
            "POST",
            "/api/links",
            {"first": first, "second": second, "enabled": True},
        )


def test_dashboard_health_endpoint():
    status, payload = request("GET", "/health")
    assert status == 200
    assert payload == {"status": "ok", "service": "dashboard"}


def test_system_reports_six_peer_containers():
    status, payload = request("GET", "/api/system")
    assert status == 200
    assert [peer["peer_id"] for peer in payload["peers"]] == PEERS
    assert all(not peer.get("unreachable", False) for peer in payload["peers"])


@pytest.mark.parametrize("required", range(7))
def test_live_api_accepts_every_k_between_zero_and_six(required: int):
    status, payload = request(
        "PUT",
        "/api/settings",
        {
            "policy": "custom",
            "required_votes": required,
            "ttl": 5,
            "threshold": 120,
        },
    )
    assert status == 200
    assert payload["required_votes"] == required


LIVE_FALSE_POSITIVE_CASES = [
    (selected_count, required, "confirmed" if selected_count >= required else "rejected")
    for selected_count in range(1, 5)
    for required in range(5)
]


@pytest.mark.parametrize(
    ("selected_count", "required", "expected"), LIVE_FALSE_POSITIVE_CASES
)
def test_multiple_false_positives_respect_k_over_n(
    selected_count: int, required: int, expected: str
):
    request(
        "PUT",
        "/api/settings",
        {
            "policy": "custom",
            "required_votes": required,
            "ttl": 5,
            "threshold": 120,
        },
    )
    status, payload = request(
        "POST",
        "/api/events/false-positive",
        {"peer_ids": PEERS[:selected_count]},
    )
    result = payload["result"]
    assert status == 200
    assert result["observed"] == selected_count
    assert result["required"] == required
    assert result["status"] == expected


def test_false_positive_request_requires_a_selection():
    with pytest.raises(urllib.error.HTTPError) as error:
        request("POST", "/api/events/false-positive", {"peer_ids": []})
    assert error.value.code == 400


def test_false_positive_request_rejects_inactive_peer():
    with pytest.raises(urllib.error.HTTPError) as error:
        request("POST", "/api/events/false-positive", {"peer_ids": ["peer-e"]})
    assert error.value.code == 409


def test_activation_returns_real_discovery_trace_and_shutdown_is_silent():
    _, activated = request(
        "POST", "/api/peers/peer-e/active", {"active": True}
    )
    assert activated["active"] is True
    assert set(activated["discovered_peers"]) == set(PEERS[:4])

    _, deactivated = request(
        "POST", "/api/peers/peer-e/active", {"active": False}
    )
    assert deactivated["active"] is False
    assert set(deactivated["notified_peers"]) == set(PEERS[:4])

    _, before = request_url("GET", "http://127.0.0.1:8105/state")
    packet_count = len(before["packet_log"])
    _, ignored = request_url(
        "POST",
        "http://127.0.0.1:8105/network/status",
        {"peer_id": "peer-a", "active": True, "timestamp": 1.0},
    )
    _, after = request_url("GET", "http://127.0.0.1:8105/state")

    assert ignored["status"] == "peer_offline"
    assert len(after["packet_log"]) == packet_count
    assert not any(peer["online"] for peer in after["discovered_peers"])


def test_ttl_blocks_multihop_packets_and_reports_the_drop_point():
    for first, second in [
        ("peer-a", "peer-c"),
        ("peer-a", "peer-d"),
        ("peer-b", "peer-d"),
    ]:
        request(
            "POST",
            "/api/links",
            {"first": first, "second": second, "enabled": False},
        )

    request(
        "PUT",
        "/api/settings",
        {"policy": "strict", "required_votes": 3, "ttl": 1, "threshold": 120},
    )
    _, limited = request("POST", "/api/events/global")
    limited_result = limited["result"]

    assert limited_result["status"] == "inconclusive"
    assert limited_result["observed"] == 2
    assert {vote["peer_id"] for vote in limited_result["votes"] if vote["status"] == "dropped_ttl"} == {"peer-c", "peer-d"}
    assert limited_result["ttl_failures"]
    assert all(failure["dropped_at"] == "peer-b" for failure in limited_result["ttl_failures"])

    request("DELETE", "/api/gateway")
    request(
        "PUT",
        "/api/settings",
        {"policy": "strict", "required_votes": 3, "ttl": 3, "threshold": 120},
    )
    _, sufficient = request("POST", "/api/events/global")
    sufficient_result = sufficient["result"]
    _, gateway = request_url("GET", "http://127.0.0.1:8090/state")

    assert sufficient_result["status"] == "confirmed"
    assert sufficient_result["observed"] == 4
    assert sufficient_result["ttl_failures"] == []
    assert gateway["events"][0]["report_count"] == 4

