// Jarvis web GUI — vanilla JS, no build step, no framework. Talks to server.py over the
// same /ws protocol every other client (main.py --server, voice_companion.py) speaks, so
// there's exactly one source of truth for what a "turn" is, not a GUI-specific dialect.

const $ = (sel) => document.querySelector(sel);

const els = {
  main: $("#main"),
  sidebar: $("#sidebar"),
  sidebarToggle: $("#sidebarToggle"),
  sidebarOverlay: $("#sidebarOverlay"),
  newConvBtn: $("#newConvBtn"),
  themeToggleBtn: $("#themeToggleBtn"),
  pushSettingsBtn: $("#pushSettingsBtn"),
  pushModal: $("#pushModal"),
  pushCloseBtn: $("#pushCloseBtn"),
  pushEnabledToggle: $("#pushEnabledToggle"),
  pushCategoriesSection: $("#pushCategoriesSection"),
  pushCatApprovals: $("#pushCatApprovals"),
  pushCatAlerts: $("#pushCatAlerts"),
  pushCatBriefing: $("#pushCatBriefing"),
  pushCatMessages: $("#pushCatMessages"),
  pushCatNetworkAnomaly: $("#pushCatNetworkAnomaly"),
  pushCatNewDevice: $("#pushCatNewDevice"),
  pushCatFailedLogins: $("#pushCatFailedLogins"),
  pushCatBackupFailure: $("#pushCatBackupFailure"),
  pushCatInfrastructureDown: $("#pushCatInfrastructureDown"),
  pushQuietEnabled: $("#pushQuietEnabled"),
  pushQuietStart: $("#pushQuietStart"),
  pushQuietEnd: $("#pushQuietEnd"),
  pushTestBtn: $("#pushTestBtn"),
  searchInput: $("#searchInput"),
  convList: $("#convList"),
  convSectionLabel: $("#convSectionLabel"),
  clearProjectFilterBtn: $("#clearProjectFilterBtn"),
  projectList: $("#projectList"),
  newProjectBtn: $("#newProjectBtn"),
  connStatus: $("#connStatus"),
  convTitle: $("#convTitle"),
  projectBadge: $("#projectBadge"),
  listeningIndicator: $("#listeningIndicator"),
  messages: $("#messages"),
  attachmentBar: $("#attachmentBar"),
  composer: $("#composer"),
  composerInput: $("#composerInput"),
  sendBtn: $("#sendBtn"),
  attachBtn: $("#attachBtn"),
  attachFolderBtn: $("#attachFolderBtn"),
  fileInput: $("#fileInput"),
  folderInput: $("#folderInput"),
  micBtn: $("#micBtn"),
  toolsBtn: $("#toolsBtn"),
  toolsModal: $("#toolsModal"),
  toolsCloseBtn: $("#toolsCloseBtn"),
  toolsBody: $("#toolsBody"),
  skillsBtn: $("#skillsBtn"),
  skillsModal: $("#skillsModal"),
  skillsCloseBtn: $("#skillsCloseBtn"),
  skillsBody: $("#skillsBody"),
  teamsBtn: $("#teamsBtn"),
  teamsModal: $("#teamsModal"),
  teamsCloseBtn: $("#teamsCloseBtn"),
  teamsBody: $("#teamsBody"),
  projectModal: $("#projectModal"),
  projectModalCloseBtn: $("#projectModalCloseBtn"),
  projectNameInput: $("#projectNameInput"),
  projectCreateBtn: $("#projectCreateBtn"),
  moveModal: $("#moveModal"),
  moveModalCloseBtn: $("#moveModalCloseBtn"),
  moveModalList: $("#moveModalList"),
};

const state = {
  ws: null,
  deviceId: null,
  token: null,
  conversationId: localStorage.getItem("jarvis_conversation_id") || null,
  currentProjectId: null,   // project of the conversation currently open, if any
  conversations: [],
  projects: [],
  projectFilter: null,      // sidebar filter — show only this project's conversations
  attachments: [],          // files visible to the current conversation
  streamingEl: null,
  streamingText: "",
  reconnectDelay: 1000,
  searchMode: false,
  searchResults: [],
  pendingVoiceOrigin: false,  // composer text about to be sent came from dictation
  awaitingVoiceReply: false,  // speak the next assistant reply aloud
  wakeArmed: false,   // mic is open, hands-free "hey Jarvis" listening loop is running
  wakeBusy: false,    // wake loop pauses while true — set right before a wake-triggered sendMessage, cleared once the spoken reply finishes (see speakAloud)
  skills: [],
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
      conversation_id: state.conversationId,
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
      state.conversationId = msg.conversation_id;
      state.currentProjectId = msg.project_id || null;
      localStorage.setItem("jarvis_conversation_id", state.conversationId);
      if (!msg.brain_available) {
        setConnStatus(true, "connected (AI brain unavailable — check server .env)");
      }
      refreshProjectList();
      refreshConversationList();
      loadConversationMessages(state.conversationId);
      send({ type: "get_integration_status" });
      send({ type: "list_skills" });
      break;

    case "conversation_list":
      state.conversations = msg.conversations;
      renderConvList();
      break;

    case "search_results":
      state.searchResults = msg.results;
      renderConvList();
      break;

    case "conversation_list_changed":
      refreshConversationList();
      refreshProjectList();
      break;

    case "conversation_opened":
      state.conversationId = msg.conversation_id;
      state.currentProjectId = msg.project_id || null;
      localStorage.setItem("jarvis_conversation_id", state.conversationId);
      els.convTitle.textContent = msg.title || "New conversation";
      renderProjectBadge();
      renderMessages(msg.messages || []);
      highlightActiveConv();
      refreshFileList();
      break;

    case "conversation_renamed":
      updateConvTitleEverywhere(msg.conversation_id, msg.title);
      break;

    case "conversation_deleted":
      state.conversations = state.conversations.filter(c => c.id !== msg.conversation_id);
      renderConvList();
      if (msg.conversation_id === state.conversationId) {
        newConversation();
      }
      break;

    case "project_list":
      state.projects = msg.projects;
      renderProjectList();
      renderMoveModalList();
      break;

    case "project_created":
      closeProjectModal();
      break;

    case "file_list":
      if (msg.conversation_id === state.conversationId) {
        state.attachments = msg.files;
        renderAttachmentBar();
      }
      break;

    case "file_uploaded":
      if (msg.file && (!state.attachments.some(f => f.id === msg.file.id))) {
        state.attachments.push(msg.file);
        renderAttachmentBar();
      }
      break;

    case "file_deleted":
      state.attachments = state.attachments.filter(f => f.id !== msg.file_id);
      renderAttachmentBar();
      break;

    case "tool_list":
      renderToolsModal(msg.tools);
      break;

    case "team_list":
      renderTeamsModal(msg.teams);
      break;

    case "skill_list":
      state.skills = msg.skills;
      renderSkillsModal(state.skills);
      updateSkillsPendingBadge();
      break;

    case "skill_updated":
      if (state.skills) {
        const i = state.skills.findIndex(s => s.name === msg.skill.name);
        if (i >= 0) state.skills[i] = msg.skill; else state.skills.push(msg.skill);
        renderSkillsModal(state.skills);
      } else {
        state.skills = [msg.skill];
      }
      updateSkillsPendingBadge();
      break;

    case "integration_status":
    case "integration_status_changed":
      renderIntegrationStatus(msg);
      break;

    case "push_settings":
      renderPushSettings(msg);
      break;

    case "push_test_result":
      els.pushTestBtn.textContent = msg.sent ? "Sent — check for it" : "Not sent (see notes below)";
      setTimeout(() => { els.pushTestBtn.textContent = "Send test notification"; }, 3000);
      break;

    case "user_message":
      // Another device (or another tab) talking in the conversation we're watching — the
      // server never echoes this back to whichever socket sent it, so no dedup needed here;
      // our own sends are already rendered optimistically in sendMessage().
      if (msg.conversation_id === state.conversationId) {
        appendUserBubble(msg.text, msg.source);
      }
      break;

    case "stream_chunk":
      if (msg.conversation_id === state.conversationId) appendStreamChunk(msg.text);
      break;

    case "round_end":
      if (msg.conversation_id === state.conversationId && msg.tools_ran && msg.tools_ran.length) {
        appendToolNote(`Used: ${msg.tools_ran.join(", ")}`);
      }
      break;

    case "tool_status":
      if (msg.conversation_id !== state.conversationId) break;
      if (msg.event === "tools_starting") {
        appendToolNote(`Running ${msg.data.join(", ")}…`);
      } else if (msg.event === "team_routing") {
        const icons = msg.data.teams.map(k => TEAM_ICON[k] || "🤖").join("");
        const names = msg.data.teams.map(k => TEAM_LABEL[k] || k).join(" → ");
        const how = msg.data.explicit ? "asked directly" : "routed";
        appendToolNote(`${icons} ${names} (${how})`, "team-note");
        // Phase 6 item 5: remembered so the NEXT assistant bubble (about to start
        // streaming) gets a persistent badge, not just this transient note — the note
        // scrolls away, the badge stays attached to the message itself.
        state.pendingTeamBadge = { teams: msg.data.teams };
      } else if (msg.event === "team_active") {
        appendToolNote(`${TEAM_ICON[msg.data.team] || "🤖"} ${TEAM_LABEL[msg.data.team] || msg.data.team} team working…`, "team-note");
      } else if (msg.event === "team_handoff_gate") {
        const verdict = msg.data.approved ? "approved" : "not approved";
        appendToolNote(`⚠ Cybersecurity → Hacking handoff ${verdict}`, "team-note");
      }
      break;

    case "stream_end":
      if (msg.conversation_id === state.conversationId) finishStream(msg);
      refreshConversationList();
      if (msg.full_text) notifyIfHidden("Jarvis", msg.full_text);
      break;

    case "voice_status":
      if (msg.conversation_id === state.conversationId) {
        els.listeningIndicator.classList.toggle("hidden", msg.state !== "listening");
      }
      break;

    case "approval_request":
      showApprovalBanner(msg);
      break;
  }
}

// ---------------------------------------------------------------------------
// Conversation list (sidebar)
// ---------------------------------------------------------------------------

function refreshConversationList() {
  send({ type: "list_conversations", project_id: state.projectFilter || undefined });
}

function relTime(ts) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return new Date(ts * 1000).toLocaleDateString();
}

function renderConvList() {
  els.convList.innerHTML = "";
  const items = state.searchMode ? state.searchResults : state.conversations;

  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "tool-note";
    empty.style.padding = "10px";
    empty.textContent = state.searchMode ? "No matches." : "No conversations yet.";
    els.convList.appendChild(empty);
    return;
  }

  for (const conv of items) {
    const row = document.createElement("div");
    row.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");
    row.dataset.id = conv.id;

    const left = document.createElement("div");
    left.style.overflow = "hidden";
    left.style.flex = "1";
    const titleEl = document.createElement("div");
    titleEl.className = "conv-title";
    titleEl.textContent = conv.title || "New conversation";
    left.appendChild(titleEl);
    if (conv.snippet) {
      const snip = document.createElement("span");
      snip.className = "snippet";
      snip.innerHTML = conv.snippet.replace(/</g, "&lt;");
      left.appendChild(snip);
    }
    row.appendChild(left);

    if (!state.searchMode) {
      const time = document.createElement("span");
      time.className = "conv-time";
      time.textContent = relTime(conv.updated_at);
      row.appendChild(time);
    }

    const actions = document.createElement("div");
    actions.className = "conv-actions";
    const moveBtn = document.createElement("button");
    moveBtn.textContent = "📁";
    moveBtn.title = "Move to project";
    moveBtn.onclick = (e) => { e.stopPropagation(); openMoveModal(conv.id); };
    const renameBtn = document.createElement("button");
    renameBtn.textContent = "✎";
    renameBtn.title = "Rename";
    renameBtn.onclick = (e) => { e.stopPropagation(); renameConversation(conv.id, conv.title); };
    const delBtn = document.createElement("button");
    delBtn.textContent = "🗑";
    delBtn.title = "Delete";
    delBtn.onclick = (e) => { e.stopPropagation(); deleteConversation(conv.id); };
    actions.appendChild(moveBtn);
    actions.appendChild(renameBtn);
    actions.appendChild(delBtn);
    row.appendChild(actions);

    row.onclick = () => openConversation(conv.id);
    els.convList.appendChild(row);
  }
}

function highlightActiveConv() {
  document.querySelectorAll(".conv-item").forEach(el => {
    el.classList.toggle("active", el.dataset.id === state.conversationId);
  });
}

function updateConvTitleEverywhere(conversationId, title) {
  const conv = state.conversations.find(c => c.id === conversationId);
  if (conv) conv.title = title;
  if (conversationId === state.conversationId) els.convTitle.textContent = title;
  renderConvList();
}

// ---------------------------------------------------------------------------
// Conversation actions
// ---------------------------------------------------------------------------

function newConversation() {
  send({ type: "new_conversation", project_id: state.projectFilter || undefined });
  closeSidebarOnMobile();
}

function openConversation(id) {
  if (id === state.conversationId) { closeSidebarOnMobile(); return; }
  send({ type: "open_conversation", conversation_id: id });
  closeSidebarOnMobile();
}

function renameConversation(id, currentTitle) {
  const title = prompt("Rename conversation:", currentTitle || "");
  if (title && title.trim()) {
    send({ type: "rename_conversation", conversation_id: id, title: title.trim() });
  }
}

function deleteConversation(id) {
  if (!confirm("Delete this conversation? This can't be undone.")) return;
  send({ type: "delete_conversation", conversation_id: id });
}

function loadConversationMessages(id) {
  send({ type: "open_conversation", conversation_id: id });
}

// ---------------------------------------------------------------------------
// Projects (sidebar folders)
// ---------------------------------------------------------------------------

function refreshProjectList() {
  send({ type: "list_projects" });
}

function renderProjectList() {
  els.projectList.innerHTML = "";
  if (state.projects.length === 0) {
    const empty = document.createElement("div");
    empty.className = "project-empty";
    empty.textContent = "No projects yet.";
    els.projectList.appendChild(empty);
  }
  for (const proj of state.projects) {
    const row = document.createElement("div");
    row.className = "project-item" + (proj.id === state.projectFilter ? " active" : "");

    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = proj.name;
    row.appendChild(name);

    const count = document.createElement("span");
    count.className = "project-count";
    count.textContent = proj.conversation_count;
    row.appendChild(count);

    const actions = document.createElement("div");
    actions.className = "project-actions";
    const renameBtn = document.createElement("button");
    renameBtn.textContent = "✎";
    renameBtn.title = "Rename project";
    renameBtn.onclick = (e) => {
      e.stopPropagation();
      const name2 = prompt("Rename project:", proj.name);
      if (name2 && name2.trim()) send({ type: "rename_project", project_id: proj.id, name: name2.trim() });
    };
    const delBtn = document.createElement("button");
    delBtn.textContent = "🗑";
    delBtn.title = "Delete project (keeps its chats)";
    delBtn.onclick = (e) => {
      e.stopPropagation();
      if (confirm(`Delete project "${proj.name}"? Its conversations stay, just ungrouped.`)) {
        send({ type: "delete_project", project_id: proj.id });
        if (state.projectFilter === proj.id) clearProjectFilter();
      }
    };
    actions.appendChild(renameBtn);
    actions.appendChild(delBtn);
    row.appendChild(actions);

    row.onclick = () => setProjectFilter(proj.id);
    els.projectList.appendChild(row);
  }
}

function setProjectFilter(projectId) {
  state.projectFilter = projectId;
  const proj = state.projects.find(p => p.id === projectId);
  els.convSectionLabel.textContent = (proj ? proj.name.toUpperCase() : "PROJECT");
  els.clearProjectFilterBtn.classList.remove("hidden");
  renderProjectList();
  refreshConversationList();
  closeSidebarOnMobile();
}

function clearProjectFilter() {
  state.projectFilter = null;
  els.convSectionLabel.textContent = "ALL CONVERSATIONS";
  els.clearProjectFilterBtn.classList.add("hidden");
  renderProjectList();
  refreshConversationList();
}

function renderProjectBadge() {
  const proj = state.projects.find(p => p.id === state.currentProjectId);
  if (proj) {
    els.projectBadge.textContent = "📁 " + proj.name;
    els.projectBadge.classList.remove("hidden");
  } else {
    els.projectBadge.classList.add("hidden");
  }
}

function openMoveModal(conversationId) {
  els.moveModal.dataset.conversationId = conversationId;
  els.moveModal.classList.remove("hidden");
  renderMoveModalList();
}

function renderMoveModalList() {
  if (els.moveModal.classList.contains("hidden")) return;
  const conversationId = els.moveModal.dataset.conversationId;
  if (!conversationId) return;
  const conv = state.conversations.find(c => c.id === conversationId) ||
               (state.searchMode ? state.searchResults.find(c => c.id === conversationId) : null);
  const currentProject = conv ? conv.project_id : undefined;

  els.moveModalList.innerHTML = "";
  const noneRow = document.createElement("div");
  noneRow.className = "move-item" + (!currentProject ? " active" : "");
  noneRow.textContent = "No project";
  noneRow.onclick = () => moveConversationToProject(conversationId, null);
  els.moveModalList.appendChild(noneRow);

  for (const proj of state.projects) {
    const row = document.createElement("div");
    row.className = "move-item" + (currentProject === proj.id ? " active" : "");
    row.textContent = proj.name;
    row.onclick = () => moveConversationToProject(conversationId, proj.id);
    els.moveModalList.appendChild(row);
  }
}

function moveConversationToProject(conversationId, projectId) {
  send({ type: "set_conversation_project", conversation_id: conversationId, project_id: projectId });
  // The server's conversation_list_changed broadcast refreshes the sidebar, but it doesn't
  // say *which* conversation changed — if this is the one currently open, its header badge
  // would otherwise sit stale until the next full reopen. Update it here immediately.
  if (conversationId === state.conversationId) {
    state.currentProjectId = projectId;
    renderProjectBadge();
  }
  closeMoveModal();
}

function closeMoveModal() {
  els.moveModal.classList.add("hidden");
  delete els.moveModal.dataset.conversationId;
}

function openProjectModal() {
  els.projectNameInput.value = "";
  els.projectModal.classList.remove("hidden");
  els.projectNameInput.focus();
}
function closeProjectModal() {
  els.projectModal.classList.add("hidden");
}

els.newProjectBtn.addEventListener("click", openProjectModal);
els.projectModalCloseBtn.addEventListener("click", closeProjectModal);
els.projectCreateBtn.addEventListener("click", () => {
  const name = els.projectNameInput.value.trim();
  if (name) send({ type: "create_project", name });
});
els.projectNameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); els.projectCreateBtn.click(); }
});
els.clearProjectFilterBtn.addEventListener("click", clearProjectFilter);
els.moveModalCloseBtn.addEventListener("click", closeMoveModal);

// ---------------------------------------------------------------------------
// Tools panel
// ---------------------------------------------------------------------------

els.toolsBtn.addEventListener("click", () => {
  send({ type: "list_tools" });
  els.toolsModal.classList.remove("hidden");
});
els.toolsCloseBtn.addEventListener("click", () => els.toolsModal.classList.add("hidden"));

// snake_case tool name -> "Title Case" for display — the raw function name (e.g.
// "add_reference_material") is what the model calls internally, never something a person
// reading the Tools panel should have to parse themselves.
function humanizeToolName(name) {
  return name.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function renderToolsModal(tools) {
  els.toolsBody.innerHTML = "";
  let lastGroup = null;
  for (const tool of tools) {
    const group = tool.role || "general";
    if (group !== lastGroup) {
      const heading = document.createElement("div");
      heading.className = "tool-group-title";
      // Friendly team name (e.g. "Personal Assistant"), not the raw role key
      // ("personal_assistant") or "GENERAL" for coordinator-level tools with no team.
      heading.textContent = group === "general" ? "AVAILABLE TO EVERY TEAM" : (TEAM_LABEL[group] || group).toUpperCase();
      els.toolsBody.appendChild(heading);
      lastGroup = group;
    }
    const entry = document.createElement("div");
    entry.className = "tool-entry";
    const nameRow = document.createElement("div");
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = humanizeToolName(tool.name);
    const tier = document.createElement("span");
    tier.className = "tool-tier";
    tier.textContent = tool.tier;  // already a plain-English label from the server
    nameRow.appendChild(name);
    nameRow.appendChild(tier);
    const desc = document.createElement("div");
    desc.className = "tool-desc";
    desc.textContent = tool.description;
    entry.appendChild(nameRow);
    entry.appendChild(desc);
    els.toolsBody.appendChild(entry);
  }
}

// ---------------------------------------------------------------------------
// Teams (Phase 4) — a static reference panel (mirrors Tools) plus live routing notes
// rendered inline in the chat itself (see the tool_status handler above) so you can
// actually see which agent handled a given request, not just look it up after the fact.
// ---------------------------------------------------------------------------

const TEAM_LABEL = {
  personal_assistant: "Personal Assistant", network: "Network", it: "IT",
  cybersecurity: "Cybersecurity", hacking: "Hacking",
};
const TEAM_ICON = {
  personal_assistant: "🗂", network: "📡", it: "🖥", cybersecurity: "🛡", hacking: "🎯",
};

els.teamsBtn.addEventListener("click", () => {
  send({ type: "list_teams" });
  els.teamsModal.classList.remove("hidden");
});
els.teamsCloseBtn.addEventListener("click", () => els.teamsModal.classList.add("hidden"));

function renderTeamsModal(teamList) {
  els.teamsBody.innerHTML = "";
  for (const team of teamList) {
    const entry = document.createElement("div");
    entry.className = "tool-entry";

    const nameRow = document.createElement("div");
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = `${TEAM_ICON[team.key] || "🤖"} ${team.name}`;
    const count = document.createElement("span");
    count.className = "tool-tier";
    count.textContent = `${team.tool_count} tools`;
    nameRow.appendChild(name);
    nameRow.appendChild(count);

    const desc = document.createElement("div");
    desc.className = "tool-desc";
    desc.textContent = team.description;  // plain-English summary, not the raw LLM prompt

    const addr = document.createElement("div");
    addr.className = "tool-desc";
    addr.textContent = `Address directly with: "ask the ${team.aliases[0]}..."`;

    entry.appendChild(nameRow);
    entry.appendChild(desc);
    entry.appendChild(addr);
    els.teamsBody.appendChild(entry);
  }
}

// ---------------------------------------------------------------------------
// Integration status panel (Phase 6 item 5, made live + 5-state in Phase 8 section 6) —
// 7 dots in the sidebar footer: Obsidian/Gmail/Calendar/Drive (Phase 6) plus
// Twingate/Firewall/NAS (Phase 8). Each dot's data-state is one of "connected" |
// "degraded" | "disconnected" | "error" | "loading" — "loading" is the one state the
// server never sends (see server.py's STATE_* constants); it's just what every dot shows
// from page load until the first real integration_status/integration_status_changed
// message arrives, so the panel never silently shows stale/wrong info in the gap.
// Fetched once on "ready" (see handleServerMessage) AND kept live afterward — the server
// now runs a background check loop and broadcasts integration_status_changed only when a
// state actually flips, so Firewall/NAS/Twingate (genuinely variable now that the brain
// travels — see task.md's Phase 8 entry) update without needing a page reload.
// ---------------------------------------------------------------------------

const INTEGRATION_STATE_LABEL = {
  connected: "connected", degraded: "degraded — partially working", disconnected: "not connected",
  error: "error checking status", loading: "checking…",
};
const INTEGRATION_DOT_TITLE = {
  statusDotObsidian: "Obsidian vault", statusDotGmail: "Gmail", statusDotCalendar: "Calendar",
  statusDotDrive: "Drive", statusDotTwingate: "Twingate", statusDotFirewall: "Firewall",
  statusDotNas: "NAS",
};

function renderIntegrationStatus(status) {
  const dots = {
    statusDotObsidian: status.obsidian, statusDotGmail: status.gmail,
    statusDotCalendar: status.calendar, statusDotDrive: status.drive,
    statusDotTwingate: status.twingate, statusDotFirewall: status.firewall,
    statusDotNas: status.nas,
  };
  for (const [id, state] of Object.entries(dots)) {
    const el = document.getElementById(id);
    if (!el || !state) continue;  // undefined = this dot's key wasn't in the payload; leave it alone
    const previousState = el.dataset.state;
    const changed = previousState !== state;
    el.dataset.state = state;
    el.title = `${INTEGRATION_DOT_TITLE[id]}: ${INTEGRATION_STATE_LABEL[state] || state}`;
    // HUD instruments should announce a real change, not sit static — a quick slide+fade
    // "fly-in" whenever a dot's state actually flips (not on every re-render of the same
    // state, and not for the initial loading->first-real-status transition, which is just
    // the page settling in, not an event worth calling out).
    if (changed && state !== "loading" && previousState !== "loading") {
      el.classList.remove("hud-flyin");
      void el.offsetWidth;  // restart the animation even if it's still mid-run from a prior flip
      el.classList.add("hud-flyin");
    }
  }
}

// ---------------------------------------------------------------------------
// Notification settings modal (Phase 6 item 6) — the toggle drives the actual
// subscribe/unsubscribe flow (enablePushNotifications/disablePushNotifications, defined
// above near the service worker registration); everything below just reflects and edits
// server-held prefs (categories, quiet hours) for whichever device_id this browser is.
// ---------------------------------------------------------------------------

els.pushSettingsBtn.addEventListener("click", () => {
  send({ type: "get_push_settings" });
  els.pushModal.classList.remove("hidden");
});
els.pushCloseBtn.addEventListener("click", () => els.pushModal.classList.add("hidden"));

function renderPushSettings(settings) {
  els.pushEnabledToggle.checked = !!settings.subscribed;
  els.pushCategoriesSection.classList.toggle("hidden", !settings.subscribed);
  const cats = settings.categories || {};
  els.pushCatApprovals.checked = cats.approvals !== false;
  els.pushCatAlerts.checked = cats.alerts !== false;
  els.pushCatBriefing.checked = cats.briefing !== false;
  els.pushCatMessages.checked = !!cats.messages;
  els.pushCatNetworkAnomaly.checked = cats.network_anomaly !== false;
  els.pushCatNewDevice.checked = cats.new_device !== false;
  els.pushCatFailedLogins.checked = cats.failed_logins !== false;
  els.pushCatBackupFailure.checked = cats.backup_failure !== false;
  els.pushCatInfrastructureDown.checked = cats.infrastructure_down !== false;
  const qh = settings.quiet_hours || {};
  els.pushQuietEnabled.checked = !!qh.enabled;
  els.pushQuietStart.value = qh.start || "22:00";
  els.pushQuietEnd.value = qh.end || "07:00";
}

els.pushEnabledToggle.addEventListener("change", async () => {
  if (els.pushEnabledToggle.checked) {
    const ok = await enablePushNotifications();
    if (!ok) els.pushEnabledToggle.checked = false;  // permission denied or unsupported
    else send({ type: "get_push_settings" });
  } else {
    await disablePushNotifications();
    els.pushCategoriesSection.classList.add("hidden");
  }
});

function _sendCategoryUpdate() {
  send({
    type: "set_push_settings",
    categories: {
      approvals: els.pushCatApprovals.checked,
      alerts: els.pushCatAlerts.checked,
      briefing: els.pushCatBriefing.checked,
      messages: els.pushCatMessages.checked,
      network_anomaly: els.pushCatNetworkAnomaly.checked,
      new_device: els.pushCatNewDevice.checked,
      failed_logins: els.pushCatFailedLogins.checked,
      backup_failure: els.pushCatBackupFailure.checked,
      infrastructure_down: els.pushCatInfrastructureDown.checked,
    },
  });
}
for (const el of [
  els.pushCatApprovals, els.pushCatAlerts, els.pushCatBriefing, els.pushCatMessages,
  els.pushCatNetworkAnomaly, els.pushCatNewDevice, els.pushCatFailedLogins, els.pushCatBackupFailure,
  els.pushCatInfrastructureDown,
]) {
  el.addEventListener("change", _sendCategoryUpdate);
}

function _sendQuietHoursUpdate() {
  send({
    type: "set_push_settings",
    quiet_hours: {
      enabled: els.pushQuietEnabled.checked,
      start: els.pushQuietStart.value || "22:00",
      end: els.pushQuietEnd.value || "07:00",
    },
  });
}
for (const el of [els.pushQuietEnabled, els.pushQuietStart, els.pushQuietEnd]) {
  el.addEventListener("change", _sendQuietHoursUpdate);
}

els.pushTestBtn.addEventListener("click", () => {
  els.pushTestBtn.textContent = "Sending…";
  send({ type: "test_push_notification" });
});

// ---------------------------------------------------------------------------
// Skill review queue (Phase 5) — proposed skills need Devin's explicit approve/edit/
// reject before they're eligible to run; this panel is the only client-facing path to
// those actions, mirroring the server's own "only a connected device can trigger this"
// trust model. Grouped by status so the review queue (proposed) is visually distinct
// from what's already active, flagged, or set aside.
// ---------------------------------------------------------------------------

els.skillsBtn.addEventListener("click", () => {
  send({ type: "list_skills" });
  els.skillsModal.classList.remove("hidden");
});
els.skillsCloseBtn.addEventListener("click", () => els.skillsModal.classList.add("hidden"));

function updateSkillsPendingBadge() {
  const badge = document.getElementById("skillsPendingBadge");
  if (!badge) return;
  const n = (state.skills || []).filter(s => s.status === "proposed").length;
  badge.textContent = String(n);
  badge.classList.toggle("hidden", n === 0);
}

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
      tier.textContent = `${skill.tier} · v${skill.version}`;  // tier already a plain-English label from the server
      nameRow.appendChild(name);
      nameRow.appendChild(tier);

      const desc = document.createElement("div");
      desc.className = "tool-desc";
      // Dropped the old raw "— tools: move_or_rename_path, run_local_script" trailer —
      // internal function names, redundant with steps below. A generated-script path
      // (data\synthesized_tools\...\.py, from a "synth:" skill) is collapsed to a plain
      // phrase for the same reason: a file path on disk means nothing to read in a list,
      // it's not something you'd click here anyway.
      const readableSteps = skill.steps
        .map(s => s.replace(/[\w:\\\/.]*synthesized_tools[\w:\\\/.]*\.py/i, "a script Jarvis wrote and reviewed"))
        .join(" → ");
      desc.textContent = `${skill.when_to_use} — steps: ${readableSteps}`;

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

// ---------------------------------------------------------------------------
// File attachments
// ---------------------------------------------------------------------------

function refreshFileList() {
  send({ type: "list_files", conversation_id: state.conversationId });
}

function renderAttachmentBar() {
  els.attachmentBar.innerHTML = "";
  els.attachmentBar.classList.toggle("hidden", state.attachments.length === 0);
  for (const f of state.attachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip" + (f.extractable ? "" : " not-extractable");
    const label = document.createElement("span");
    label.textContent = (f.extractable ? "📄 " : "📦 ") + f.filename + (f.truncated ? " (truncated)" : "");
    chip.appendChild(label);
    const remove = document.createElement("button");
    remove.className = "chip-remove";
    remove.textContent = "✕";
    remove.title = "Remove attachment";
    remove.onclick = () => send({ type: "delete_file", file_id: f.id });
    chip.appendChild(remove);
    els.attachmentBar.appendChild(chip);
  }
}

async function uploadFiles(fileList) {
  // Snapshot into a plain array immediately — fileList is a *live* reference to the file
  // input's own .files. The caller resets input.value = "" right after kicking this off
  // (so picking the same file twice still fires 'change'), and iterating a live host
  // collection across that reset is exactly the kind of thing that behaves inconsistently
  // between browsers. A plain array can't be invalidated out from under this loop.
  const files = Array.from(fileList);
  for (const file of files) {
    const name = file.webkitRelativePath || file.name;
    const formData = new FormData();
    formData.append("conversation_id", state.conversationId);
    formData.append("file", file, name);
    try {
      const resp = await fetch("/upload", { method: "POST", body: formData });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        appendToolNote(`Couldn't attach ${name}: ${err.error || resp.statusText}`);
        continue;
      }
      const meta = await resp.json();
      if (!state.attachments.some(f => f.id === meta.id)) {
        state.attachments.push(meta);
      }
    } catch (e) {
      appendToolNote(`Couldn't attach ${name}: ${e}`);
    }
  }
  renderAttachmentBar();
}

els.attachBtn.addEventListener("click", () => els.fileInput.click());
els.attachFolderBtn.addEventListener("click", () => els.folderInput.click());
els.fileInput.addEventListener("change", async () => {
  if (els.fileInput.files.length) await uploadFiles(els.fileInput.files);
  els.fileInput.value = "";
});
els.folderInput.addEventListener("change", async () => {
  if (els.folderInput.files.length) await uploadFiles(els.folderInput.files);
  els.folderInput.value = "";
});

// ---------------------------------------------------------------------------
// Messages / streaming render
// ---------------------------------------------------------------------------

function renderMessages(messages) {
  els.messages.innerHTML = "";
  state.streamingEl = null;
  state.streamingText = "";
  if (messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Say something.";
    els.messages.appendChild(empty);
    return;
  }
  for (const m of messages) {
    if (m.role === "user") appendUserBubble(m.content, m.source, false);
    else appendAssistantBubble(m.content, false);
  }
  scrollToBottom();
}

function clearEmptyState() {
  const empty = els.messages.querySelector(".empty-state");
  if (empty) empty.remove();
}

function appendUserBubble(text, source) {
  clearEmptyState();
  const row = document.createElement("div");
  row.className = "msg-row user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  els.messages.appendChild(row);
  if (source === "voice") {
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.style.textAlign = "right";
    meta.textContent = "via voice";
    els.messages.appendChild(meta);
  }
  scrollToBottom();
}

function appendAssistantBubble(text, isLive = true) {
  clearEmptyState();
  // isLive=false means this is a historical reload (renderMessages) — team routing is
  // only tracked for the live session, not persisted per-message, so a reloaded
  // conversation deliberately shows no badge rather than a stale or wrong one. A real
  // scope boundary, not an oversight: persisting per-message team attribution would need
  // a schema change this pass didn't need to make.
  if (isLive) _consumePendingTeamBadge();
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  els.messages.appendChild(row);
  scrollToBottom();
}

function appendToolNote(text, extraClass) {
  clearEmptyState();
  const note = document.createElement("div");
  note.className = extraClass ? `tool-note ${extraClass}` : "tool-note";
  note.textContent = text;
  els.messages.appendChild(note);
  scrollToBottom();
}

function _consumePendingTeamBadge() {
  // A separate block-level element preceding the message row, same flat-list-of-elements
  // pattern appendToolNote() already uses, rather than nested inside the row's flex layout
  // (which is flex-direction: row — a badge inserted there would sit beside the bubble,
  // not above it).
  if (!state.pendingTeamBadge) return;
  clearEmptyState();
  const badge = document.createElement("div");
  badge.className = "team-badge";
  badge.style.marginLeft = "24px";
  badge.textContent = state.pendingTeamBadge.teams
    .map(k => `${TEAM_ICON[k] || "🤖"} ${TEAM_LABEL[k] || k}`)
    .join(" → ");
  els.messages.appendChild(badge);
  state.pendingTeamBadge = null;
}

function appendStreamChunk(text) {
  clearEmptyState();
  if (!state.streamingEl) {
    _consumePendingTeamBadge();
    const row = document.createElement("div");
    row.className = "msg-row assistant";
    const bubble = document.createElement("div");
    bubble.className = "bubble streaming";
    row.appendChild(bubble);
    els.messages.appendChild(row);
    state.streamingEl = bubble;
    state.streamingText = "";
  }
  state.streamingText += text;
  state.streamingEl.textContent = state.streamingText;
  scrollToBottom();
}

function finishStream(msg) {
  if (state.streamingEl) {
    state.streamingEl.classList.remove("streaming");
    state.streamingEl.textContent = msg.full_text || state.streamingText;
  } else if (msg.full_text) {
    // No chunks arrived (e.g. every round was pure tool calls) but there's a final answer.
    appendAssistantBubble(msg.full_text);
  }
  state.streamingEl = null;
  state.streamingText = "";

  for (const d of (msg.denied || [])) {
    const note = document.createElement("div");
    note.className = "denied-note";
    note.textContent = `refused: ${d.tool} needs '${d.required}' on the server`;
    els.messages.appendChild(note);
  }
  scrollToBottom();

  if (state.awaitingVoiceReply) {
    state.awaitingVoiceReply = false;
    speakAloud(msg.full_text);
  }
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

function sendMessage(text) {
  if (!text.trim()) return;
  const source = state.pendingVoiceOrigin ? "voice" : "text";
  state.pendingVoiceOrigin = false;
  if (source === "voice") state.awaitingVoiceReply = true;
  appendUserBubble(text, source);
  send({ type: "message", text, source });
}

els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.composerInput.value;
  els.composerInput.value = "";
  els.composerInput.style.height = "auto";
  sendMessage(text);
});

els.composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
  // Any manual keystroke means whatever's in the box is no longer purely dictated text.
  state.pendingVoiceOrigin = false;
});

els.composerInput.addEventListener("input", () => {
  els.composerInput.style.height = "auto";
  els.composerInput.style.height = Math.min(els.composerInput.scrollHeight, 160) + "px";
});

// ---------------------------------------------------------------------------
// Conversation title editing (click the header title to rename)
// ---------------------------------------------------------------------------

els.convTitle.addEventListener("blur", () => {
  const title = els.convTitle.textContent.trim();
  if (title && state.conversationId) {
    send({ type: "rename_conversation", conversation_id: state.conversationId, title });
  }
});
els.convTitle.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); els.convTitle.blur(); }
});

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

let searchDebounce = null;
state.semanticSearch = false;

function runSearch() {
  const q = els.searchInput.value.trim();
  if (!q) {
    state.searchMode = false;
    renderConvList();
    return;
  }
  state.searchMode = true;
  send({ type: state.semanticSearch ? "semantic_search" : "search_conversations", query: q });
}

els.searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(runSearch, 250);
});

const semanticToggleEl = document.getElementById("semanticToggle");
semanticToggleEl.addEventListener("click", () => {
  state.semanticSearch = !state.semanticSearch;
  semanticToggleEl.classList.toggle("active", state.semanticSearch);
  semanticToggleEl.title = state.semanticSearch
    ? "Semantic search ON (searching by meaning) — click for keyword search"
    : "Toggle semantic (meaning-based) search";
  if (els.searchInput.value.trim()) runSearch();
});

// ---------------------------------------------------------------------------
// Approval prompts (Tier-3/4 tool confirmations)
// ---------------------------------------------------------------------------

function showApprovalBanner(msg) {
  const banner = document.createElement("div");
  banner.className = "approval-banner";
  const label = document.createElement("span");
  label.textContent = `[${msg.tier}] Server wants to run '${msg.action}'.`;
  banner.appendChild(label);

  const btns = document.createElement("span");
  const approve = document.createElement("button");
  approve.className = "approve";
  approve.textContent = "Approve";
  approve.onclick = () => {
    send({ type: "approve", approval_id: msg.approval_id, approved: true });
    banner.remove();
  };
  const deny = document.createElement("button");
  deny.className = "deny";
  deny.textContent = "Deny";
  deny.onclick = () => {
    send({ type: "approve", approval_id: msg.approval_id, approved: false });
    banner.remove();
  };
  btns.appendChild(approve);
  btns.appendChild(deny);
  banner.appendChild(btns);

  els.messages.appendChild(banner);
  scrollToBottom();

  if (msg.tier === "TIER_3") {
    // Matches the CLI's own semantics — Tier 3 is "notify, then act unless cancelled".
    setTimeout(() => { if (banner.isConnected) { approve.click(); } }, 3000);
  }
}

// ---------------------------------------------------------------------------
// Voice input — hands-free wake word ("hey Jarvis"), Web Audio API capture (local mic,
// resampled to raw PCM16/16kHz in the browser), posted to /transcribe, which reuses the
// same local Vosk engine voice_companion.py's wake-word listener uses. Nothing here is
// cloud STT — no Web Speech API, deliberately, so this stays consistent with the rest of
// this app's zero-API-key voice story. TTS playback of the reply uses the browser's own
// built-in speechSynthesis — also fully local, nothing sent anywhere, just the system
// voice reading text out loud.
//
// Click the mic once to arm: it opens the mic and keeps it open, alternating between two
// phases, until you click it again to disarm:
//   1. Listening for the wake word — ~2.5s rolling windows, each transcribed and checked
//      for "jarvis" appearing anywhere in it.
//   2. Heard it — capture the actual command, auto-stopping on ~1.1s of trailing silence
//      (or a 12s hard cap), then send it exactly like a normal typed message and speak the
//      reply back (same awaitingVoiceReply mechanism a typed message never touches).
//
// Real, deliberate limitation (documented, not hidden — same honesty as redact()/the
// prompt-injection detector elsewhere in this project): this is disjoint-window "does the
// word appear in this transcript" wake detection, not a real streaming wake-word model like
// voice.py's openWakeWord. A phrase spoken right at a window boundary can get split across
// two windows and missed in both — carrying a short audio tail into the next window (below)
// softens that but doesn't eliminate it. voice_companion.py's real wake-word engine remains
// the more reliable choice for "always want to be heard"; this is the browser-tab
// equivalent, with the trade-offs a browser tab actually has (a live mic stays open the
// whole time the tab is armed — that's the cost of hands-free here, not a bug).
// ---------------------------------------------------------------------------

let wakeAudioCtx = null;
let wakeMicStream = null;
let wakeMicSource = null;
let wakeMicProcessor = null;
let wakeSilentGain = null;

const WAKE_WORD_WINDOW_MS = 2500;
const WAKE_TAIL_CARRY_MS = 600;     // audio carried from the end of one window into the next, so a phrase spoken right at the boundary isn't split and lost in both halves
const COMMAND_SILENCE_MS = 1100;    // trailing quiet required after real speech before a command is considered finished
const COMMAND_MAX_MS = 12000;       // hard cap regardless of silence, so a stuck/noisy mic can't listen forever
const VOICE_RMS_THRESHOLD = 0.02;   // rough "someone is actually talking" floor for the silence detector

async function armWakeWord() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendToolNote("This browser doesn't support microphone capture.");
    return;
  }
  try {
    wakeMicStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    appendToolNote("Microphone access was denied or is unavailable.");
    return;
  }
  wakeAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  wakeMicSource = wakeAudioCtx.createMediaStreamSource(wakeMicStream);
  wakeMicProcessor = wakeAudioCtx.createScriptProcessor(4096, 1, 1);

  // ScriptProcessorNode only fires onaudioprocess while connected through to the
  // destination in some browsers — route through a silent gain so nothing is actually
  // heard (no mic-to-speaker feedback) while still satisfying that requirement.
  wakeSilentGain = wakeAudioCtx.createGain();
  wakeSilentGain.gain.value = 0;
  wakeMicSource.connect(wakeMicProcessor);
  wakeMicProcessor.connect(wakeSilentGain);
  wakeSilentGain.connect(wakeAudioCtx.destination);

  state.wakeArmed = true;
  els.micBtn.classList.add("wake-armed");
  els.micBtn.title = 'Listening for "hey Jarvis" — click to stop';
  send({ type: "voice_status", state: "listening" });
  _wakeLoop();
}

function disarmWakeWord() {
  if (!state.wakeArmed) return;
  state.wakeArmed = false;
  els.micBtn.classList.remove("wake-armed", "wake-capturing");
  els.micBtn.title = "Voice input";
  send({ type: "voice_status", state: "idle" });

  try { wakeMicProcessor.disconnect(); } catch (e) {}
  try { wakeSilentGain.disconnect(); } catch (e) {}
  try { wakeMicSource.disconnect(); } catch (e) {}
  if (wakeMicStream) wakeMicStream.getTracks().forEach(t => t.stop());
  if (wakeAudioCtx) wakeAudioCtx.close();
  wakeAudioCtx = null; wakeMicStream = null; wakeMicSource = null; wakeMicProcessor = null; wakeSilentGain = null;
}

function _sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function _rms(buf) {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

// Records from the already-open wake mic session, calling `shouldStop(chunk, elapsedMs)`
// after every ~4096-sample callback, until it returns true (or the mic gets disarmed out
// from under it). `prefixChunks` (optional) is prepended before the first real chunk —
// used to carry the wake-word window's audio tail into the next window.
function _recordUntil(shouldStop, prefixChunks) {
  return new Promise((resolve) => {
    const chunks = prefixChunks ? [...prefixChunks] : [];
    const startedAt = performance.now();
    wakeMicProcessor.onaudioprocess = (e) => {
      if (!state.wakeArmed) { wakeMicProcessor.onaudioprocess = null; resolve({ chunks: null, tail: null }); return; }
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      chunks.push(data);
      if (shouldStop(data, performance.now() - startedAt)) {
        wakeMicProcessor.onaudioprocess = null;
        resolve({ chunks, tail: data });
      }
    };
  });
}

async function _transcribeChunks(chunks) {
  if (!chunks || !chunks.length) return "";
  const pcm16 = floatTo16BitPCM(downsampleTo16k(mergeFloat32(chunks), wakeAudioCtx.sampleRate));
  const blob = new Blob([pcm16], { type: "application/octet-stream" });
  const formData = new FormData();
  formData.append("audio", blob, "clip.raw");
  try {
    const resp = await fetch("/transcribe", { method: "POST", body: formData });
    const data = await resp.json();
    return data.text || "";
  } catch (e) {
    return "";
  }
}

async function _wakeLoop() {
  let carryTail = null;
  while (state.wakeArmed) {
    // Don't listen while Jarvis is speaking the last reply — see speakAloud for the normal
    // release path. Capped at 30s so a dropped connection/missed stream_end (speakAloud
    // never gets called at all in that case) can't wedge the wake loop shut forever;
    // disarming/rearming already resets it too, this just means you don't have to.
    let waited = 0;
    while (state.wakeArmed && state.wakeBusy && waited < 30000) { await _sleep(150); waited += 150; }
    state.wakeBusy = false;
    if (!state.wakeArmed) break;

    // Phase 1: poll for the wake word.
    els.micBtn.classList.remove("wake-capturing");
    const { chunks, tail } = await _recordUntil((_, elapsed) => elapsed > WAKE_WORD_WINDOW_MS, carryTail);
    carryTail = tail ? [tail] : null;  // only ever carry the single most recent callback's worth (~85ms @ 48kHz/4096) is too little on its own, so keep accumulating below
    if (!state.wakeArmed || !chunks) break;
    const heard = await _transcribeChunks(chunks);
    if (!state.wakeArmed) break;
    if (!heard || !heard.toLowerCase().includes("jarvis")) continue;

    // Phase 2: wake word heard — capture the actual command, auto-stopping on trailing
    // silence (same "notify, then act" reasoning this project already applies to Tier-3
    // tool approvals — give it a moment, don't demand an explicit end signal).
    els.micBtn.classList.add("wake-capturing");
    appendToolNote('🎙️ Heard "Jarvis" — listening…', "team-note");
    let spoke = false;
    let silenceSince = null;
    const captured = await _recordUntil((chunk, elapsed) => {
      const level = _rms(chunk);
      if (level > VOICE_RMS_THRESHOLD) { spoke = true; silenceSince = null; }
      else if (spoke && silenceSince === null) { silenceSince = performance.now(); }
      const quietFor = silenceSince !== null ? performance.now() - silenceSince : 0;
      return elapsed > COMMAND_MAX_MS || (spoke && quietFor > COMMAND_SILENCE_MS);
    });
    els.micBtn.classList.remove("wake-capturing");
    if (!state.wakeArmed || !captured.chunks) break;
    const text = await _transcribeChunks(captured.chunks);
    if (!state.wakeArmed) break;
    if (text && text.trim()) {
      state.pendingVoiceOrigin = true;
      state.wakeBusy = true;  // hold off the next listening window until the reply's been spoken (see speakAloud)
      sendMessage(text);
    } else {
      appendToolNote("(didn't catch a command after the wake word)");
    }
    carryTail = null;  // a command capture just ran long past any short-window carry being meaningful
  }
}

els.micBtn.addEventListener("click", () => {
  if (state.wakeArmed) disarmWakeWord();
  else armWakeWord();
});

function mergeFloat32(chunks) {
  let length = 0;
  for (const c of chunks) length += c.length;
  const result = new Float32Array(length);
  let offset = 0;
  for (const c of chunks) { result.set(c, offset); offset += c.length; }
  return result;
}

function downsampleTo16k(buffer, inputSampleRate) {
  const targetRate = 16000;
  if (inputSampleRate === targetRate) return buffer;
  const ratio = inputSampleRate / targetRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < newLength) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0, count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  for (let i = 0, offset = 0; i < float32Array.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

// `wakeBusy` (set by _wakeLoop right before sendMessage on a wake-triggered command) is
// cleared here, on the utterance actually finishing — not on a fixed timer — so the next
// wake-word listening window doesn't open while Jarvis's own reply is still playing out of
// the speakers and at real risk of the mic hearing itself (echoCancellation on the mic
// stream helps too, but this closes the gap between "reply text arrived" and "TTS actually
// started/finished speaking it", which a fixed delay can't know either side of).
function speakAloud(text) {
  if (!text || !("speechSynthesis" in window)) { state.wakeBusy = false; return; }
  try {
    window.speechSynthesis.cancel();  // don't stack replies if one's already speaking
    const utter = new SpeechSynthesisUtterance(text);
    utter.onend = () => { state.wakeBusy = false; };
    utter.onerror = () => { state.wakeBusy = false; };
    window.speechSynthesis.speak(utter);
  } catch (e) {
    state.wakeBusy = false;  // best-effort — silence isn't worth surfacing an error for, but the wake loop still needs releasing
  }
}

// ---------------------------------------------------------------------------
// Mobile sidebar toggle
// ---------------------------------------------------------------------------

els.sidebarToggle.addEventListener("click", () => {
  els.sidebar.classList.toggle("open");
  els.sidebarOverlay.style.display = els.sidebar.classList.contains("open") ? "block" : "none";
});
els.sidebarOverlay.addEventListener("click", closeSidebarOnMobile);

function closeSidebarOnMobile() {
  els.sidebar.classList.remove("open");
  els.sidebarOverlay.style.display = "none";
}

els.newConvBtn.addEventListener("click", newConversation);

// ---------------------------------------------------------------------------
// Theme toggle (Phase 6 item 5) — cycles light/dark/system. "system" is the absence of
// a data-theme attribute (style.css's prefers-color-scheme media query decides); the two
// explicit states override it. Persisted so a reload doesn't flash back to system default.
// ---------------------------------------------------------------------------

const THEME_CYCLE = ["system", "dark", "light"];
const THEME_ICON = { system: "🌗", dark: "🌙", light: "☀" };

function applyTheme(theme) {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
  els.themeToggleBtn.textContent = THEME_ICON[theme] || THEME_ICON.system;
  els.themeToggleBtn.title = `Theme: ${theme} (click to change)`;
}

applyTheme(localStorage.getItem("jarvis_theme") || "system");

els.themeToggleBtn.addEventListener("click", () => {
  const current = localStorage.getItem("jarvis_theme") || "system";
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(current) + 1) % THEME_CYCLE.length];
  localStorage.setItem("jarvis_theme", next);
  applyTheme(next);
});

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

document.getElementById("exportBtn").addEventListener("click", () => {
  if (!state.conversationId) return;
  const a = document.createElement("a");
  a.href = `/export/${state.conversationId}`;
  a.download = "";  // filename comes from the server's Content-Disposition header
  document.body.appendChild(a);
  a.click();
  a.remove();
});

// ---------------------------------------------------------------------------
// Browser notifications + PWA installability
// ---------------------------------------------------------------------------
// Two independent layers, deliberately kept separate:
//  - notifyIfHidden(): foreground-only, plain Notification API — needs the tab still open
//    (just not focused). Unchanged from before this item.
//  - Phase 6 item 6, below: real Web Push via the service worker's push subscription —
//    reaches a device even with every tab and the browser itself closed. This is what makes
//    a phone notification possible at all; notifyIfHidden alone never could.

if ("Notification" in window && Notification.permission === "default") {
  Notification.requestPermission().catch(() => {});
}

let _swRegistration = null;
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then((reg) => { _swRegistration = reg; }).catch(() => {});
  // Deep link from a notification tap on an already-open tab (see sw.js's
  // notificationclick handler) — the SW postMessage()s us instead of navigating, since a
  // service worker can't change what's on screen in a client it doesn't own directly.
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data && event.data.type === "open_conversation" && event.data.conversation_id) {
      openConversation(event.data.conversation_id);
    }
  });
}

function notifyIfHidden(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden) return;  // tab is focused and visible — no need to also notify
  try {
    const n = new Notification(title, { body: body.slice(0, 200), icon: "/icon.svg" });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) { /* best-effort — a notification failing shouldn't break anything else */ }
}

// ---------------------------------------------------------------------------
// Phase 6 item 6: push subscription + settings (categories, quiet hours)
// ---------------------------------------------------------------------------

function _b64urlToUint8Array(b64url) {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const base64 = (b64url + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

async function enablePushNotifications() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  if (Notification.permission !== "granted") {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return false;
  }
  const reg = _swRegistration || await navigator.serviceWorker.ready;
  const { key } = await fetch("/push-public-key").then((r) => r.json());
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _b64urlToUint8Array(key),
    });
  }
  send({ type: "push_subscribe", subscription: sub.toJSON() });
  return true;
}

async function disablePushNotifications() {
  send({ type: "push_unsubscribe" });
  if (_swRegistration) {
    const sub = await _swRegistration.pushManager.getSubscription();
    if (sub) await sub.unsubscribe().catch(() => {});
  }
}

// Tier 10 (android_app) — called from native Android code via
// webView.evaluateJavascript("window.registerFcmToken('...')"), never by this page's own
// JS. The WebView has no FCM API of its own to get a token from directly (that's the
// Firebase SDK, native-only) — the app fetches the token itself and hands it in here, the
// one deliberate seam between the native shell and the web UI it hosts. Exposed on
// `window` (not just a local function) specifically so evaluateJavascript can reach it.
// Retries until the socket is actually open rather than dropping the call — the native
// side may well call this before boot()'s own hello handshake has finished.
window.registerFcmToken = function (token) {
  const trySend = () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      send({ type: "register_fcm_token", token });
    } else {
      setTimeout(trySend, 300);
    }
  };
  trySend();
};

// ---------------------------------------------------------------------------
boot();

// Deep link: a notification tap that had to open a fresh tab/window (sw.js's
// notificationclick openWindow fallback) lands here with ?conv=<id> — open it once we're
// actually connected (state.conversationId gets set by the "ready" handler first). A
// dashboard/team deep link now opens /dashboard directly (see sw.js) rather than landing
// here, since the dashboard moved out of this page entirely.
(function _handleDeepLinkOnLoad() {
  const params = new URLSearchParams(window.location.search);
  const conv = params.get("conv");
  if (!conv) return;
  const tryOpen = () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      openConversation(conv);
    } else {
      setTimeout(tryOpen, 300);
    }
  };
  tryOpen();
  history.replaceState(null, "", window.location.pathname);
})();

// ---------------------------------------------------------------------------
// Depth-layer parallax (see style.css's ".depth-layer" comment) — the two extra
// background planes drift at a slower rate than .messages as it scrolls, via a
// --depth-scroll custom property each layer's transform reads. rAF-throttled so a fast
// trackpad fling doesn't queue up a pile of unapplied writes; passive listener since this
// never calls preventDefault. Pure decoration — if #app or #messages is ever missing this
// just does nothing, never an error.
// ---------------------------------------------------------------------------
(function _setupDepthParallax() {
  const scroller = els.messages;
  const target = document.getElementById("app");
  if (!scroller || !target) return;
  let ticking = false;
  scroller.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      target.style.setProperty("--depth-scroll", `${scroller.scrollTop}px`);
      ticking = false;
    });
  }, { passive: true });
})();
