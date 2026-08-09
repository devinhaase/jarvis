// dashboard.js — Overseer/team dashboard, standalone page (see dashboard.html, server.py's
// /dashboard route). Talks to the same /ws protocol app.js does — get_overseer_snapshot,
// get_team_dashboard, approve, list_skills/approve_skill/reject_skill/disable_skill/
// clear_skill_flag, and the live broadcasts (team_status_changed, approval_request/
// resolved, skill_updated, cross_team_incident) — just from its own connection, opened
// with hello's dashboard_only:true so it never creates a phantom conversation (see
// server.py's /ws handler). This file is a deliberate near-verbatim port of app.js's old
// Phase 7 dashboard section (render functions, event wiring) plus its skills-modal
// section — same functions, same markup/classes, just relocated to their own page so a
// new chat in the main GUI no longer opens with the dashboard showing.

const $ = (sel) => document.querySelector(sel);

const els = {
  overseerView: $("#overseerView"),
  teamDashboardView: $("#teamDashboardView"),
  teamTiles: $("#teamTiles"),
  overseerQueue: $("#overseerQueue"),
  crossTeamLog: $("#crossTeamLog"),
  systemHealthStrip: $("#systemHealthStrip"),
  vpnStatusBadge: $("#vpnStatusBadge"),
  backToOverseerBtn: $("#backToOverseerBtn"),
  teamDashTitle: $("#teamDashTitle"),
  teamDashStatus: $("#teamDashStatus"),
  teamActivityFeed: $("#teamActivityFeed"),
  teamQueue: $("#teamQueue"),
  teamHistory: $("#teamHistory"),
  teamHealthBlock: $("#teamHealthBlock"),
  connStatus: $("#connStatus"),
  skillsModal: $("#skillsModal"),
  skillsCloseBtn: $("#skillsCloseBtn"),
  skillsBody: $("#skillsBody"),
};

const state = {
  ws: null,
  deviceId: null,
  token: null,
  reconnectDelay: 1000,
  dashboardTeam: null,   // team key currently drilled into, or null when on the Overseer view
  skills: [],
};

const TEAM_LABEL = {
  personal_assistant: "Personal Assistant", network: "Network", it: "IT",
  cybersecurity: "Cybersecurity", hacking: "Hacking",
};
const TEAM_ICON = {
  personal_assistant: "🗂", network: "📡", it: "🖥", cybersecurity: "🛡", hacking: "🎯",
};

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

async function boot() {
  try {
    const resp = await fetch("/gui-config");
    const cfg = await resp.json();
    state.deviceId = cfg.device_id;
    state.token = cfg.token;
  } catch (e) {
    setConnStatus(false, "Could not reach server");
    return;
  }
  connect();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: "hello",
      device_id: state.deviceId,
      token: state.token,
      capabilities: ["filesystem"],
      dashboard_only: true,
    }));
  };

  ws.onclose = () => {
    setConnStatus(false, "Reconnecting…");
    setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 10000);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (evt) => handleServerMessage(JSON.parse(evt.data));
}

function setConnStatus(online, label) {
  els.connStatus.textContent = label || (online ? "connected" : "offline");
  els.connStatus.className = "conn-status " + (online ? "online" : "offline");
}

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

function relTime(ts) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return new Date(ts * 1000).toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Server -> client message handling
// ---------------------------------------------------------------------------

function handleServerMessage(msg) {
  switch (msg.type) {
    case "error":
      setConnStatus(false, msg.message || "error");
      break;

    case "ready":
      state.reconnectDelay = 1000;
      setConnStatus(true);
      if (!msg.brain_available) {
        setConnStatus(true, "connected (AI brain unavailable — check server .env)");
      }
      refreshCurrentView();
      break;

    case "overseer_snapshot":
      renderOverseerSnapshot(msg);
      break;

    case "team_dashboard":
      renderTeamDashboard(msg);
      break;

    case "team_status_changed":
      // Instant, in-place tile/pill update — no round trip needed for something this
      // cheap, same reasoning as app.js's integration dots' own live-toggle pattern.
      patchTeamStatus(msg.team, msg.status, msg.detail);
      break;

    case "cross_team_incident":
      if (!state.dashboardTeam) refreshCurrentView();
      break;

    case "approval_request":
    case "approval_resolved":
      refreshCurrentView();
      break;

    case "skill_list":
      state.skills = msg.skills;
      renderSkillsModal(state.skills);
      break;

    case "skill_updated":
      if (state.skills) {
        const i = state.skills.findIndex(s => s.name === msg.skill.name);
        if (i >= 0) state.skills[i] = msg.skill; else state.skills.push(msg.skill);
        renderSkillsModal(state.skills);
      } else {
        state.skills = [msg.skill];
      }
      refreshCurrentView();  // a proposed/reviewed skill can change the queue
      break;
  }
}

// ---------------------------------------------------------------------------
// Overseer / team dashboard — near-verbatim port of app.js's Phase 7 section
// ---------------------------------------------------------------------------

function showOverseer() {
  state.dashboardTeam = null;
  els.overseerView.classList.remove("hidden");
  els.teamDashboardView.classList.add("hidden");
  send({ type: "get_overseer_snapshot" });
}

function showTeamDashboard(teamKey) {
  state.dashboardTeam = teamKey;
  els.overseerView.classList.add("hidden");
  els.teamDashboardView.classList.remove("hidden");
  send({ type: "get_team_dashboard", team: teamKey });
}

els.backToOverseerBtn.addEventListener("click", showOverseer);

// Re-fetches whichever sub-view is actually on screen — the single place every live-update
// trigger (approval_request/resolved, skill_updated, cross_team_incident) funnels through,
// so none of them need to know which sub-view is currently open.
function refreshCurrentView() {
  if (state.dashboardTeam) send({ type: "get_team_dashboard", team: state.dashboardTeam });
  else send({ type: "get_overseer_snapshot" });
}

function renderOverseerSnapshot(snap) {
  renderTeamTiles(snap.teams || []);
  renderQueue(els.overseerQueue, snap.queue || [], true);
  renderCrossTeamLog(snap.cross_team_log || []);
  renderSystemHealth(snap.integration || {}, snap.vpn || {});
}

function renderTeamTiles(teams) {
  els.teamTiles.innerHTML = "";
  for (const t of teams) {
    const tile = document.createElement("div");
    tile.className = `team-tile status-${t.status}`;
    tile.dataset.team = t.key;

    const icon = document.createElement("div");
    icon.className = "tile-icon";
    icon.textContent = TEAM_ICON[t.key] || "🤖";
    const name = document.createElement("div");
    name.className = "tile-name";
    name.textContent = t.name;
    const status = document.createElement("div");
    status.className = "tile-status";
    status.textContent = t.status;

    tile.appendChild(icon);
    tile.appendChild(name);
    tile.appendChild(status);
    tile.addEventListener("click", () => showTeamDashboard(t.key));
    els.teamTiles.appendChild(tile);
  }
}

function patchTeamStatus(teamKey, status, detail) {
  const tile = els.teamTiles.querySelector(`.team-tile[data-team="${teamKey}"]`);
  if (tile) {
    tile.className = `team-tile status-${status}`;
    const statusEl = tile.querySelector(".tile-status");
    if (statusEl) statusEl.textContent = status;
  }
  if (state.dashboardTeam === teamKey) {
    els.teamDashStatus.textContent = status;
    els.teamDashStatus.className = "team-status-pill status-" + status;
  }
}

function renderQueue(container, items, showTeamLabel) {
  container.innerHTML = "";
  if (!items || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "queue-empty";
    empty.textContent = "Nothing waiting.";
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "queue-item";

    const badge = document.createElement("span");
    if (item.kind === "approval") {
      badge.className = "queue-badge " + (item.tier === "TIER_4" ? "tier-4" : "tier-3");
      badge.textContent = item.tier === "TIER_4" ? "TIER 4" : "TIER 3";
    } else {
      badge.className = "queue-badge skill";
      badge.textContent = "SKILL";
    }
    row.appendChild(badge);

    const title = document.createElement("span");
    title.className = "queue-title";
    title.textContent = item.title;
    row.appendChild(title);

    if (showTeamLabel) {
      const team = document.createElement("span");
      team.className = "queue-team";
      team.textContent = `${TEAM_ICON[item.team] || "🤖"} ${TEAM_LABEL[item.team] || item.team || "—"}`;
      row.appendChild(team);
    }

    const actions = document.createElement("span");
    actions.className = "queue-actions";
    if (item.kind === "approval") {
      const approve = document.createElement("button");
      approve.className = "approve";
      approve.textContent = "Approve";
      approve.addEventListener("click", () => send({ type: "approve", approval_id: item.id, approved: true }));
      const deny = document.createElement("button");
      deny.className = "deny";
      deny.textContent = "Deny";
      deny.addEventListener("click", () => send({ type: "approve", approval_id: item.id, approved: false }));
      actions.appendChild(approve);
      actions.appendChild(deny);
    } else {
      const review = document.createElement("button");
      review.className = "approve";
      review.textContent = "Review";
      review.addEventListener("click", () => {
        send({ type: "list_skills" });
        els.skillsModal.classList.remove("hidden");
      });
      actions.appendChild(review);
    }
    row.appendChild(actions);
    container.appendChild(row);
  }
}

function renderCrossTeamLog(incidents) {
  els.crossTeamLog.innerHTML = "";
  if (!incidents || incidents.length === 0) {
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = "No cross-team activity yet.";
    els.crossTeamLog.appendChild(empty);
    return;
  }
  for (const inc of incidents) {
    const row = document.createElement("div");
    row.className = "log-entry";
    const link = document.createElement("div");
    link.className = "log-link";
    link.textContent = `${TEAM_ICON[inc.created_by_team] || "🤖"} ${TEAM_LABEL[inc.created_by_team] || inc.created_by_team || "?"} `
      + `→ ${TEAM_ICON[inc.target_team] || "🤖"} ${TEAM_LABEL[inc.target_team] || inc.target_team}`;
    const title = document.createElement("div");
    title.className = "log-title";
    title.textContent = `${inc.title} (${inc.status})`;
    row.appendChild(link);
    row.appendChild(title);
    els.crossTeamLog.appendChild(row);
  }
}

function renderSystemHealth(integration, vpn) {
  els.systemHealthStrip.innerHTML = "";
  const items = [
    ["Obsidian", integration.obsidian], ["Gmail", integration.gmail],
    ["Calendar", integration.calendar], ["Drive", integration.drive],
  ];
  for (const [label, ok] of items) {
    const chip = document.createElement("span");
    chip.className = "health-chip " + (ok ? "ok" : "warn");
    chip.textContent = `${ok ? "●" : "○"} ${label}`;
    els.systemHealthStrip.appendChild(chip);
  }
  if (vpn && vpn.detected) {
    els.vpnStatusBadge.textContent = vpn.running ? `🔒 Tailscale connected${vpn.hostname ? " (" + vpn.hostname + ")" : ""}` : "🔒 Tailscale installed, not running";
    els.vpnStatusBadge.className = "vpn-status-badge" + (vpn.running ? " ok" : "");
  } else {
    els.vpnStatusBadge.className = "vpn-status-badge hidden";
  }
}

function renderTeamDashboard(dash) {
  if (dash.error) return;
  els.teamDashTitle.textContent = `${TEAM_ICON[dash.key] || "🤖"} ${dash.name}`;
  els.teamDashStatus.textContent = dash.status;
  els.teamDashStatus.className = "team-status-pill status-" + dash.status;

  els.teamActivityFeed.innerHTML = "";
  if (dash.status === "idle") {
    const empty = document.createElement("div");
    empty.className = "feed-empty";
    empty.textContent = "Idle — nothing running right now.";
    els.teamActivityFeed.appendChild(empty);
  } else {
    const entry = document.createElement("div");
    entry.className = "feed-entry";
    const time = document.createElement("span");
    time.className = "feed-time";
    time.textContent = relTime(dash.since);
    entry.appendChild(time);
    entry.appendChild(document.createTextNode(dash.detail || dash.status));
    els.teamActivityFeed.appendChild(entry);
  }

  const combinedQueue = [
    ...(dash.queue || []).map(q => ({ ...q, kind: "approval" })),
    ...(dash.proposed_skills || []).map(s => ({ kind: "skill", id: s.name, title: s.name, team: s.team, tier: s.tier })),
  ];
  renderQueue(els.teamQueue, combinedQueue, false);

  els.teamHistory.innerHTML = "";
  if (!dash.history || dash.history.length === 0) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No recorded actions yet.";
    els.teamHistory.appendChild(empty);
  } else {
    for (const ep of dash.history) {
      const row = document.createElement("div");
      row.className = "history-entry outcome-" + (ep.outcome || "success");
      const dot = document.createElement("span");
      dot.className = "outcome-dot";
      const action = document.createElement("span");
      action.className = "history-action";
      action.textContent = ep.action;
      const time = document.createElement("span");
      time.className = "history-time";
      time.textContent = relTime(ep.timestamp);
      row.appendChild(dot);
      row.appendChild(action);
      row.appendChild(time);
      els.teamHistory.appendChild(row);
    }
  }

  renderTeamHealthBlock(dash.key, dash.health || {}, dash.incidents || []);
}

function renderTeamHealthBlock(teamKey, health, incidents) {
  const parts = [];
  if (teamKey === "hacking") {
    const total = (health.authorized_hosts || []).length + (health.authorized_domains || []).length + (health.authorized_networks || []).length;
    parts.push(`${total} authorized target(s)`);
  } else if (teamKey === "cybersecurity") {
    parts.push(`${health.open_alerts || 0} open alert(s)`);
  }
  if (incidents.length) parts.push(`${incidents.length} open incident(s) targeting this team`);
  els.teamHealthBlock.textContent = parts.length ? parts.join(" · ") : "No dedicated health checks for this team yet.";
}

// ---------------------------------------------------------------------------
// Skill review queue — ported from app.js so a queue item's "Review" button works
// standalone on this page (see dashboard.html's comment on the modal).
// ---------------------------------------------------------------------------

els.skillsCloseBtn.addEventListener("click", () => els.skillsModal.classList.add("hidden"));

const SKILL_STATUS_ORDER = ["proposed", "active", "disabled", "rejected"];
const SKILL_STATUS_LABEL = {
  proposed: "PROPOSED — awaiting your review", active: "ACTIVE",
  disabled: "DISABLED", rejected: "REJECTED",
};

function renderSkillsModal(skillList) {
  els.skillsBody.innerHTML = "";
  if (!skillList || skillList.length === 0) {
    const empty = document.createElement("div");
    empty.className = "tool-desc";
    empty.textContent = "No skills yet — Jarvis proposes one after a multi-step task that looks reusable.";
    els.skillsBody.appendChild(empty);
    return;
  }

  for (const statusKey of SKILL_STATUS_ORDER) {
    const group = skillList.filter(s => s.status === statusKey);
    if (group.length === 0) continue;

    const heading = document.createElement("div");
    heading.className = "tool-group-title";
    heading.textContent = SKILL_STATUS_LABEL[statusKey];
    els.skillsBody.appendChild(heading);

    for (const skill of group) {
      const entry = document.createElement("div");
      entry.className = "tool-entry";

      const nameRow = document.createElement("div");
      const name = document.createElement("span");
      name.className = "tool-name";
      name.textContent = skill.name + (skill.flagged ? " ⚠" : "");
      const tier = document.createElement("span");
      tier.className = "tool-tier";
      tier.textContent = `${skill.tier} · v${skill.version}`;
      nameRow.appendChild(name);
      nameRow.appendChild(tier);

      const desc = document.createElement("div");
      desc.className = "tool-desc";
      desc.textContent = `${skill.when_to_use} — steps: ${skill.steps.join(" -> ")} — tools: ${skill.tools.join(", ")}`;

      const stats = document.createElement("div");
      stats.className = "tool-desc";
      stats.textContent = `${skill.success_count} succeeded, ${skill.fail_count} failed` +
        (skill.flagged ? " — flagged for review (failure rate spiked; still active until you decide)" : "");

      entry.appendChild(nameRow);
      entry.appendChild(desc);
      entry.appendChild(stats);

      const actions = document.createElement("div");
      actions.className = "skill-actions";
      if (skill.status === "proposed") {
        actions.appendChild(skillActionBtn("Approve", () => send({ type: "approve_skill", name: skill.name })));
        actions.appendChild(skillActionBtn("Reject", () => send({ type: "reject_skill", name: skill.name })));
      } else if (skill.status === "active") {
        if (skill.flagged) {
          actions.appendChild(skillActionBtn("Clear flag", () => send({ type: "clear_skill_flag", name: skill.name })));
        }
        actions.appendChild(skillActionBtn("Disable", () => send({ type: "disable_skill", name: skill.name })));
      }
      if (actions.childElementCount > 0) entry.appendChild(actions);

      els.skillsBody.appendChild(entry);
    }
  }
}

function skillActionBtn(label, onClick) {
  const btn = document.createElement("button");
  btn.className = "text-btn small";
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

boot();

// ---------------------------------------------------------------------------
// Notification deep-linking — registers the same service worker index.html does (its
// registration scope is the whole origin, so this just makes sure a browser that only ever
// opens /dashboard directly still has it) and handles both ways a tap can reach this page:
// an already-open tab gets postMessage'd by sw.js's notificationclick handler; a fresh tab
// gets opened straight to /dashboard?team=<key> and reads it here on load.
// ---------------------------------------------------------------------------

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data && event.data.type === "open_dashboard_team" && event.data.team) {
      openTeamOnceReady(event.data.team);
    }
  });
}

function openTeamOnceReady(teamKey) {
  const tryOpen = () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      showTeamDashboard(teamKey);
    } else {
      setTimeout(tryOpen, 300);
    }
  };
  tryOpen();
}

(function _handleDeepLinkOnLoad() {
  const params = new URLSearchParams(window.location.search);
  const team = params.get("team");
  if (!team) return;
  openTeamOnceReady(team);
  history.replaceState(null, "", window.location.pathname);
})();
