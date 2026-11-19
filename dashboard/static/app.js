const PEER_IDS = ["peer-a", "peer-b", "peer-c", "peer-d", "peer-e", "peer-f"];
const stage = document.querySelector("#networkStage");
const world = document.querySelector("#networkWorld");
const menu = document.querySelector("#nodeMenu");
const toast = document.querySelector("#toast");
const eventButton = document.querySelector("#neutrinoButton");

let systemState = null;
let selectedPeer = "A";
let menuPeer = "A";
let zoom = 1;
let panX = 0;
let panY = 0;
let dragStart = null;
let pendingActions = 0;
let playbackSteps = [];
let playbackIndex = 0;
let playbackSpeed = 2.5;
let playbackPaused = false;
let playbackRunning = false;
let currentPacketAnimation = null;
let playbackGeneration = 0;
let playbackActivity = [];
let playbackSession = false;
let playbackCompletion = null;
const selectedFalsePositivePeers = new Set();
let currentPacketElement = null;
let currentPlaybackRoute = null;
let lifecycleVisual = null;
let nodeDrag = null;
let suppressedNodeClick = { id: null, until: 0 };

const DEFAULT_NODE_POSITIONS = {
  "peer-a": [.50, .194], "peer-b": [.80, .419], "peer-c": [.65, .823],
  "peer-d": [.25, .677], "peer-e": [.22, .242], "peer-f": [.44, .903], gateway: [.90, .839],
};
const NODE_POSITIONS = Object.fromEntries(Object.entries(DEFAULT_NODE_POSITIONS).map(([id, position]) => [id, [...position]]));
const LAYOUT_STORAGE_KEY = "campo-p2p-layout-v2";

const letterToId = (letter) => letter === "GW" ? "gateway" : `peer-${letter.toLowerCase()}`;
const idToLetter = (id) => id === "gateway" ? "GW" : id.replace("peer-", "").toUpperCase();
const edgeKey = (first, second) => [first, second].sort().join("|");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]));
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = null;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.detail || `Error HTTP ${response.status}`);
  return payload;
}

function setBusy(busy) {
  document.querySelectorAll(".event-button, .stepper button, .segmented button, .node-toggle, .node-menu-button, .peer-choice, #runFalsePositives").forEach((button) => { button.disabled = busy; });
}

async function runAction(action) {
  pendingActions += 1;
  setBusy(true);
  try { return await action(); }
  finally {
    pendingActions -= 1;
    if (!pendingActions) {
      setBusy(false);
      if (systemState) {
        renderQuorumControls();
        renderFalsePositiveSelector();
      }
    }
  }
}

function showToast(title, detail, error = false) {
  toast.classList.remove("show", "error");
  void toast.offsetWidth;
  toast.querySelector("strong").textContent = title;
  toast.querySelector("small").textContent = detail;
  toast.classList.toggle("error", error);
  toast.classList.add("show");
}

function peerByLetter(letter) {
  return systemState?.peers.find((peer) => peer.peer_id === letterToId(letter));
}

function activePeerIds() {
  return new Set((systemState?.peers || []).filter((peer) => peer.active && !peer.unreachable).map((peer) => peer.peer_id));
}

function parseVisualLink(link) {
  const [first, second] = link.dataset.link.split("-");
  return [letterToId(first), letterToId(second)];
}

function shortestPath(start, destination = "gateway") {
  if (!systemState) return null;
  const allowed = activePeerIds();
  allowed.add("gateway");
  if (!allowed.has(start)) return null;
  const graph = Object.fromEntries([...allowed].map((node) => [node, []]));
  Object.entries(systemState.topology).forEach(([key, enabled]) => {
    if (!enabled) return;
    const [first, second] = key.split("|");
    if (allowed.has(first) && allowed.has(second)) {
      graph[first].push(second);
      graph[second].push(first);
    }
  });
  const queue = [[start]];
  const visited = new Set([start]);
  while (queue.length) {
    const path = queue.shift();
    const node = path.at(-1);
    if (node === destination) return path;
    [...(graph[node] || [])].sort().forEach((neighbor) => {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push([...path, neighbor]);
      }
    });
  }
  return null;
}

function routeLabel(route) {
  return route?.map(idToLetter).join(" → ") || "Sin ruta";
}

function loadSavedLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_STORAGE_KEY) || "null");
    Object.keys(NODE_POSITIONS).forEach((id) => {
      const position = saved?.[id];
      if (Array.isArray(position) && position.length === 2 && position.every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
        NODE_POSITIONS[id] = [...position];
      }
    });
  } catch {
    localStorage.removeItem(LAYOUT_STORAGE_KEY);
  }
}

function saveLayout() {
  localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(NODE_POSITIONS));
}

function nodeElement(id) {
  return id === "gateway" ? document.querySelector(".gateway-node") : document.querySelector(`.map-node[data-peer="${idToLetter(id)}"]`);
}

function applyNodePosition(id) {
  const element = nodeElement(id);
  const position = NODE_POSITIONS[id];
  if (!element || !position) return;
  element.style.setProperty("--x", `${position[0] * 100}%`);
  element.style.setProperty("--y", `${position[1] * 100}%`);
}

function offsetPathForRoute(route) {
  const width = world.clientWidth;
  const height = world.clientHeight;
  const points = route.map((node) => NODE_POSITIONS[node]).filter(Boolean).map(([x, y]) => [Math.round(x * width), Math.round(y * height)]);
  if (points.length < 2) return "";
  return `path("${points.map(([x, y], index) => `${index ? "L" : "M"} ${x} ${y}`).join(" ")}")`;
}

function updateNetworkGeometry() {
  Object.keys(NODE_POSITIONS).forEach(applyNodePosition);
  document.querySelectorAll(".network-link").forEach((link) => {
    const [first, second] = parseVisualLink(link);
    const start = NODE_POSITIONS[first];
    const end = NODE_POSITIONS[second];
    if (!start || !end) return;
    const path = `M${Math.round(start[0] * 1000)} ${Math.round(start[1] * 620)}L${Math.round(end[0] * 1000)} ${Math.round(end[1] * 620)}`;
    link.querySelectorAll("path").forEach((line) => line.setAttribute("d", path));
  });
  if (currentPacketElement && currentPlaybackRoute) {
    currentPacketElement.style.offsetPath = offsetPathForRoute(currentPlaybackRoute);
  }
}

function resetLayout() {
  Object.entries(DEFAULT_NODE_POSITIONS).forEach(([id, position]) => { NODE_POSITIONS[id] = [...position]; });
  saveLayout();
  updateNetworkGeometry();
  showToast("Disposición restaurada", "Los peers y el gateway volvieron a sus posiciones iniciales.");
}

function renderLinks() {
  const active = activePeerIds();
  const links = [...document.querySelectorAll(".network-link")];
  links.forEach((link) => {
    const [first, second] = parseVisualLink(link);
    const endpointsAvailable = (first === "gateway" || active.has(first)) && (second === "gateway" || active.has(second));
    const enabled = Boolean(systemState.topology[edgeKey(first, second)]);
    link.classList.toggle("unavailable", !endpointsAvailable);
    link.classList.toggle("disabled", endpointsAvailable && !enabled);
    link.querySelector(".link-line").classList.remove("route");
    link.querySelector(".link-line").removeAttribute("marker-end");
    link.setAttribute("aria-label", `Enlace ${idToLetter(first)}-${idToLetter(second)}, ${enabled ? "activo" : "cortado"}`);
  });

  const route = shortestPath(letterToId(selectedPeer));
  route?.slice(0, -1).forEach((node, index) => {
    const next = route[index + 1];
    const link = links.find((candidate) => {
      const endpoints = parseVisualLink(candidate);
      return endpoints.includes(node) && endpoints.includes(next);
    });
    if (link) {
      link.querySelector(".link-line").classList.add("route");
      if (index === route.length - 2) link.querySelector(".link-line").setAttribute("marker-end", "url(#routeArrow)");
    }
  });
}

function renderGlobalActivity() {
  const list = document.querySelector("#activityList");
  if (playbackSession) {
    const synchronized = playbackActivity.slice(-8).reverse();
    if (!synchronized.length) {
      list.innerHTML = '<li class="empty-activity">Esperando el próximo paquete de la red P2P.</li>';
      return;
    }
    list.innerHTML = synchronized.map((entry) => `<li class="playback-entry"><span class="activity-icon ${entry.packetType === "REPORT" ? "report" : ""} ${entry.ttlFailure ? "ttl" : ""}">${escapeHtml(entry.packetType.charAt(0))}</span><div><strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.packetType)} · ${escapeHtml(routeLabel(entry.route))}</small></div><time>Paso ${entry.step}</time></li>`).join("");
    return;
  }
  const log = (systemState?.peers || [])
    .flatMap((peer) => {
      const visibleEntries = peer.active && !peer.unreachable
        ? (peer.packet_log || [])
        : (peer.packet_log || []).filter((entry) => entry.action === "DEACTIVATED");
      return visibleEntries.map((entry) => ({ ...entry, ownerPeerId: peer.peer_id }));
    })
    .sort((first, second) => new Date(second.timestamp) - new Date(first.timestamp))
    .slice(0, 8);
  if (!log.length) {
    list.innerHTML = '<li class="empty-activity">Todavía no hay paquetes registrados en la red.</li>';
    return;
  }
  const labels = {
    DISCOVERED: "Peer descubierto", SENT: "Paquete enviado", RECEIVED: "Paquete recibido",
    FORWARDED: "Paquete reenviado", DUPLICATE: "Duplicado descartado", FAILED: "Envío fallido",
    ACTIVATED: "Peer activado", DEACTIVATED: "Peer desactivado", MEASURED: "Evento detectado", SYNCED: "Estado sincronizado", MEMBERSHIP: "Membresía actualizada",
  };
  list.innerHTML = log.map((entry) => {
    const type = entry.packet_type || "P2P";
    const icon = type.charAt(0);
    const route = entry.route?.length ? entry.route.map(idToLetter).join(" → ") : entry.peer;
    const time = new Date(entry.timestamp).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    const inactiveIds = new Set((systemState?.peers || []).filter((item) => !item.active || item.unreachable).map((item) => item.peer_id));
    const referencesInactivePeer = (entry.route || []).some((node) => inactiveIds.has(node)) || inactiveIds.has(entry.peer);
    const owner = idToLetter(entry.ownerPeerId);
    return `<li class="${referencesInactivePeer ? "historical" : ""}"><span class="activity-icon ${type === "REPORT" ? "report" : ""}">${escapeHtml(icon)}</span><div><strong>Peer ${escapeHtml(owner)} · ${escapeHtml(labels[entry.action] || entry.action)}</strong><small>${escapeHtml(type)} · ${escapeHtml(route || entry.detail)}</small></div><time>${escapeHtml(time)}</time></li>`;
  }).join("");
}

function renderSelectedPeer() {
  const peer = peerByLetter(selectedPeer);
  if (!peer) return;
  const activeIds = activePeerIds();
  const operationalLinks = Object.entries(systemState.topology).filter(([key, enabled]) => {
    if (!enabled) return false;
    return key.split("|").every((node) => node === "gateway" || activeIds.has(node));
  }).length;
  const reportCount = (systemState.gateway.events || []).reduce((total, event) => total + event.report_count, 0);
  const networkOperational = activeIds.size >= 2;

  document.querySelectorAll(".map-node").forEach((node) => node.classList.toggle("selected", node.dataset.peer === selectedPeer));
  document.querySelector("#networkStatus span").textContent = networkOperational ? "Operativa" : "Degradada";
  document.querySelector("#networkStatus").classList.toggle("offline", !networkOperational);
  document.querySelector("#networkPeers").textContent = `${activeIds.size} de ${systemState.peers.length}`;
  document.querySelector("#networkLinks").textContent = `${operationalLinks} activos`;
  const configuredRequired = Math.min(activeIds.size, Math.max(0, systemState.settings.required_votes ?? Math.max(0, activeIds.size - 1)));
  document.querySelector("#networkConsensus").textContent = systemState.settings.policy === "strict"
    ? `Todos ${activeIds.size}/${activeIds.size}`
    : `Configurable ${configuredRequired}/${activeIds.size}`;
  document.querySelector("#networkReports").textContent = `${reportCount} recibidos`;
  const requiredVotes = systemState.settings.policy === "strict" ? activeIds.size : configuredRequired;
  const toleratedPeers = Math.max(0, activeIds.size - requiredVotes);
  document.querySelector("#consensusExplanation").textContent = systemState.settings.policy === "strict"
    ? `Con ${activeIds.size} peers activos, se necesitan ${requiredVotes} votos positivos. Si uno no detecta o no responde, el evento no se confirma.`
    : requiredVotes === 0
      ? `K es 0 de ${activeIds.size}: cualquier evento se confirma sin exigir votos positivos. Es el extremo más permisivo de la demostración.`
      : `K es ${requiredVotes} de ${activeIds.size}: se ${toleratedPeers === 1 ? "permite" : "permiten"} hasta ${toleratedPeers} ${toleratedPeers === 1 ? "peer" : "peers"} sin detectar, sin responder o en desacuerdo.`;
  document.querySelector("#activityContext").textContent = playbackSession ? "Red completa · sincronizada con la reproducción" : "Red completa · tráfico real más reciente";
  renderGlobalActivity();
  renderLinks();
}

function renderNodeDetails(letter) {
  const peer = peerByLetter(letter);
  if (!peer || !systemState) return;
  const active = peer.active && !peer.unreachable;
  const direct = Object.entries(systemState.topology)
    .filter(([key]) => key.split("|").includes(peer.peer_id))
    .map(([key, enabled]) => {
      const other = key.split("|").find((node) => node !== peer.peer_id);
      const otherPeer = other === "gateway" ? null : systemState.peers.find((item) => item.peer_id === other);
      const available = other === "gateway" || Boolean(otherPeer?.active && !otherPeer?.unreachable);
      return { other, enabled, available };
    });
  const discovered = peer.discovered_peers || [];
  const packets = (peer.packet_log || []).slice(0, 8);
  const latestEvent = peer.events?.[0];
  document.querySelector("#inspectorTitle").textContent = `Peer ${letter}`;
  document.querySelector("#inspectorSubtitle").textContent = active ? `${peer.zone} · participando en la malla` : `${peer.zone} · apagado, sin tráfico nuevo`;
  const connections = direct.map(({ other, enabled, available }) => {
    const usable = active && enabled && available;
    const state = !enabled ? "cortado" : !available ? "peer apagado" : active ? "operativo" : "nodo local apagado";
    return `<li><span>${escapeHtml(letter)} ↔ ${escapeHtml(idToLetter(other))}</span><small class="${usable ? "live" : "cut"}">${escapeHtml(state)}</small></li>`;
  }).join("");
  const registry = discovered.length ? discovered.map((item) => `<li><span>${escapeHtml(idToLetter(item.peer_id))}</span><small class="${item.online ? "positive" : "negative"}">${item.online ? `en línea · ${item.last_seen_seconds}s` : "fuera de línea"}</small></li>`).join("") : '<li><span>Sin peers descubiertos</span><small>—</small></li>';
  const packetRows = packets.length ? packets.map((entry) => {
    const route = entry.route?.length ? entry.route.map(idToLetter).join(" → ") : idToLetter(entry.peer || peer.peer_id);
    const time = new Date(entry.timestamp).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    return `<li><span>${escapeHtml(entry.action)} · ${escapeHtml(entry.packet_type)}</span><small>${escapeHtml(route)} · ${escapeHtml(time)}</small></li>`;
  }).join("") : '<li><span>Sin paquetes registrados</span><small>—</small></li>';
  const votes = latestEvent?.votes?.length ? latestEvent.votes.map((vote) => `<li><span>Peer ${escapeHtml(idToLetter(vote.peer_id))}</span><small class="${vote.observed ? "positive" : "negative"}">${vote.observed ? "detectó" : vote.observed === false ? "no detectó" : "sin respuesta"} · ${escapeHtml(routeLabel(vote.route))}</small></li>`).join("") : '<li><span>Sin votaciones</span><small>—</small></li>';
  document.querySelector("#inspectorBody").innerHTML = `
    <section class="inspector-section"><h3>Ruta calculada al gateway</h3><div class="inspector-path">${escapeHtml(active ? routeLabel(shortestPath(peer.peer_id)) : "Sin ruta: el peer está apagado")}</div></section>
    <section class="inspector-section"><h3>Conexiones directas</h3><ul class="connection-list">${connections}</ul></section>
    <section class="inspector-section"><h3>Peers descubiertos</h3><ul class="vote-list">${registry}</ul></section>
    <section class="inspector-section"><h3>Última votación conocida</h3><ul class="vote-list">${votes}</ul></section>
    <section class="inspector-section"><h3>Actividad exclusiva de Peer ${escapeHtml(letter)} ${active ? "" : "(histórica)"}</h3><ul class="packet-list">${packetRows}</ul></section>`;
  document.querySelector("#nodeInspector").hidden = false;
}

function renderQuorumControls() {
  if (!systemState) return;
  const activeCount = activePeerIds().size;
  const configured = Math.min(activeCount, Math.max(0, systemState.settings.required_votes ?? Math.max(0, activeCount - 1)));
  const custom = systemState.settings.policy !== "strict";
  document.querySelector("#quorumValue").textContent = configured;
  document.querySelector("#quorumRange").textContent = `de ${activeCount} ${activeCount === 1 ? "peer activo" : "peers activos"}`;
  document.querySelector("#quorumDown").disabled = pendingActions > 0 || !custom || configured <= 0;
  document.querySelector("#quorumUp").disabled = pendingActions > 0 || !custom || configured >= activeCount;
  document.querySelector(".quorum-setting").classList.toggle("disabled", !custom);
}

function renderFalsePositiveSelector() {
  if (!systemState) return;
  const container = document.querySelector("#falsePositivePeers");
  const activeLetters = new Set(systemState.peers.filter((peer) => peer.active && !peer.unreachable).map((peer) => idToLetter(peer.peer_id)));
  [...selectedFalsePositivePeers].forEach((letter) => { if (!activeLetters.has(letter)) selectedFalsePositivePeers.delete(letter); });
  container.innerHTML = systemState.peers.map((peer) => {
    const letter = idToLetter(peer.peer_id);
    const active = activeLetters.has(letter);
    const selected = selectedFalsePositivePeers.has(letter);
    return `<button class="peer-choice" data-false-peer="${escapeHtml(letter)}" aria-pressed="${selected}" ${active && !pendingActions ? "" : "disabled"}><strong>Peer ${escapeHtml(letter)}</strong><small>${active ? selected ? "seleccionado" : "activo" : "apagado"}</small></button>`;
  }).join("");
  container.querySelectorAll(".peer-choice").forEach((button) => button.addEventListener("click", () => {
    const letter = button.dataset.falsePeer;
    if (selectedFalsePositivePeers.has(letter)) selectedFalsePositivePeers.delete(letter);
    else selectedFalsePositivePeers.add(letter);
    renderFalsePositiveSelector();
  }));
  const count = selectedFalsePositivePeers.size;
  document.querySelector("#falsePositiveCount").textContent = count ? `${count} ${count === 1 ? "peer seleccionado" : "peers seleccionados"}` : "Ningún peer seleccionado";
  document.querySelector("#runFalsePositives").disabled = pendingActions > 0 || count === 0;
}

function renderSystem(state) {
  systemState = state;
  const active = state.peers.filter((peer) => peer.active && !peer.unreachable);
  document.querySelector("#meshStatus").innerHTML = "<i></i> Malla operativa";
  document.querySelector("#meshStatus").classList.remove("error");
  document.querySelector("#activePeerCount").textContent = `${active.length} peers conectados`;
  document.querySelector("#ttlValue").textContent = state.settings.ttl;
  document.querySelectorAll(".segmented button").forEach((button) => button.classList.toggle("active", button.dataset.policy === state.settings.policy));
  renderQuorumControls();
  renderFalsePositiveSelector();

  state.peers.forEach((peer) => {
    const letter = idToLetter(peer.peer_id);
    const node = document.querySelector(`.map-node[data-peer="${letter}"]`);
    if (!node) return;
    const actualActive = peer.active && !peer.unreachable;
    const lifecycle = lifecycleVisual?.peerId === peer.peer_id ? lifecycleVisual : null;
    const isActive = actualActive || Boolean(lifecycle);
    const discovered = (peer.discovered_peers || []).filter((item) => item.online).length;
    node.classList.toggle("active", isActive);
    node.classList.toggle("standby", !isActive && !peer.unreachable);
    node.classList.toggle("offline", Boolean(peer.unreachable));
    node.classList.toggle("lifecycle-transition", Boolean(lifecycle));
    node.querySelector(".node-label small").textContent = lifecycle
      ? lifecycle.activating ? "Iniciando · descubriendo vecinos" : "Saliendo · notificando vecinos"
      : actualActive ? `Activo · ${discovered} vecinos` : peer.unreachable ? "Sin respuesta" : "En espera · no participa";
  });

  const reportCount = (state.gateway.events || []).reduce((total, event) => total + event.report_count, 0);
  document.querySelector("#gatewayReports").textContent = `${reportCount} ${reportCount === 1 ? "reporte recibido" : "reportes recibidos"}`;
  renderSelectedPeer();
}

async function refreshSystem({ quiet = false } = {}) {
  if (pendingActions && quiet) return;
  try {
    renderSystem(await api("GET", "/api/system"));
  } catch (error) {
    document.querySelector("#meshStatus").innerHTML = "<i></i> Backend sin respuesta";
    document.querySelector("#meshStatus").classList.add("error");
    if (!quiet) showToast("No se pudo leer la red", error.message, true);
  }
}

function selectPeer(letter) {
  selectedPeer = letter;
  renderSelectedPeer();
}

async function toggleNode(letter) {
  const peer = peerByLetter(letter);
  if (!peer) return;
  closeMenu();
  stopPlayback();
  resetPlaybackActivity();
  try {
    await runAction(async () => {
      const activating = !peer.active;
      const result = await api("POST", `/api/peers/${peer.peer_id}/active`, { active: activating });
      showToast(result.active ? `Peer ${letter} activado` : `Peer ${letter} desactivado`, result.active ? "El peer inició descubrimiento y sincronización." : "El peer dejó de participar en la malla.");
      await refreshSystem();
      prepareLifecyclePlayback(letter, result, activating);
    });
  } catch (error) { showToast("No se cambió el peer", error.message, true); }
}

async function toggleLink(link) {
  if (link.classList.contains("unavailable") || !systemState) return;
  const [first, second] = parseVisualLink(link);
  const enabled = Boolean(systemState.topology[edgeKey(first, second)]);
  stopPlayback();
  resetPlaybackActivity();
  try {
    await runAction(async () => {
      const result = await api("POST", "/api/links", { first, second, enabled: !enabled });
      showToast(result.enabled ? "Enlace restaurado" : "Enlace cortado", `${idToLetter(first)}-${idToLetter(second)} · topología real actualizada`, !result.enabled);
      await refreshSystem();
    });
  } catch (error) { showToast("No se cambió el enlace", error.message, true); }
}

function openMenu(node, button) {
  menuPeer = node.dataset.peer;
  const peer = peerByLetter(menuPeer);
  document.querySelector("#menuTitle").textContent = `Peer ${menuPeer}`;
  document.querySelector("#toggleMenuLabel").textContent = peer?.active ? "Desactivar nodo" : "Activar nodo";
  const stageRect = stage.getBoundingClientRect();
  const buttonRect = button.getBoundingClientRect();
  menu.style.left = `${Math.min(buttonRect.left - stageRect.left, stageRect.width - 240)}px`;
  menu.style.top = `${Math.min(buttonRect.bottom - stageRect.top + 6, stageRect.height - 170)}px`;
  menu.hidden = false;
}

function closeMenu() { menu.hidden = true; }

function updateZoom(next) {
  zoom = Math.min(1.8, Math.max(.75, next));
  world.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  document.querySelector("#zoomLevel").textContent = `${Math.round(zoom * 100)}%`;
}

function clearPlaybackVisuals() {
  currentPacketAnimation?.cancel();
  currentPacketAnimation = null;
  currentPacketElement = null;
  currentPlaybackRoute = null;
  world.querySelectorAll(".sim-packet").forEach((packet) => packet.remove());
  document.querySelectorAll(".network-link.playback").forEach((link) => link.classList.remove("playback"));
  document.querySelectorAll(".map-node.playback-node").forEach((node) => node.classList.remove("playback-node"));
}

function stopPlayback() {
  playbackGeneration += 1;
  playbackRunning = false;
  playbackPaused = false;
  playbackSteps = [];
  playbackIndex = 0;
  playbackCompletion = null;
  clearPlaybackVisuals();
  if (lifecycleVisual) {
    lifecycleVisual = null;
    if (systemState) renderSystem(systemState);
  }
  document.querySelector("#pausePlayback").setAttribute("aria-pressed", "false");
  document.querySelector("#pausePlayback span").textContent = "Pausa";
}

function resetPlaybackActivity() {
  playbackSession = false;
  playbackActivity = [];
}

function routeEdges(route) {
  return route.slice(0, -1).map((node, index) => edgeKey(node, route[index + 1]));
}

function buildPlaybackSteps(result) {
  const coordinator = result.coordinator;
  const steps = [{ phase: 0, packetType: "DETECTION", label: `Peer ${idToLetter(coordinator)} detecta el evento`, route: [coordinator] }];
  const addTtlDrop = (phase, item, context) => {
    const travelled = item.traversed_route?.length ? item.traversed_route : item.route;
    const droppedAt = item.dropped_at || travelled?.at(-1) || coordinator;
    steps.push({
      phase,
      packetType: "TTL_DROP",
      label: `TTL agotado en ${idToLetter(droppedAt)} · ${context} descartado`,
      route: [droppedAt],
      ttlFailure: true,
    });
  };

  (result.votes || []).filter((vote) => vote.peer_id !== coordinator).forEach((vote) => {
    const travelled = vote.traversed_route?.length ? vote.traversed_route : vote.route;
    if (travelled?.length > 1) {
      steps.push({ phase: 1, packetType: "CANDIDATE", label: `${idToLetter(coordinator)} consulta a ${idToLetter(vote.peer_id)} · TTL ${result.ttl}`, route: travelled });
    }
    if (vote.status === "delivered" && vote.route?.length) {
      steps.push({ phase: 1, packetType: "VOTE", label: `${idToLetter(vote.peer_id)} responde ${vote.observed ? "SÍ" : "NO"}`, route: [...vote.route].reverse() });
    } else if (vote.status === "dropped_ttl") {
      addTtlDrop(1, vote, `consulta a ${idToLetter(vote.peer_id)}`);
    }
  });

  (result.outcome_deliveries || []).forEach((delivery) => {
    const targetPeer = delivery.target_peer_id || delivery.peer_id;
    const travelled = delivery.traversed_route?.length ? delivery.traversed_route : delivery.route;
    if (travelled?.length > 1) {
      steps.push({ phase: 2, packetType: "OUTCOME", label: `${idToLetter(coordinator)} distribuye la decisión a ${idToLetter(targetPeer)}`, route: travelled });
    }
    if (delivery.status === "dropped_ttl") addTtlDrop(2, delivery, `resultado para ${idToLetter(targetPeer)}`);
  });

  (result.report_results || []).forEach((report) => {
    const sourcePeer = report.source_peer_id || report.peer_id;
    const travelled = report.traversed_route?.length ? report.traversed_route : report.route;
    if (travelled?.length > 1) {
      steps.push({ phase: 3, packetType: report.status === "delivered" ? "REPORT" : "REPORT_ATTEMPT", label: `${idToLetter(sourcePeer)} envía su reporte al Gateway`, route: travelled });
    }
    if (report.status === "dropped_ttl") addTtlDrop(3, report, `reporte de ${idToLetter(sourcePeer)}`);
  });
  return steps;
}

function setProgressLabels(labels) {
  document.querySelectorAll(".event-progress ol li").forEach((step, index) => {
    step.textContent = labels[index] || "";
  });
}

function buildLifecycleSteps(letter, result, activating) {
  const peerId = letterToId(letter);
  const peers = activating ? (result.discovered_peers || []) : (result.notified_peers || []);
  if (activating) {
    const steps = [{ phase: 0, packetType: "START", label: `Peer ${letter} inicia su servicio P2P`, route: [peerId] }];
    peers.forEach((other) => {
      steps.push({ phase: 1, packetType: "HELLO", label: `${letter} envía HELLO a ${idToLetter(other)}`, route: [peerId, other] });
      steps.push({ phase: 2, packetType: "HELLO_ACK", label: `${idToLetter(other)} confirma y comparte la membresía`, route: [other, peerId] });
    });
    steps.push({ phase: 3, packetType: "SYNC", label: `Peer ${letter} completa registro y sincronización`, route: [peerId], lifecycleFinal: true });
    return steps;
  }
  const steps = [{ phase: 0, packetType: "LEAVE", label: `Peer ${letter} inicia su salida ordenada`, route: [peerId] }];
  peers.forEach((other) => {
    steps.push({ phase: 1, packetType: "STATUS", label: `${letter} notifica su salida a ${idToLetter(other)}`, route: [peerId, other] });
    steps.push({ phase: 2, packetType: "MEMBERSHIP", label: `${idToLetter(other)} retira a ${letter} de su membresía activa`, route: [other] });
  });
  steps.push({ phase: 3, packetType: "OFFLINE", label: `Peer ${letter} queda inactivo y deja de comunicarse`, route: [peerId], lifecycleFinal: true });
  return steps;
}

function finishLifecycleVisual(step) {
  if (!step.lifecycleFinal || !lifecycleVisual) return;
  lifecycleVisual = null;
  if (systemState) renderSystem(systemState);
}

function finishPlaybackSequence() {
  const completion = playbackCompletion;
  playbackCompletion = null;
  completion?.();
}

function setPlaybackPhase(phase) {
  document.querySelectorAll(".event-progress li").forEach((step, index) => {
    step.classList.toggle("done", index <= phase);
    step.classList.toggle("current", index === phase);
  });
}

async function animatePlaybackStep(step, generation) {
  clearPlaybackVisuals();
  setPlaybackPhase(step.phase);
  playbackActivity.push({ ...step, step: playbackIndex + 1 });
  renderGlobalActivity();
  document.querySelector("#playbackStatus").textContent = `${playbackIndex + 1}/${playbackSteps.length} · ${step.label}`;
  if (step.route.length === 1) {
    const letter = idToLetter(step.route[0]);
    const node = document.querySelector(`.map-node[data-peer="${letter}"]`);
    node?.classList.add("playback-node");
    await new Promise((resolve) => setTimeout(resolve, 520 / playbackSpeed));
    node?.classList.remove("playback-node");
    finishLifecycleVisual(step);
    return;
  }
  const activeEdges = new Set(routeEdges(step.route));
  document.querySelectorAll(".network-link").forEach((link) => {
    const [first, second] = parseVisualLink(link);
    link.classList.toggle("playback", activeEdges.has(edgeKey(first, second)));
  });
  const packetPath = offsetPathForRoute(step.route);
  if (!packetPath || generation !== playbackGeneration) return;
  const packet = document.createElement("span");
  packet.className = "sim-packet";
  packet.setAttribute("aria-hidden", "true");
  packet.style.offsetPath = packetPath;
  world.appendChild(packet);
  currentPacketElement = packet;
  currentPlaybackRoute = [...step.route];
  const animation = packet.animate([{ offsetDistance: "0%" }, { offsetDistance: "100%" }], { duration: 760, easing: "cubic-bezier(.45,0,.55,1)", fill: "forwards" });
  animation.playbackRate = playbackSpeed;
  currentPacketAnimation = animation;
  await animation.finished.catch(() => {});
  packet.remove();
  currentPacketAnimation = null;
  currentPacketElement = null;
  currentPlaybackRoute = null;
  finishLifecycleVisual(step);
}

async function runPlayback() {
  if (playbackRunning || playbackPaused || playbackIndex >= playbackSteps.length) return;
  playbackRunning = true;
  const generation = playbackGeneration;
  while (!playbackPaused && playbackIndex < playbackSteps.length && generation === playbackGeneration) {
    await animatePlaybackStep(playbackSteps[playbackIndex], generation);
    if (generation !== playbackGeneration) break;
    playbackIndex += 1;
  }
  playbackRunning = false;
  if (playbackIndex >= playbackSteps.length && generation === playbackGeneration) {
    clearPlaybackVisuals();
    document.querySelector("#playbackStatus").textContent = `Completo · ${playbackSteps.length} pasos`;
    finishPlaybackSequence();
  } else if (playbackPaused && generation === playbackGeneration) {
    document.querySelector("#playbackStatus").textContent = `Pausado · ${playbackIndex}/${playbackSteps.length} completados`;
  }
}

function preparePlayback(result, onComplete) {
  stopPlayback();
  playbackGeneration += 1;
  playbackSession = true;
  playbackActivity = [];
  playbackSteps = buildPlaybackSteps(result);
  playbackIndex = 0;
  playbackCompletion = onComplete;
  setProgressLabels(["Evento local", "Votos P2P", "Decisión", "Envío GW"]);
  renderSelectedPeer();
  document.querySelector("#playbackStatus").textContent = `Listo · ${playbackSteps.length} pasos`;
  runPlayback();
}

function prepareLifecyclePlayback(letter, result, activating) {
  stopPlayback();
  playbackGeneration += 1;
  playbackSession = true;
  playbackActivity = [];
  playbackSteps = buildLifecycleSteps(letter, result, activating);
  playbackIndex = 0;
  lifecycleVisual = { peerId: letterToId(letter), activating };
  setProgressLabels(activating
    ? ["Inicio", "HELLO", "Registro mutuo", "Sincronización"]
    : ["Salida", "Avisos", "Membresía", "Inactivo"]);
  document.querySelector("#eventProgress").classList.remove("blocked");
  document.querySelector("#eventTitle").textContent = activating ? `Peer ${letter} · DESCUBRIMIENTO` : `Peer ${letter} · SALIDA DE LA MALLA`;
  const affected = activating ? (result.discovered_peers || []).length : (result.notified_peers || []).length;
  document.querySelector("#eventSubtitle").textContent = activating
    ? `${affected} ${affected === 1 ? "vecino encontrado" : "vecinos encontrados"} · reproducción sincronizada`
    : `${affected} ${affected === 1 ? "vecino notificado" : "vecinos notificados"} · después permanecerá inactivo`;
  document.querySelector("#eventTrace").textContent = activating
    ? "El peer parte sin membresía activa, envía HELLO y reconstruye su vista de la red."
    : "El peer anuncia su salida una sola vez; apagado no descubre, actualiza ni envía paquetes.";
  renderSystem(systemState);
  document.querySelector("#playbackStatus").textContent = `Listo · ${playbackSteps.length} pasos`;
  runPlayback();
}

function showFinalEventOutcome(payload, falsePositive = false) {
  const result = payload.result;
  const confirmed = result.status === "confirmed";
  const policyLabel = result.policy === "strict" ? "Todos (N/N)" : `Configurable (${result.required}/${result.votes.length})`;
  const statusLabel = { confirmed: "CONFIRMADO", rejected: "RECHAZADO", inconclusive: "INCONCLUSO" };
  const ttlFailures = result.ttl_failures || [];
  const ttlSuffix = ttlFailures.length ? confirmed ? " · ENTREGA PARCIAL POR TTL" : " · TTL AGOTADO" : "";
  document.querySelector("#eventTitle").textContent = `${payload.event_id} · ${statusLabel[result.status] || result.status.toUpperCase()}${ttlSuffix}`;
  const missingVotes = Math.max(0, result.required - result.observed);
  const decisionReason = confirmed
    ? `alcanzó los ${result.required} requeridos`
    : `${missingVotes} ${missingVotes === 1 ? "voto positivo pendiente" : "votos positivos pendientes"}`;
  document.querySelector("#eventSubtitle").textContent = ttlFailures.length
    ? `${policyLabel}: ${result.observed} positivos de ${result.votes.length}; ${ttlFailures.length} ${ttlFailures.length === 1 ? "paquete fue descartado" : "paquetes fueron descartados"} con TTL ${result.ttl}.`
    : `${policyLabel}: ${result.observed} positivos de ${result.votes.length}; ${decisionReason}.`;
  document.querySelector("#eventProgress").classList.toggle("blocked", ttlFailures.length > 0);
  const voteTrace = result.votes.map((vote) => {
    const verdict = vote.observed ? "SÍ" : vote.observed === false ? "NO" : "SIN RESPUESTA";
    return `<span>Peer ${escapeHtml(idToLetter(vote.peer_id))}: ${verdict} · ${escapeHtml(routeLabel(vote.route))}</span>`;
  }).join(" &nbsp;•&nbsp; ");
  const ttlTrace = ttlFailures.length
    ? ` &nbsp;•&nbsp; <span>TTL: descarte en ${ttlFailures.map((failure) => escapeHtml(idToLetter(failure.dropped_at || failure.peer_id))).join(", ")}</span>`
    : "";
  document.querySelector("#eventTrace").innerHTML = voteTrace + ttlTrace + ` &nbsp;•&nbsp; Coordinó Peer ${escapeHtml(idToLetter(result.coordinator))}`;

  if (ttlFailures.length) {
    const first = ttlFailures[0];
    const stageLabel = { vote: "consulta P2P", outcome: "distribución del resultado", report: "reporte al gateway" }[first.stage] || "paquete";
    showToast(
      "TTL agotado · paquete descartado",
      `TTL ${result.ttl} no alcanzó para la ${stageLabel}; se detuvo en ${idToLetter(first.dropped_at || first.peer_id)}.`,
      true,
    );
  } else {
    showToast(
      falsePositive && !confirmed ? "Falso positivo rechazado" : confirmed ? "Evento confirmado por la malla" : "Evento no confirmado",
      `${result.observed} observaciones, ${result.negative} votos negativos.`,
      falsePositive && !confirmed,
    );
  }
}

function renderEventOutcome(payload, falsePositive = false) {
  const result = payload.result;
  stage.classList.remove("event-active");
  void stage.offsetWidth;
  stage.classList.add("event-active");
  document.querySelector("#eventProgress").classList.remove("blocked");
  document.querySelector("#eventTitle").textContent = `${payload.event_id} · VALIDANDO EN LA RED`;
  document.querySelector("#eventSubtitle").textContent = `TTL ${result.ttl} · reproduciendo el recorrido real de los paquetes`;
  document.querySelector("#eventTrace").textContent = "El resultado y las alertas aparecerán al finalizar la animación.";
  preparePlayback(result, () => showFinalEventOutcome(payload, falsePositive));
}

async function injectEvent(falsePositive = false, peerLetter = selectedPeer, peerLetters = null) {
  const requestedLetters = falsePositive ? (peerLetters?.length ? peerLetters : [peerLetter]) : [];
  const unavailable = requestedLetters.filter((letter) => {
    const peer = peerByLetter(letter);
    return !peer?.active || peer.unreachable;
  });
  if (falsePositive && (!requestedLetters.length || unavailable.length)) {
    closeMenu();
    stopPlayback();
    resetPlaybackActivity();
    document.querySelector("#eventProgress").classList.add("blocked");
    document.querySelector("#eventTitle").textContent = unavailable.length ? `Peer ${unavailable.join(", ")} no disponible` : "Selecciona al menos un peer";
    document.querySelector("#eventSubtitle").textContent = "La simulación fue bloqueada antes de crear paquetes";
    document.querySelector("#eventTrace").textContent = unavailable.length ? "Solo los peers activos pueden recibir un falso positivo." : "Marca uno o más peers en la barra derecha y vuelve a probar.";
    document.querySelectorAll(".event-progress li").forEach((step) => step.classList.remove("done"));
    return showToast("No se generó tráfico", unavailable.length ? "La selección contiene peers apagados." : "No hay peers seleccionados.", true);
  }
  closeMenu();
  stopPlayback();
  resetPlaybackActivity();
  try {
    await runAction(async () => {
      document.querySelector("#eventTitle").textContent = falsePositive ? `Falso positivo en Peer ${requestedLetters.join(", ")}` : "Distribuyendo evento a la malla";
      document.querySelector("#eventSubtitle").textContent = "Esperando votos P2P reales";
      const path = falsePositive ? "/api/events/false-positive" : "/api/events/global";
      const payload = await api("POST", path, falsePositive ? { peer_ids: requestedLetters.map(letterToId) } : undefined);
      renderEventOutcome(payload, falsePositive);
      await new Promise((resolve) => setTimeout(resolve, 500));
      await refreshSystem();
    });
  } catch (error) { showToast("La operación falló", error.message, true); }
}

async function saveSettings(nextPolicy, nextTtl, nextRequired = systemState?.settings.required_votes ?? 0) {
  if (!systemState) return;
  try {
    await runAction(async () => {
      const activeCount = activePeerIds().size;
      const boundedRequired = Math.min(activeCount, Math.max(0, nextRequired));
      const settings = await api("PUT", "/api/settings", { policy: nextPolicy, required_votes: boundedRequired, ttl: nextTtl, threshold: systemState.settings.threshold });
      systemState.settings = settings;
      renderSystem(systemState);
      const required = settings.policy === "strict" ? activeCount : boundedRequired;
      showToast("Política de consenso actualizada", `${settings.policy === "strict" ? "Todos (N/N)" : `Configurable (${required}/${activeCount})`}: requiere ${required} votos positivos.`);
    });
  } catch (error) { showToast("No se actualizaron las reglas", error.message, true); }
}

function draggableNodeId(element) {
  return element.dataset.node || letterToId(element.dataset.peer);
}

function beginNodeDrag(event, element) {
  if (event.button !== 0 || event.target.closest(".node-menu-button")) return;
  const id = draggableNodeId(element);
  nodeDrag = { id, element, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false };
  element.classList.add("dragging-node");
  element.setPointerCapture(event.pointerId);
  event.stopPropagation();
}

function moveNode(event) {
  if (!nodeDrag || event.pointerId !== nodeDrag.pointerId) return;
  const distance = Math.hypot(event.clientX - nodeDrag.startX, event.clientY - nodeDrag.startY);
  if (distance < 4 && !nodeDrag.moved) return;
  nodeDrag.moved = true;
  const rect = world.getBoundingClientRect();
  const x = Math.min(.94, Math.max(.06, (event.clientX - rect.left) / rect.width));
  const y = Math.min(.92, Math.max(.07, (event.clientY - rect.top) / rect.height));
  NODE_POSITIONS[nodeDrag.id] = [x, y];
  updateNetworkGeometry();
}

function endNodeDrag(event) {
  if (!nodeDrag || event.pointerId !== nodeDrag.pointerId) return;
  const { element, id, moved, pointerId } = nodeDrag;
  if (element.hasPointerCapture(pointerId)) element.releasePointerCapture(pointerId);
  element.classList.remove("dragging-node");
  if (moved) {
    suppressedNodeClick = { id, until: Date.now() + 350 };
    saveLayout();
  }
  nodeDrag = null;
}

function moveNodeWithKeyboard(event, element) {
  const deltas = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  const delta = deltas[event.key];
  if (!delta) return;
  event.preventDefault();
  event.stopPropagation();
  const id = draggableNodeId(element);
  const step = event.shiftKey ? .05 : .015;
  const [x, y] = NODE_POSITIONS[id];
  NODE_POSITIONS[id] = [
    Math.min(.94, Math.max(.06, x + delta[0] * step)),
    Math.min(.92, Math.max(.07, y + delta[1] * step)),
  ];
  saveLayout();
  updateNetworkGeometry();
}

document.querySelectorAll(".map-node, .gateway-node").forEach((element) => {
  element.addEventListener("pointerdown", (event) => beginNodeDrag(event, element));
  element.addEventListener("pointermove", moveNode);
  element.addEventListener("pointerup", endNodeDrag);
  element.addEventListener("pointercancel", endNodeDrag);
  element.addEventListener("keydown", (event) => moveNodeWithKeyboard(event, element));
});

document.querySelectorAll(".node-toggle").forEach((button) => button.addEventListener("click", (event) => {
  event.stopPropagation();
  const id = letterToId(button.closest(".map-node").dataset.peer);
  if (suppressedNodeClick.id === id && Date.now() < suppressedNodeClick.until) return;
  toggleNode(button.closest(".map-node").dataset.peer);
}));
document.querySelectorAll(".node-label").forEach((label) => label.addEventListener("click", () => {
  const letter = label.closest(".map-node").dataset.peer;
  const id = letterToId(letter);
  if (suppressedNodeClick.id === id && Date.now() < suppressedNodeClick.until) return;
  selectPeer(letter);
}));
document.querySelectorAll(".node-menu-button").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); openMenu(button.closest(".map-node"), button); }));
document.querySelectorAll(".network-link").forEach((link) => {
  link.addEventListener("click", () => toggleLink(link));
  link.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleLink(link); } });
});

menu.querySelector('[data-action="toggle"]').addEventListener("click", () => toggleNode(menuPeer));
menu.querySelector('[data-action="false-positive"]').addEventListener("click", () => injectEvent(true, menuPeer));
menu.querySelector('[data-action="details"]').addEventListener("click", () => { const peer = menuPeer; closeMenu(); selectPeer(peer); renderNodeDetails(peer); });
document.querySelector("#closeInspector").addEventListener("click", () => { document.querySelector("#nodeInspector").hidden = true; });
document.querySelectorAll(".playback-controls [data-speed]").forEach((button) => button.addEventListener("click", () => {
  playbackSpeed = Number(button.dataset.speed);
  document.querySelectorAll(".playback-controls [data-speed]").forEach((item) => item.classList.toggle("active", item === button));
  if (currentPacketAnimation) currentPacketAnimation.playbackRate = playbackSpeed;
  if (playbackPaused) {
    playbackPaused = false;
    document.querySelector("#pausePlayback").setAttribute("aria-pressed", "false");
    document.querySelector("#pausePlayback span").textContent = "Pausa";
    currentPacketAnimation?.play();
    runPlayback();
  }
}));
document.querySelector("#pausePlayback").addEventListener("click", () => {
  playbackPaused = !playbackPaused;
  document.querySelector("#pausePlayback").setAttribute("aria-pressed", String(playbackPaused));
  document.querySelector("#pausePlayback span").textContent = playbackPaused ? "Continuar" : "Pausa";
  if (playbackPaused) currentPacketAnimation?.pause();
  else { currentPacketAnimation?.play(); runPlayback(); }
});
eventButton.addEventListener("click", () => injectEvent(false));
document.querySelectorAll(".segmented button").forEach((button) => button.addEventListener("click", () => saveSettings(button.dataset.policy, systemState.settings.ttl, systemState.settings.required_votes)));
document.querySelector("#quorumDown").addEventListener("click", () => saveSettings("custom", systemState.settings.ttl, Math.max(0, Number(document.querySelector("#quorumValue").textContent) - 1)));
document.querySelector("#quorumUp").addEventListener("click", () => saveSettings("custom", systemState.settings.ttl, Math.min(activePeerIds().size, Number(document.querySelector("#quorumValue").textContent) + 1)));
document.querySelector("#runFalsePositives").addEventListener("click", () => injectEvent(true, selectedPeer, [...selectedFalsePositivePeers]));
document.querySelector("#ttlDown").addEventListener("click", () => saveSettings(systemState.settings.policy, Math.max(1, systemState.settings.ttl - 1)));
document.querySelector("#ttlUp").addEventListener("click", () => saveSettings(systemState.settings.policy, Math.min(20, systemState.settings.ttl + 1)));
document.querySelector("#clearActivity").textContent = "Actualizar";
document.querySelector("#clearActivity").addEventListener("click", () => { resetPlaybackActivity(); refreshSystem(); });

document.querySelector("#zoomIn").addEventListener("click", () => updateZoom(zoom + .15));
document.querySelector("#zoomOut").addEventListener("click", () => updateZoom(zoom - .15));
document.querySelector("#zoomReset").addEventListener("click", () => { panX = 0; panY = 0; updateZoom(1); });
document.querySelector("#resetLayout").addEventListener("click", resetLayout);
stage.addEventListener("wheel", (event) => {
  if (event.target.closest(".node-inspector")) return;
  event.preventDefault();
  updateZoom(zoom + (event.deltaY < 0 ? .1 : -.1));
}, { passive: false });
stage.addEventListener("pointerdown", (event) => {
  if (event.target.closest("button, .network-link, .node-inspector, .map-node, .gateway-node, a")) return;
  dragStart = { x: event.clientX - panX, y: event.clientY - panY };
  stage.classList.add("dragging");
  stage.setPointerCapture(event.pointerId);
});
document.querySelector("#nodeInspector").addEventListener("pointerdown", (event) => event.stopPropagation());
stage.addEventListener("pointermove", (event) => {
  if (!dragStart) return;
  panX = event.clientX - dragStart.x;
  panY = event.clientY - dragStart.y;
  updateZoom(zoom);
});
stage.addEventListener("pointerup", (event) => {
  dragStart = null;
  stage.classList.remove("dragging");
  if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
});
stage.addEventListener("pointercancel", () => { dragStart = null; stage.classList.remove("dragging"); });
document.addEventListener("click", (event) => { if (!menu.contains(event.target) && !event.target.closest(".node-menu-button")) closeMenu(); });

loadSavedLayout();
updateNetworkGeometry();
window.addEventListener("resize", updateNetworkGeometry);
refreshSystem();
setInterval(() => refreshSystem({ quiet: true }), 2000);
