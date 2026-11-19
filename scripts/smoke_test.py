import json
import time
import urllib.request


BASE_URL = "http://127.0.0.1:8080"


def send(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


system = send("GET", "/api/system")
active = [peer["peer_id"] for peer in system["peers"] if peer.get("active")]
assert active == ["peer-a", "peer-b", "peer-c", "peer-d"], active

confirmed = send("POST", "/api/events/global")
assert confirmed["result"]["status"] == "confirmed", confirmed
assert confirmed["result"]["observed"] == 4, confirmed

# Deja cerrar la ventana de correlación del evento global anterior.
time.sleep(4)
false_positive = send("POST", "/api/events/false-positive", {"peer_id": "peer-b"})
assert false_positive["result"]["status"] == "rejected", false_positive

send("POST", "/api/links", {"first": "peer-a", "second": "gateway", "enabled": False})
routed = send("POST", "/api/events/global")
assert routed["result"]["status"] == "confirmed", routed
time.sleep(1)
gateway = send("GET", "/api/system")["gateway"]
latest = gateway["events"][0]
peer_a_report = next(item for item in latest["reports"] if item["peer_id"] == "peer-a")
assert len(peer_a_report["route"]) == 3, peer_a_report
send("POST", "/api/links", {"first": "peer-a", "second": "gateway", "enabled": True})

print("Smoke test correcto: confirmación, falso positivo y ruta alternativa verificados.")
