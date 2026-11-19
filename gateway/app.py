from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


RETENTION_SECONDS = 300


class Envelope(BaseModel):
    message_id: str
    packet_type: str
    origin: str
    destination: str
    route: list[str]
    hop_index: int
    ttl: int
    payload: dict[str, Any]


reports: dict[str, dict[str, dict[str, Any]]] = {}
packet_log: deque[dict[str, Any]] = deque(maxlen=500)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def cleanup_loop() -> None:
    while True:
        cutoff = time.time() - RETENTION_SECONDS
        expired = [
            event_id
            for event_id, by_peer in reports.items()
            if by_peer and max(item["received_unix"] for item in by_peer.values()) < cutoff
        ]
        for event_id in expired:
            reports.pop(event_id, None)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="CAMPO P2P · Gateway", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


@app.post("/network/report")
async def receive_report(envelope: Envelope) -> dict[str, Any]:
    payload = envelope.payload
    event_id = payload["event_id"]
    peer_id = payload["peer_id"]
    record = {
        **payload,
        "route": envelope.route,
        "remaining_ttl": envelope.ttl,
        "received_at": utc_now(),
        "received_unix": time.time(),
    }
    reports.setdefault(event_id, {})[peer_id] = record
    packet_log.appendleft(
        {
            "timestamp": record["received_at"],
            "event_id": event_id,
            "peer_id": peer_id,
            "route": envelope.route,
            "remaining_ttl": envelope.ttl,
        }
    )
    return {
        "status": "delivered",
        "event_id": event_id,
        "peer_id": peer_id,
        "route": envelope.route,
        "remaining_ttl": envelope.ttl,
    }


@app.get("/state")
async def state() -> dict[str, Any]:
    events = []
    for event_id, by_peer in reports.items():
        ordered = sorted(by_peer.values(), key=lambda item: item["peer_id"])
        events.append(
            {
                "event_id": event_id,
                "report_count": len(ordered),
                "reports": ordered,
                "latest_at": max(item["received_at"] for item in ordered),
            }
        )
    events.sort(key=lambda item: item["latest_at"], reverse=True)
    return {"service": "gateway", "events": events, "packet_log": list(packet_log)}


@app.delete("/state")
async def clear_state() -> dict[str, str]:
    reports.clear()
    packet_log.clear()
    return {"status": "cleared"}
