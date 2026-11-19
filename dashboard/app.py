from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common.core import GATEWAY_ID, PEER_IDS, default_topology, edge_key


class PeerActiveRequest(BaseModel):
    active: bool


class LinkRequest(BaseModel):
    first: str
    second: str
    enabled: bool


class FalsePositiveRequest(BaseModel):
    peer_id: str | None = None
    peer_ids: list[str] = Field(default_factory=list, max_length=6)


class SettingsRequest(BaseModel):
    policy: str = Field(pattern="^(strict|tolerant|custom)$")
    required_votes: int | None = Field(default=None, ge=0, le=6)
    ttl: int = Field(ge=1, le=20)
    threshold: float = Field(ge=1, le=1000)


topology = default_topology()
settings: dict[str, Any] = {
    "policy": "strict",
    "required_votes": 3,
    "ttl": 5,
    "threshold": 120.0,
}
client: httpx.AsyncClient | None = None


def peer_url(peer_id: str) -> str:
    return f"http://{peer_id}:8000"


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    if client is None:
        raise RuntimeError("HTTP client is not ready")
    return await client.request(method, url, timeout=6, **kwargs)


async def broadcast_topology() -> None:
    await asyncio.gather(
        *[
            request("POST", f"{peer_url(peer_id)}/control/topology", json={"edges": topology})
            for peer_id in PEER_IDS
        ],
        return_exceptions=True,
    )


async def broadcast_threshold() -> None:
    await asyncio.gather(
        *[
            request(
                "POST",
                f"{peer_url(peer_id)}/control/threshold",
                json={"threshold": settings["threshold"]},
            )
            for peer_id in PEER_IDS
        ],
        return_exceptions=True,
    )


async def initialize_network() -> None:
    await asyncio.sleep(2)
    await broadcast_topology()
    await broadcast_threshold()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = httpx.AsyncClient()
    task = asyncio.create_task(initialize_network())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title="CAMPO P2P · Control", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "dashboard"}


@app.get("/")
async def index():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"name": "CAMPO P2P", "status": "backend ready", "docs": "/docs"}


async def peer_states() -> list[dict[str, Any]]:
    async def get_one(peer_id: str) -> dict[str, Any]:
        try:
            response = await request("GET", f"{peer_url(peer_id)}/state")
            return response.json()
        except Exception as exc:
            return {"peer_id": peer_id, "active": False, "unreachable": True, "error": str(exc)[:100]}

    return await asyncio.gather(*[get_one(peer_id) for peer_id in PEER_IDS])


@app.get("/api/system")
async def system_state() -> dict[str, Any]:
    peers = await peer_states()
    try:
        gateway = (await request("GET", "http://gateway:8000/state")).json()
    except Exception as exc:
        gateway = {"service": "gateway", "unreachable": True, "error": str(exc)[:100]}
    return {
        "simulation": True,
        "peers": peers,
        "gateway": gateway,
        "topology": topology,
        "settings": settings,
        "timestamp": time.time(),
    }


async def active_peer_ids() -> list[str]:
    return [item["peer_id"] for item in await peer_states() if item.get("active")]


async def inject_and_validate(
    targets: list[str], coordinator: str, event_prefix: str = "NEU"
) -> dict[str, Any]:
    if not targets:
        raise HTTPException(status_code=409, detail="No active peers")
    event_id = f"{event_prefix}-{uuid.uuid4().hex[:8].upper()}"
    event_timestamp = time.time()
    injection_results = await asyncio.gather(
        *[
            request(
                "POST",
                f"{peer_url(peer_id)}/control/inject",
                json={"event_id": event_id, "event_timestamp": event_timestamp},
            )
            for peer_id in targets
        ],
        return_exceptions=True,
    )
    await asyncio.sleep(0.4)
    response = await request(
        "POST",
        f"{peer_url(coordinator)}/events/validate",
        json={
            "event_id": event_id,
            "event_timestamp": event_timestamp,
            "policy": settings["policy"],
            "required_votes": settings["required_votes"],
            "ttl": settings["ttl"],
        },
    )
    return {
        "event_id": event_id,
        "injected_peers": targets,
        "injection_successes": sum(not isinstance(item, Exception) for item in injection_results),
        "result": response.json(),
    }


@app.post("/api/events/global")
async def global_event() -> dict[str, Any]:
    active = await active_peer_ids()
    return await inject_and_validate(active, active[0] if active else "")


@app.post("/api/events/false-positive")
async def false_positive(request_body: FalsePositiveRequest) -> dict[str, Any]:
    active = await active_peer_ids()
    requested = request_body.peer_ids or ([request_body.peer_id] if request_body.peer_id else [])
    selected = list(dict.fromkeys(requested))
    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one peer")
    invalid = [peer_id for peer_id in selected if peer_id not in active]
    if invalid:
        raise HTTPException(status_code=409, detail="Every selected peer must be active")
    return await inject_and_validate(selected, selected[0], event_prefix="FP")


@app.post("/api/peers/{peer_id}/active")
async def set_peer_active(peer_id: str, request_body: PeerActiveRequest) -> dict[str, Any]:
    if peer_id not in PEER_IDS:
        raise HTTPException(status_code=404, detail="Unknown peer")
    response = await request(
        "POST", f"{peer_url(peer_id)}/control/active", json=request_body.model_dump()
    )
    return response.json()


@app.post("/api/links")
async def set_link(request_body: LinkRequest) -> dict[str, Any]:
    valid_nodes = {*PEER_IDS, GATEWAY_ID}
    if request_body.first not in valid_nodes or request_body.second not in valid_nodes:
        raise HTTPException(status_code=400, detail="Unknown node")
    if request_body.first == request_body.second:
        raise HTTPException(status_code=400, detail="A link needs two different nodes")
    key = edge_key(request_body.first, request_body.second)
    if key not in topology:
        raise HTTPException(status_code=400, detail="Unsupported link")
    topology[key] = request_body.enabled
    await broadcast_topology()
    return {"link": key, "enabled": topology[key]}


@app.put("/api/settings")
async def update_settings(request_body: SettingsRequest) -> dict[str, Any]:
    settings.update(request_body.model_dump(exclude_none=True))
    await broadcast_threshold()
    return settings


@app.delete("/api/gateway")
async def clear_gateway() -> dict[str, Any]:
    response = await request("DELETE", "http://gateway:8000/state")
    return response.json()
