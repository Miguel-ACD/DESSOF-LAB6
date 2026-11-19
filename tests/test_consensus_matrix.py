from itertools import permutations

import pytest

from common.core import (
    GATEWAY_ID,
    PEER_IDS,
    default_topology,
    edge_key,
    evaluate_confirmation,
    required_confirmations,
    shortest_path,
)


def vote_partitions(max_peers: int = 6):
    return [
        (active, observed, negative, active - observed - negative)
        for active in range(max_peers + 1)
        for observed in range(active + 1)
        for negative in range(active - observed + 1)
    ]


VOTE_PARTITIONS = vote_partitions()
CUSTOM_CASES = [
    (*partition, required)
    for partition in VOTE_PARTITIONS
    for required in range(7)
]


def expected_status(observed: int, negative: int, missing: int, required: int) -> str:
    if observed >= required:
        return "confirmed"
    if missing > 0 and negative == 0 and observed + missing >= required:
        return "inconclusive"
    return "rejected"


@pytest.mark.parametrize(
    ("active", "observed", "negative", "missing"), VOTE_PARTITIONS
)
def test_strict_consensus_for_every_vote_partition(
    active: int, observed: int, negative: int, missing: int
):
    expected = expected_status(observed, negative, missing, active)
    assert evaluate_confirmation(
        observed, negative, missing, active, "strict"
    ) == expected


@pytest.mark.parametrize(
    ("active", "observed", "negative", "missing", "configured"), CUSTOM_CASES
)
def test_custom_consensus_for_every_vote_partition_and_threshold(
    active: int,
    observed: int,
    negative: int,
    missing: int,
    configured: int,
):
    effective_required = min(active, configured)
    expected = expected_status(observed, negative, missing, effective_required)
    assert evaluate_confirmation(
        observed, negative, missing, active, "custom", configured
    ) == expected


@pytest.mark.parametrize("active", range(7))
@pytest.mark.parametrize("configured", range(-2, 9))
def test_configured_threshold_is_always_clamped_to_zero_and_n(
    active: int, configured: int
):
    expected = 0 if active == 0 else min(active, max(0, configured))
    assert required_confirmations(active, "custom", configured) == expected


@pytest.mark.parametrize("active", range(7))
def test_strict_threshold_always_equals_n(active: int):
    assert required_confirmations(active, "strict", 0) == active
    assert required_confirmations(active, "strict", 6) == active


ALL_NODES = [*PEER_IDS, GATEWAY_ID]
ORDERED_NODE_PAIRS = list(permutations(ALL_NODES, 2))


@pytest.mark.parametrize(("first", "second"), ORDERED_NODE_PAIRS)
def test_edge_keys_are_symmetric_and_stable(first: str, second: str):
    key = edge_key(first, second)
    assert key == edge_key(second, first)
    assert key.split("|") == sorted((first, second))


@pytest.mark.parametrize("peer_id", PEER_IDS)
def test_every_peer_has_a_direct_gateway_route_in_the_default_mesh(peer_id: str):
    topology = default_topology()
    assert shortest_path(topology, peer_id, GATEWAY_ID, set(ALL_NODES)) == [
        peer_id,
        GATEWAY_ID,
    ]


@pytest.mark.parametrize("peer_id", PEER_IDS)
@pytest.mark.parametrize("relay", PEER_IDS)
def test_each_peer_can_reroute_through_an_available_relay(peer_id: str, relay: str):
    if peer_id == relay:
        pytest.skip("A peer cannot relay through itself")
    topology = default_topology()
    topology[edge_key(peer_id, GATEWAY_ID)] = False
    allowed = {peer_id, relay, GATEWAY_ID}
    assert shortest_path(topology, peer_id, GATEWAY_ID, allowed) == [
        peer_id,
        relay,
        GATEWAY_ID,
    ]


@pytest.mark.parametrize("peer_id", PEER_IDS)
def test_an_isolated_peer_has_no_gateway_route(peer_id: str):
    topology = default_topology()
    for other in ALL_NODES:
        if other != peer_id:
            topology[edge_key(peer_id, other)] = False
    assert shortest_path(topology, peer_id, GATEWAY_ID, set(ALL_NODES)) is None


@pytest.mark.parametrize("peer_id", PEER_IDS)
def test_inactive_start_or_destination_cannot_route(peer_id: str):
    topology = default_topology()
    assert shortest_path(topology, peer_id, GATEWAY_ID, {GATEWAY_ID}) is None
    assert shortest_path(topology, peer_id, GATEWAY_ID, {peer_id}) is None

