from common.core import (
    GATEWAY_ID,
    PEER_IDS,
    default_topology,
    edge_key,
    evaluate_confirmation,
    shortest_path,
)


def test_full_mesh_has_expected_edge_count():
    topology = default_topology()
    assert len(topology) == 21
    assert topology[edge_key("peer-a", "peer-f")]
    assert topology[edge_key("peer-a", GATEWAY_ID)]


def test_dijkstra_uses_peer_when_gateway_link_is_cut():
    topology = default_topology()
    topology[edge_key("peer-a", GATEWAY_ID)] = False
    path = shortest_path(
        topology,
        "peer-a",
        GATEWAY_ID,
        {"peer-a", "peer-b", GATEWAY_ID},
    )
    assert path == ["peer-a", "peer-b", GATEWAY_ID]


def test_strict_and_tolerant_confirmation():
    assert evaluate_confirmation(4, 0, 0, 4, "strict") == "confirmed"
    assert evaluate_confirmation(3, 1, 0, 4, "strict") == "rejected"
    assert evaluate_confirmation(3, 1, 0, 4, "tolerant") == "confirmed"
    assert evaluate_confirmation(1, 3, 0, 4, "tolerant") == "rejected"


def test_configurable_confirmation_accepts_zero_to_n():
    assert evaluate_confirmation(0, 4, 0, 4, "custom", 0) == "confirmed"
    assert evaluate_confirmation(2, 2, 0, 4, "custom", 2) == "confirmed"
    assert evaluate_confirmation(2, 2, 0, 4, "custom", 3) == "rejected"
    assert evaluate_confirmation(4, 0, 0, 4, "custom", 6) == "confirmed"


def test_missing_vote_can_be_inconclusive():
    assert evaluate_confirmation(3, 0, 1, 4, "strict") == "inconclusive"


def test_peer_names_are_stable():
    assert PEER_IDS == ["peer-a", "peer-b", "peer-c", "peer-d", "peer-e", "peer-f"]
