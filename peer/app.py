from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from common.core import (
    GATEWAY_ID,
    PEER_IDS,
    default_topology,
    edge_key,
    evaluate_confirmation,
    required_confirmations,
    shortest_path,
)


RETENTION_SECONDS = 300
MEASUREMENT_INTERVAL = 2
HEARTBEAT_INTERVAL = 2
OFFLINE_AFTER = 8
CORRELATION_WINDOW = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InjectRequest(BaseModel):
    event_id: str
    event_timestamp: float
    value: float | None = None


class ValidationRequest(BaseModel):
    event_id: str
    event_timestamp: float
    policy: str = Field(pattern="^(strict|tolerant|custom)$")
    required_votes: int | None = Field(default=None, ge=0, le=6)
    ttl: int = Field(ge=1, le=20)


class ActiveRequest(BaseModel):
    active: bool


class TopologyRequest(BaseModel):
    edges: dict[str, bool]


class ThresholdRequest(BaseModel):
    threshold: float = Field(ge=1, le=1000)


class HelloRequest(BaseModel):
    peer_id: str
    timestamp: float


class PeerStatusRequest(BaseModel):
    peer_id: str
    active: bool
    timestamp: float


class Envelope(BaseModel):
    message_id: str
    packet_type: str
    origin: str
    destination: str
    route: list[str]
    hop_index: int
    ttl: int
    payload: dict[str, Any]


class PeerRuntime:
    def __init__(self) -> None:
        self.peer_id = os.getenv("PEER_ID", "peer-a")
        self.zone = os.getenv("PEER_ZONE", self.peer_id.replace("peer-", "Zona ").title())
        self.active = os.getenv("INITIAL_ACTIVE", "false").lower() == "true"
        self.threshold = float(os.getenv("DETECTION_THRESHOLD", "120"))
        self.topology = default_topology()
        self.readings: deque[dict[str, Any]] = deque(maxlen=1000)
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.packet_log: deque[dict[str, Any]] = deque(maxlen=500)
        self.registry: dict[str, dict[str, Any]] = {}
        self.seen_messages: dict[str, float] = {}
        self.client: httpx.AsyncClient | None = None
        self.tasks: list[asyncio.Task] = []

    def log_packet(
        self,
        action: str,
        packet_type: str,
        message_id: str,
        peer: str,
        route: list[str] | None = None,
        detail: str = "",
    ) -> None:
        # An inactive peer is completely silent. Historical packets remain
        # readable, but no background task or inbound request may add new ones.
        if not self.active:
            return
        self.packet_log.appendleft(
            {
                "timestamp": utc_now(),
                "action": action,
                "packet_type": packet_type,
                "message_id": message_id,
                "peer": peer,
                "route": route or [],
                "detail": detail,
            }
        )

    def current_active_peers(self) -> list[str]:
        now = time.time()
        peers = [self.peer_id] if self.active else []
        for peer_id, record in self.registry.items():
            if record.get("active") and now - record.get("last_seen", 0) <= OFFLINE_AFTER:
                peers.append(peer_id)
        return sorted(set(peers))

    def allowed_nodes(self, include_gateway: bool = False) -> set[str]:
        nodes = set(self.current_active_peers())
        if include_gateway:
            nodes.add(GATEWAY_ID)
        return nodes

    def find_observation(self, event_id: str, event_timestamp: float) -> dict[str, Any] | None:
        for reading in reversed(self.readings):
            if reading.get("event_id") == event_id and reading["value"] >= self.threshold:
                return reading
        # An isolated false-positive test must never borrow a different recent event.
        if event_id.startswith("FP-"):
            return None
        for reading in reversed(self.readings):
            if (
                reading["value"] >= self.threshold
                and abs(reading["unix_timestamp"] - event_timestamp) <= CORRELATION_WINDOW
            ):
                return reading
        return None


runtime = PeerRuntime()


async def post(url: str, payload: dict[str, Any], timeout: float = 4) -> httpx.Response:
    if runtime.client is None:
        raise RuntimeError("HTTP client is not ready")
    return await runtime.client.post(url, json=payload, timeout=timeout)


def peer_url(peer_id: str) -> str:
    return f"http://{peer_id}:8000"


async def measurement_loop() -> None:
    while True:
        if runtime.active:
            value = round(max(2, random.gauss(22, 4)), 2)
            runtime.readings.append(
                {
                    "peer_id": runtime.peer_id,
                    "zone": runtime.zone,
                    "value": value,
                    "unit": "CPM simulados",
                    "timestamp": utc_now(),
                    "unix_timestamp": time.time(),
                    "event_id": None,
                    "synthetic": True,
                }
            )
        await asyncio.sleep(MEASUREMENT_INTERVAL)


async def heartbeat_once() -> None:
    if not runtime.active:
        return
    for other in PEER_IDS:
        if other == runtime.peer_id or not runtime.topology.get(edge_key(runtime.peer_id, other), False):
            continue
        try:
            response = await post(
                f"{peer_url(other)}/network/hello",
                {"peer_id": runtime.peer_id, "timestamp": time.time()},
                timeout=1.5,
            )
            body = response.json()
            if response.is_success and body.get("active"):
                was_online = runtime.registry.get(other, {}).get("active", False)
                runtime.registry[other] = {
                    "active": True,
                    "last_seen": time.time(),
                    "zone": body.get("zone", other),
                }
                if not was_online:
                    runtime.log_packet("DISCOVERED", "HELLO", str(uuid.uuid4()), other)
                for discovered_id in body.get("known_peers", []):
                    if discovered_id == runtime.peer_id:
                        continue
                    previous = runtime.registry.get(discovered_id, {})
                    runtime.registry[discovered_id] = {
                        "active": True,
                        "last_seen": time.time(),
                        "zone": previous.get("zone", discovered_id),
                        "via": other,
                    }
        except Exception:
            pass


async def heartbeat_loop() -> None:
    while True:
        await heartbeat_once()
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def cleanup_loop() -> None:
    while True:
        cutoff = time.time() - RETENTION_SECONDS
        runtime.readings = deque(
            (item for item in runtime.readings if item["unix_timestamp"] >= cutoff), maxlen=1000
        )
        runtime.events = deque(
            (item for item in runtime.events if item["unix_timestamp"] >= cutoff), maxlen=200
        )
        runtime.seen_messages = {
            key: seen_at for key, seen_at in runtime.seen_messages.items() if seen_at >= cutoff
        }
        for peer_id, record in runtime.registry.items():
            if time.time() - record.get("last_seen", 0) > OFFLINE_AFTER:
                record["active"] = False
        await asyncio.sleep(5)


async def synchronize() -> None:
    await asyncio.sleep(0.5)
    if not runtime.active:
        return
    for other in runtime.current_active_peers():
        if other == runtime.peer_id:
            continue
        try:
            if runtime.client is None:
                return
            response = await runtime.client.get(f"{peer_url(other)}/sync/recent", timeout=2)
            response.raise_for_status()
            body = response.json()
            known_events = {item["event_id"] for item in runtime.events}
            added = 0
            for event in body.get("events", []):
                if event["event_id"] not in known_events:
                    runtime.events.appendleft(event)
                    known_events.add(event["event_id"])
                    added += 1
            if not runtime.active:
                return
            runtime.log_packet(
                "SYNCED", "SYNC", str(uuid.uuid4()), other, detail=f"{added} eventos recuperados"
            )
            return
        except Exception:
            continue


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.client = httpx.AsyncClient()
    runtime.tasks = [
        asyncio.create_task(measurement_loop()),
        asyncio.create_task(heartbeat_loop()),
        asyncio.create_task(cleanup_loop()),
    ]
    yield
    for task in runtime.tasks:
        task.cancel()
    await runtime.client.aclose()


app = FastAPI(title=f"CAMPO P2P · {runtime.peer_id}", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "peer_id": runtime.peer_id, "active": runtime.active}


@app.get("/state")
async def state() -> dict[str, Any]:
    now = time.time()
    discovered = [
        {
            "peer_id": peer_id,
            "zone": record.get("zone", peer_id),
            "online": record.get("active", False)
            and now - record.get("last_seen", 0) <= OFFLINE_AFTER,
            "last_seen_seconds": round(now - record.get("last_seen", now), 1),
        }
        for peer_id, record in sorted(runtime.registry.items())
    ]
    return {
        "peer_id": runtime.peer_id,
        "zone": runtime.zone,
        "active": runtime.active,
        "threshold": runtime.threshold,
        "current_reading": runtime.readings[-1] if runtime.readings else None,
        "readings": list(runtime.readings)[-30:],
        "events": list(runtime.events)[:30],
        "packet_log": list(runtime.packet_log)[:80],
        "discovered_peers": discovered,
    }


@app.post("/network/hello")
async def hello(request: HelloRequest) -> dict[str, Any]:
    if runtime.active and runtime.topology.get(edge_key(runtime.peer_id, request.peer_id), False):
        previous = runtime.registry.get(request.peer_id, {}).get("active", False)
        runtime.registry[request.peer_id] = {
            "active": True,
            "last_seen": time.time(),
            "zone": request.peer_id,
        }
        if not previous:
            runtime.log_packet("DISCOVERED", "HELLO", str(uuid.uuid4()), request.peer_id)
    return {
        "peer_id": runtime.peer_id,
        "zone": runtime.zone,
        "active": runtime.active,
        "known_peers": runtime.current_active_peers(),
    }


@app.post("/network/status")
async def peer_status(request: PeerStatusRequest) -> dict[str, Any]:
    if not runtime.active:
        return {"status": "peer_offline", "peer_id": runtime.peer_id}
    if request.peer_id == runtime.peer_id:
        return {"status": "ignored", "peer_id": runtime.peer_id}
    if not runtime.topology.get(edge_key(runtime.peer_id, request.peer_id), False):
        return {"status": "link_disabled", "peer_id": runtime.peer_id}
    previous = runtime.registry.get(request.peer_id, {})
    runtime.registry[request.peer_id] = {
        **previous,
        "active": request.active,
        "last_seen": time.time(),
        "zone": previous.get("zone", request.peer_id),
    }
    runtime.log_packet(
        "MEMBERSHIP",
        "PEER_STATUS",
        str(uuid.uuid4()),
        request.peer_id,
        detail="activo" if request.active else "desconectado",
    )
    return {"status": "updated", "peer_id": runtime.peer_id}


async def announce_status(active: bool) -> None:
    peers = [
        peer_id
        for peer_id in PEER_IDS
        if peer_id != runtime.peer_id
        and runtime.topology.get(edge_key(runtime.peer_id, peer_id), False)
    ]
    await asyncio.gather(
        *[
            post(
                f"{peer_url(peer_id)}/network/status",
                {"peer_id": runtime.peer_id, "active": active, "timestamp": time.time()},
                timeout=1.5,
            )
            for peer_id in peers
        ],
        return_exceptions=True,
    )


@app.post("/control/active")
async def set_active(request: ActiveRequest) -> dict[str, Any]:
    changed = runtime.active != request.active
    if not changed:
        return {
            "peer_id": runtime.peer_id,
            "active": runtime.active,
            "changed": False,
            "discovered_peers": [],
            "notified_peers": [],
        }

    known_neighbors = [
        peer_id for peer_id in runtime.current_active_peers() if peer_id != runtime.peer_id
    ]
    if request.active:
        # Start from an empty live registry. Discovery must be performed again
        # instead of reusing stale membership learned before shutdown.
        for record in runtime.registry.values():
            record["active"] = False
        runtime.active = True
        runtime.log_packet("ACTIVATED", "CONTROL", str(uuid.uuid4()), runtime.peer_id)
        await heartbeat_once()
        discovered_peers = [
            peer_id
            for peer_id in runtime.current_active_peers()
            if peer_id != runtime.peer_id
        ]
        await announce_status(True)
        asyncio.create_task(synchronize())
        return {
            "peer_id": runtime.peer_id,
            "active": True,
            "changed": True,
            "discovered_peers": discovered_peers,
            "notified_peers": discovered_peers,
        }

    # The final action of an active peer is announcing its departure. Once the
    # flag changes, it no longer logs, discovers, synchronizes or handles status.
    runtime.log_packet("DEACTIVATED", "CONTROL", str(uuid.uuid4()), runtime.peer_id)
    await announce_status(False)
    runtime.active = False
    for record in runtime.registry.values():
        record["active"] = False
    return {
        "peer_id": runtime.peer_id,
        "active": False,
        "changed": True,
        "discovered_peers": [],
        "notified_peers": known_neighbors,
    }


@app.post("/control/topology")
async def set_topology(request: TopologyRequest) -> dict[str, Any]:
    runtime.topology = request.edges
    return {"peer_id": runtime.peer_id, "edges": len(runtime.topology)}


@app.post("/control/threshold")
async def set_threshold(request: ThresholdRequest) -> dict[str, Any]:
    runtime.threshold = request.threshold
    return {"peer_id": runtime.peer_id, "threshold": runtime.threshold}


@app.post("/control/inject")
async def inject(request: InjectRequest) -> dict[str, Any]:
    if not runtime.active:
        raise HTTPException(status_code=409, detail="Peer is inactive")
    jitter = random.uniform(-0.35, 0.35)
    value = round(request.value or random.uniform(145, 185), 2)
    reading = {
        "peer_id": runtime.peer_id,
        "zone": runtime.zone,
        "value": value,
        "unit": "CPM simulados",
        "timestamp": datetime.fromtimestamp(
            request.event_timestamp + jitter, tz=timezone.utc
        ).isoformat(),
        "unix_timestamp": request.event_timestamp + jitter,
        "event_id": request.event_id,
        "synthetic": True,
    }
    runtime.readings.append(reading)
    runtime.log_packet("MEASURED", "DETECTION", request.event_id, runtime.peer_id)
    return reading


async def send_envelope(envelope: Envelope) -> dict[str, Any]:
    if len(envelope.route) < 2:
        return {"status": "delivered", "remaining_ttl": envelope.ttl}
    if envelope.ttl <= 0:
        runtime.log_packet(
            "DROPPED", envelope.packet_type, envelope.message_id, envelope.destination,
            envelope.route, "TTL agotado antes del primer salto"
        )
        return {
            "status": "dropped_ttl",
            "route": envelope.route,
            "traversed_route": [runtime.peer_id],
            "dropped_at": runtime.peer_id,
            "remaining_ttl": envelope.ttl,
            "required_hops": len(envelope.route) - 1,
        }
    next_hop = envelope.route[1]
    outbound = envelope.model_copy(update={"hop_index": 1, "ttl": envelope.ttl - 1})
    runtime.log_packet(
        "SENT", envelope.packet_type, envelope.message_id, next_hop, envelope.route
    )
    try:
        url = (
            "http://gateway:8000/network/report"
            if next_hop == GATEWAY_ID
            else f"{peer_url(next_hop)}/network/forward"
        )
        response = await post(url, outbound.model_dump(), timeout=5)
        return response.json()
    except Exception as exc:
        runtime.log_packet(
            "FAILED", envelope.packet_type, envelope.message_id, next_hop,
            envelope.route, str(exc)[:120]
        )
        return {"status": "failed", "detail": str(exc)[:120], "route": envelope.route}


async def send_to_peer(
    destination: str,
    packet_type: str,
    payload: dict[str, Any],
    ttl: int,
) -> dict[str, Any]:
    allowed = runtime.allowed_nodes()
    route = shortest_path(runtime.topology, runtime.peer_id, destination, allowed)
    if not route:
        return {"status": "no_route", "destination": destination}
    envelope = Envelope(
        message_id=str(uuid.uuid4()),
        packet_type=packet_type,
        origin=runtime.peer_id,
        destination=destination,
        route=route,
        hop_index=0,
        ttl=ttl,
        payload=payload,
    )
    return await send_envelope(envelope)


async def send_report(event: dict[str, Any], ttl: int) -> dict[str, Any]:
    reading = runtime.find_observation(event["event_id"], event["event_timestamp"])
    if not reading:
        return {"status": "not_observed"}
    allowed = runtime.allowed_nodes(include_gateway=True)
    route = shortest_path(runtime.topology, runtime.peer_id, GATEWAY_ID, allowed)
    if not route:
        runtime.log_packet(
            "FAILED", "REPORT", event["event_id"], GATEWAY_ID, detail="Sin ruta al gateway"
        )
        return {"status": "no_route"}
    envelope = Envelope(
        message_id=f"report:{event['event_id']}:{runtime.peer_id}",
        packet_type="REPORT",
        origin=runtime.peer_id,
        destination=GATEWAY_ID,
        route=route,
        hop_index=0,
        ttl=ttl,
        payload={
            "event_id": event["event_id"],
            "peer_id": runtime.peer_id,
            "zone": runtime.zone,
            "measurement": reading,
            "confirmation": event,
        },
    )
    return await send_envelope(envelope)


def store_outcome(payload: dict[str, Any]) -> None:
    event_id = payload["event_id"]
    if any(item["event_id"] == event_id for item in runtime.events):
        return
    runtime.events.appendleft(
        {
            **payload,
            "unix_timestamp": time.time(),
            "recorded_at": utc_now(),
        }
    )


@app.post("/network/forward")
async def forward(envelope: Envelope) -> dict[str, Any]:
    if not runtime.active:
        return {"status": "peer_offline", "peer_id": runtime.peer_id}
    if envelope.message_id in runtime.seen_messages:
        runtime.log_packet(
            "DUPLICATE", envelope.packet_type, envelope.message_id, envelope.origin, envelope.route
        )
        return {"status": "duplicate", "peer_id": runtime.peer_id}
    runtime.seen_messages[envelope.message_id] = time.time()
    runtime.log_packet(
        "RECEIVED", envelope.packet_type, envelope.message_id, envelope.origin, envelope.route
    )

    if envelope.hop_index >= len(envelope.route) or envelope.route[envelope.hop_index] != runtime.peer_id:
        return {"status": "invalid_route", "peer_id": runtime.peer_id}

    if runtime.peer_id == envelope.destination:
        if envelope.packet_type == "CANDIDATE_EVENT":
            observation = runtime.find_observation(
                envelope.payload["event_id"], envelope.payload["event_timestamp"]
            )
            return {
                "status": "delivered",
                "peer_id": runtime.peer_id,
                "observed": observation is not None,
                "reading": observation,
                "remaining_ttl": envelope.ttl,
                "route": envelope.route,
            }
        if envelope.packet_type == "EVENT_OUTCOME":
            store_outcome(envelope.payload)
            report_result = None
            if envelope.payload["status"] == "confirmed":
                report_result = await send_report(envelope.payload, envelope.payload["ttl"])
            return {
                "status": "delivered",
                "peer_id": runtime.peer_id,
                "route": envelope.route,
                "remaining_ttl": envelope.ttl,
                "report_result": report_result,
            }
        return {"status": "delivered", "peer_id": runtime.peer_id, "route": envelope.route}

    if envelope.ttl <= 0 or envelope.hop_index + 1 >= len(envelope.route):
        traversed_route = envelope.route[: envelope.hop_index + 1]
        runtime.log_packet(
            "DROPPED", envelope.packet_type, envelope.message_id, envelope.destination,
            envelope.route, "TTL agotado"
        )
        return {
            "status": "dropped_ttl",
            "peer_id": runtime.peer_id,
            "route": envelope.route,
            "traversed_route": traversed_route,
            "dropped_at": runtime.peer_id,
            "remaining_ttl": envelope.ttl,
            "required_hops": len(envelope.route) - 1,
        }

    next_hop = envelope.route[envelope.hop_index + 1]
    forwarded = envelope.model_copy(
        update={"hop_index": envelope.hop_index + 1, "ttl": envelope.ttl - 1}
    )
    runtime.log_packet(
        "FORWARDED", envelope.packet_type, envelope.message_id, next_hop, envelope.route
    )
    try:
        url = (
            "http://gateway:8000/network/report"
            if next_hop == GATEWAY_ID
            else f"{peer_url(next_hop)}/network/forward"
        )
        response = await post(url, forwarded.model_dump(), timeout=5)
        return response.json()
    except Exception as exc:
        return {"status": "failed", "peer_id": runtime.peer_id, "detail": str(exc)[:120]}


@app.post("/events/validate")
async def validate_event(request: ValidationRequest) -> dict[str, Any]:
    if not runtime.active:
        raise HTTPException(status_code=409, detail="Peer is inactive")

    active_peers = runtime.current_active_peers()
    local_observation = runtime.find_observation(request.event_id, request.event_timestamp)
    votes: list[dict[str, Any]] = [
        {
            "peer_id": runtime.peer_id,
            "observed": local_observation is not None,
            "status": "local",
            "route": [runtime.peer_id],
        }
    ]
    for peer_id in active_peers:
        if peer_id == runtime.peer_id:
            continue
        result = await send_to_peer(
            peer_id,
            "CANDIDATE_EVENT",
            {"event_id": request.event_id, "event_timestamp": request.event_timestamp},
            request.ttl,
        )
        votes.append(
            {
                "peer_id": peer_id,
                "observed": result.get("observed") if result.get("status") == "delivered" else None,
                "status": result.get("status"),
                "route": result.get("route", []),
                "traversed_route": result.get("traversed_route", result.get("route", [])),
                "dropped_at": result.get("dropped_at"),
                "remaining_ttl": result.get("remaining_ttl"),
            }
        )

    observed = sum(vote["observed"] is True for vote in votes)
    negative = sum(vote["observed"] is False for vote in votes)
    missing = sum(vote["observed"] is None for vote in votes)
    status = evaluate_confirmation(
        observed,
        negative,
        missing,
        len(active_peers),
        request.policy,
        request.required_votes,
    )
    outcome = {
        "event_id": request.event_id,
        "event_timestamp": request.event_timestamp,
        "status": status,
        "policy": request.policy,
        "required": required_confirmations(
            len(active_peers), request.policy, request.required_votes
        ),
        "active_peers": active_peers,
        "observed": observed,
        "negative": negative,
        "missing": missing,
        "votes": votes,
        "coordinator": runtime.peer_id,
        "ttl": request.ttl,
    }
    outcome_deliveries: list[dict[str, Any]] = []
    report_results: list[dict[str, Any]] = []
    for peer_id in active_peers:
        if peer_id == runtime.peer_id:
            continue
        delivery = await send_to_peer(peer_id, "EVENT_OUTCOME", outcome, request.ttl)
        outcome_deliveries.append({"target_peer_id": peer_id, **delivery})
        if delivery.get("report_result") is not None:
            report_results.append({"source_peer_id": peer_id, **delivery["report_result"]})
    if status == "confirmed":
        report_results.append(
            {
                "source_peer_id": runtime.peer_id,
                **(await send_report(outcome, request.ttl)),
            }
        )

    ttl_failures: list[dict[str, Any]] = []
    for vote in votes:
        if vote.get("status") == "dropped_ttl":
            ttl_failures.append({"stage": "vote", **vote})
    for delivery in outcome_deliveries:
        if delivery.get("status") == "dropped_ttl":
            ttl_failures.append({"stage": "outcome", **delivery})
    for report in report_results:
        if report.get("status") == "dropped_ttl":
            ttl_failures.append({"stage": "report", **report})

    outcome["outcome_deliveries"] = outcome_deliveries
    outcome["report_results"] = report_results
    outcome["ttl_failures"] = ttl_failures
    store_outcome(outcome)

    return outcome


@app.get("/sync/recent")
async def recent() -> dict[str, Any]:
    if not runtime.active:
        raise HTTPException(status_code=409, detail="Peer is inactive")
    cutoff = time.time() - RETENTION_SECONDS
    return {
        "peer_id": runtime.peer_id,
        "events": [item for item in runtime.events if item["unix_timestamp"] >= cutoff],
    }
