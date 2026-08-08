// mind.js — Phase 8c, "Living Mind". A live 3D force-directed view of Jarvis's own tool
// registry, security-role taxonomy, and working memory (recent conversations + real
// semantic-similarity links from embeddings.py), fed by /ws/mind.
//
// Self-contained: three.js + OrbitControls are vendored locally (webapp/vendor/) — no CDN,
// consistent with the rest of this project's zero-external-runtime-dependency posture.

import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_RADIUS = { core: 2.2, hub: 1.1, tool: 0.5, memory: 0.55 };
const NODE_COLOR = {
  core: 0xe8e8f0,
  hub_general: 0x0a84ff,
  hub_defense: 0x30d158,
  hub_offense: 0xff453a,
  hub_practice: 0xbf5af2,
  hub_memory: 0xffd60a,
  memory: 0xffd60a,
};
const TIER_COLOR = {
  TIER_1: 0x8e8e99,
  TIER_2: 0x64d2ff,
  TIER_3: 0xffd60a,
  TIER_4: 0xff453a,
};

const EDGE_REST_LENGTH = { structural: 3.2, semantic: 5.5 };
const REPULSION = 18;
const CENTER_PULL = 0.015;
const DAMPING = 0.86;
const MAX_SPEED = 0.6;

// ---------------------------------------------------------------------------
// Three.js setup
// ---------------------------------------------------------------------------

const canvas = document.getElementById("scene");
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x05050a, 0.018);

const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 500);
camera.position.set(0, 6, 34);

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x05050a, 1);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 4;
controls.maxDistance = 140;

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Faint starfield backdrop so the scene doesn't feel like it's floating in a void.
(function addStarfield() {
  const count = 800;
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = 120 + Math.random() * 200;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const mat = new THREE.PointsMaterial({ color: 0x333344, size: 0.6, sizeAttenuation: true });
  scene.add(new THREE.Points(geo, mat));
})();

// Shared soft-glow sprite texture (radial gradient), tinted per-node via material.color.
const glowTexture = (function makeGlowTexture() {
  const size = 128;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.4, "rgba(255,255,255,0.35)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(c);
})();

const sphereGeo = new THREE.SphereGeometry(1, 16, 16);

// ---------------------------------------------------------------------------
// Graph state
// ---------------------------------------------------------------------------

const nodes = new Map();   // id -> { data, pos: THREE.Vector3, vel: THREE.Vector3, mesh, glow, pulse }
const edges = [];          // { source, target, type, weight, obj: THREE.Line }
let edgeGroup = new THREE.Group();
scene.add(edgeGroup);

function colorForNode(n) {
  if (n.type === "core") return NODE_COLOR.core;
  if (n.type === "hub") return NODE_COLOR["hub_" + n.role] ?? 0xffffff;
  if (n.type === "memory") return NODE_COLOR.memory;
  if (n.type === "tool") return TIER_COLOR[n.tier] ?? 0xffffff;
  return 0xffffff;
}

function initialPosition(n) {
  // Seed in rough layers so the force sim converges to something legible instead of
  // untangling from a single clumped point — core near center, hubs at a mid radius, tools
  // and memory nodes further out, randomized angularly in full 3D.
  let radius;
  if (n.type === "core") radius = 0;
  else if (n.type === "hub") radius = 7;
  else radius = 14 + Math.random() * 6;

  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  return new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta) * 0.6,  // flatten slightly — reads better from the default camera angle
    radius * Math.cos(phi)
  );
}

function addNode(n) {
  if (nodes.has(n.id)) {
    updateNode(n);
    return nodes.get(n.id);
  }
  const radius = NODE_RADIUS[n.type] ?? 0.5;
  const color = colorForNode(n);

  const material = new THREE.MeshBasicMaterial({ color });
  const mesh = new THREE.Mesh(sphereGeo, material);
  mesh.scale.setScalar(radius);
  mesh.userData.nodeId = n.id;
  scene.add(mesh);

  const glowMat = new THREE.SpriteMaterial({
    map: glowTexture, color, transparent: true, opacity: 0.55,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const glow = new THREE.Sprite(glowMat);
  glow.scale.setScalar(radius * 4.2);
  scene.add(glow);

  const pos = initialPosition(n);
  mesh.position.copy(pos);
  glow.position.copy(pos);

  const entry = { data: n, pos, vel: new THREE.Vector3(), mesh, glow, pulse: 0, baseRadius: radius };
  nodes.set(n.id, entry);
  return entry;
}

function updateNode(n) {
  const entry = nodes.get(n.id);
  if (!entry) return;
  entry.data = n;
}

function pulseNode(id, strength = 1.6) {
  const entry = nodes.get(id);
  if (entry) entry.pulse = strength;
}

function addEdge(e) {
  const key = e.source + "|" + e.target + "|" + e.type;
  if (edges.some((ex) => ex.key === key)) return;
  const color = e.type === "semantic" ? 0xffd60a : 0x3a3a48;
  const opacity = e.type === "semantic" ? 0.5 : 0.28;
  const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
  const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
  const line = new THREE.Line(geo, mat);
  edgeGroup.add(line);
  edges.push({ key, source: e.source, target: e.target, type: e.type, weight: e.weight, obj: line });
}

function applySnapshot(snapshot) {
  for (const n of snapshot.nodes) addNode(n);
  for (const e of snapshot.edges) addEdge(e);
  updateStats();
}

// ---------------------------------------------------------------------------
// Force simulation (brute-force O(n^2) — fine at this node count, see docstring)
// ---------------------------------------------------------------------------

function stepSimulation() {
  const entries = Array.from(nodes.values());

  // Repulsion between every pair.
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const a = entries[i], b = entries[j];
      const delta = new THREE.Vector3().subVectors(a.pos, b.pos);
      let dist = delta.length();
      if (dist < 0.01) { delta.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5); dist = 0.1; }
      const force = REPULSION / (dist * dist);
      delta.normalize().multiplyScalar(force);
      a.vel.add(delta);
      b.vel.sub(delta);
    }
  }

  // Spring attraction along edges.
  for (const e of edges) {
    const a = nodes.get(e.source), b = nodes.get(e.target);
    if (!a || !b) continue;
    const rest = e.type === "semantic"
      ? EDGE_REST_LENGTH.semantic * (1.3 - Math.min(e.weight ?? 0.8, 1))
      : EDGE_REST_LENGTH.structural;
    const delta = new THREE.Vector3().subVectors(b.pos, a.pos);
    const dist = Math.max(delta.length(), 0.01);
    const displacement = dist - rest;
    delta.normalize().multiplyScalar(displacement * 0.04);
    a.vel.add(delta);
    b.vel.sub(delta);
  }

  // Weak centering pull + integrate + damping.
  for (const n of entries) {
    n.vel.addScaledVector(n.pos, -CENTER_PULL);
    n.vel.multiplyScalar(DAMPING);
    if (n.vel.length() > MAX_SPEED) n.vel.setLength(MAX_SPEED);
    n.pos.add(n.vel);

    if (n.pulse > 0) n.pulse *= 0.90;
    const scale = n.baseRadius * (1 + n.pulse);
    n.mesh.position.copy(n.pos);
    n.mesh.scale.setScalar(scale);
    n.glow.position.copy(n.pos);
    n.glow.scale.setScalar(n.baseRadius * 4.2 * (1 + n.pulse * 0.8));
  }

  for (const e of edges) {
    const a = nodes.get(e.source), b = nodes.get(e.target);
    if (!a || !b) continue;
    const positions = e.obj.geometry.attributes.position;
    positions.setXYZ(0, a.pos.x, a.pos.y, a.pos.z);
    positions.setXYZ(1, b.pos.x, b.pos.y, b.pos.z);
    positions.needsUpdate = true;
  }
}

// ---------------------------------------------------------------------------
// Hover / click info panel
// ---------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
const infoPanel = document.getElementById("infoPanel");
const infoTitle = document.getElementById("infoTitle");
const infoBody = document.getElementById("infoBody");
let pinnedId = null;

function showInfo(entry) {
  const n = entry.data;
  infoTitle.textContent = n.label;
  let body = "";
  if (n.type === "tool") {
    body += `<span class="tag">Tier ${n.tier?.replace("TIER_", "")}</span><span class="tag">${n.role}</span>\n${n.description ?? ""}`;
  } else if (n.type === "memory") {
    body += `Conversation${n.updated_at ? " — updated " + new Date(n.updated_at * 1000).toLocaleString() : ""}`;
  } else if (n.type === "hub") {
    const count = edges.filter((e) => e.source === n.id).length;
    body += `${count} tool${count === 1 ? "" : "s"} in this specialist group`;
  } else if (n.type === "core") {
    body += `${nodes.size - 1} nodes tracked live`;
  }
  infoBody.innerHTML = body;
  infoPanel.classList.remove("hidden");
}

function hideInfo() {
  if (pinnedId) return;
  infoPanel.classList.add("hidden");
}

window.addEventListener("pointermove", (evt) => {
  mouse.x = (evt.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(evt.clientY / window.innerHeight) * 2 + 1;
});

window.addEventListener("click", (evt) => {
  if (evt.target.closest("#overlay")) return;
  raycaster.setFromCamera(mouse, camera);
  const meshes = Array.from(nodes.values()).map((e) => e.mesh);
  const hits = raycaster.intersectObjects(meshes);
  if (hits.length) {
    const id = hits[0].object.userData.nodeId;
    pinnedId = pinnedId === id ? null : id;
    if (pinnedId) showInfo(nodes.get(pinnedId));
    else hideInfo();
  } else {
    pinnedId = null;
    hideInfo();
  }
});

function updateHover() {
  if (pinnedId) return;
  raycaster.setFromCamera(mouse, camera);
  const meshes = Array.from(nodes.values()).map((e) => e.mesh);
  const hits = raycaster.intersectObjects(meshes);
  if (hits.length) {
    showInfo(nodes.get(hits[0].object.userData.nodeId));
  } else {
    hideInfo();
  }
}

// ---------------------------------------------------------------------------
// Stats readout
// ---------------------------------------------------------------------------

function updateStats() {
  const toolCount = Array.from(nodes.values()).filter((e) => e.data.type === "tool").length;
  const memCount = Array.from(nodes.values()).filter((e) => e.data.type === "memory").length;
  const semanticCount = edges.filter((e) => e.type === "semantic").length;
  document.getElementById("stats").innerHTML =
    `${toolCount} tools &middot; ${memCount} memories &middot; ${semanticCount} semantic links<br>` +
    `${nodes.size} nodes &middot; ${edges.length} edges`;
}

// ---------------------------------------------------------------------------
// WebSocket — same hello handshake as app.js, separate socket, read-only
// ---------------------------------------------------------------------------

const connStatusEl = document.getElementById("connStatus");
function setConnStatus(online, label) {
  connStatusEl.textContent = label || (online ? "live" : "offline");
  connStatusEl.className = "conn-status " + (online ? "online" : "offline");
}

let ws = null;
let reconnectDelay = 1000;

async function boot() {
  let cfg;
  try {
    const resp = await fetch("/gui-config");
    cfg = await resp.json();
  } catch (e) {
    setConnStatus(false, "Could not reach server");
    return;
  }
  connect(cfg.device_id, cfg.token);
}

function connect(deviceId, token) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/mind`);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", device_id: deviceId, token }));
    setConnStatus(true, "live");
    reconnectDelay = 1000;
  };

  ws.onclose = () => {
    setConnStatus(false, "reconnecting…");
    setTimeout(() => connect(deviceId, token), reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === "snapshot") {
      applySnapshot(msg);
    } else if (msg.type === "tool_fired") {
      for (const toolName of msg.tools || []) pulseNode("tool:" + toolName);
    } else if (msg.type === "memory_pulse") {
      const id = "conv:" + msg.conversation_id;
      if (!nodes.has(id)) {
        addNode({ id, type: "memory", label: msg.title || "New conversation", conversation_id: msg.conversation_id });
        addEdge({ source: "hub:memory", target: id, type: "structural" });
        updateStats();
      }
      pulseNode(id, 2.2);
    }
  };
}

document.getElementById("refreshBtn").addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "refresh" }));
});

boot();

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

function animate() {
  requestAnimationFrame(animate);
  stepSimulation();
  updateHover();
  controls.update();
  renderer.render(scene, camera);
}
animate();
