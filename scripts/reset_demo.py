import json
import urllib.request


BASE_URL = "http://127.0.0.1:8080"
PEERS = [f"peer-{letter}" for letter in "abcdef"]


def send(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


for peer_id in PEERS:
    send("POST", f"/api/peers/{peer_id}/active", {"active": peer_id in PEERS[:4]})

nodes = [*PEERS, "gateway"]
for index, first in enumerate(nodes):
    for second in nodes[index + 1 :]:
        if first == "gateway" and second == "gateway":
            continue
        try:
            send("POST", "/api/links", {"first": first, "second": second, "enabled": True})
        except Exception:
            pass

send("PUT", "/api/settings", {"policy": "strict", "required_votes": 3, "ttl": 5, "threshold": 120})
send("DELETE", "/api/gateway")
print("CAMPO P2P restaurado: peers A-D activos, E-F inactivos, malla completa, TTL 5.")
