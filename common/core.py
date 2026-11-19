from __future__ import annotations

import heapq
from itertools import combinations


PEER_IDS = [f"peer-{letter}" for letter in "abcdef"]
GATEWAY_ID = "gateway"


def edge_key(first: str, second: str) -> str:
    return "|".join(sorted((first, second)))


def default_topology() -> dict[str, bool]:
    edges: dict[str, bool] = {}
    for first, second in combinations(PEER_IDS, 2):
        edges[edge_key(first, second)] = True
    for peer_id in PEER_IDS:
        edges[edge_key(peer_id, GATEWAY_ID)] = True
    return edges


def shortest_path(
    topology: dict[str, bool],
    start: str,
    destination: str,
    allowed_nodes: set[str],
) -> list[str] | None:
    if start not in allowed_nodes or destination not in allowed_nodes:
        return None

    graph: dict[str, list[str]] = {node: [] for node in allowed_nodes}
    for key, enabled in topology.items():
        if not enabled:
            continue
        first, second = key.split("|", 1)
        if first in allowed_nodes and second in allowed_nodes:
            graph[first].append(second)
            graph[second].append(first)

    queue: list[tuple[int, str, list[str]]] = [(0, start, [start])]
    visited: set[str] = set()
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node in visited:
            continue
        if node == destination:
            return path
        visited.add(node)
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                heapq.heappush(queue, (cost + 1, neighbor, [*path, neighbor]))
    return None


def required_confirmations(
    active_count: int, policy: str, configured_required: int | None = None
) -> int:
    if active_count <= 0:
        return 0
    if policy == "strict":
        return active_count
    if configured_required is None:
        return max(1, active_count - 1)
    return min(active_count, max(0, configured_required))


def evaluate_confirmation(
    observed_count: int,
    negative_count: int,
    missing_count: int,
    active_count: int,
    policy: str,
    configured_required: int | None = None,
) -> str:
    required = required_confirmations(active_count, policy, configured_required)
    if observed_count >= required:
        return "confirmed"
    if missing_count and observed_count + missing_count >= required and not negative_count:
        return "inconclusive"
    return "rejected"
