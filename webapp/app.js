// Jarvis web GUI — vanilla JS, no build step, no framework. Talks to server.py over the
// same /ws protocol every other client (main.py --server, voice_companion.py) speaks, so
// there's exactly one source of truth for what a "turn" is, not a GUI-specific dialect.

const $ = (sel) => document.querySelector(sel);

const els = {
  contentArea: $("#contentArea"),
  dashboardTabBtn: $("#dashboardTabBtn"),
  chatTabBtn: $("#chatTabBtn"),
  main: $("#main"),
  dashboardView: $("#dashboardView"),
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
  isRecording: false,
  skills: [],
  activeView: "dashboard",   // "dashboard" | "chat" — Phase 7, dashboard is the default landing view
  dashboardTeam: null,       // team key currently drilled into, or null when on the Overseer view
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
      send({ type: "get_overseer_snapshot" });
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
      refreshCurrentDashboardView();  // Phase 7: a proposed/reviewed skill can change the queue
      break;

    case "integration_status":
      renderIntegrationStatus(msg);
      break;

    case "overseer_snapshot":
      renderOverseerSnapshot(msg);
      break;

    case "team_dashboard":
      renderTeamDashboard(msg);
      break;

    case "team_status_changed":
      // Instant, in-place tile/pill update — no round trip needed for something this
      // cheap, same reasoning as the integration dots' own live-toggle pattern.
      patchTeamStatus(msg.team, msg.status, msg.detail);
      break;

    case "cross_team_incident":
      // One team's output just triggered another's — refetch so the Overseer's log picks
      // it up within a couple seconds, matching the live-update bar every other dashboard
      // piece already holds itself to.
      if (state.activeView === "dashboard" && !state.dashboardTeam) {
        send({ type: "get_overseer_snapshot" });
      }
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
      refreshCurrentDashboardView();  // Phase 7: a new Tier-3/4 item just entered the queue
      break;

    case "approval_resolved":
      refreshCurrentDashboardView();  // Phase 7: that item just left the queue
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

function renderToolsModal(tools) {
  els.toolsBody.innerHTML = "";
  let lastGroup = null;
  for (const tool of tools) {
    const group = tool.role || "general";
    if (group !== lastGroup) {
      const heading = document.createElement("div");
      heading.className = "tool-group-title";
      heading.textContent = group.toUpperCase();
      els.toolsBody.appendChild(heading);
      lastGroup = group;
    }
    const entry = document.createElement("div");
    entry.className = "tool-entry";
    const nameRow = document.createElement("div");
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = tool.name;
    const tier = document.createElement("span");
    tier.className = "tool-tier";
    tier.textContent = tool.tier;
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
    desc.textContent = team.scope_prompt;

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
// Integration status panel (Phase 6 item 5) — 4 dots in the sidebar footer showing
// whether Obsidian/Gmail/Calendar/Drive are actually reachable right now. Fetched once
// on "ready" (see handleServerMessage) rather than polled — these are slow-changing
// (you connect Google once, then it's connected for weeks), so there's no live-update
// path here; reconnecting the websocket (e.g. server restart) re-fetches it for free.
// ---------------------------------------------------------------------------

function renderIntegrationStatus(status) {
  const dots = {
    statusDotObsidian: status.obsidian,
    statusDotGmail: status.gmail,
    statusDotCalendar: status.calendar,
    statusDotDrive: status.drive,
  };
  for (const [id, connected] of Object.entries(dots)) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("connected", !!connected);
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
    },
  });
}
for (const el of [els.pushCatApprovals, els.pushCatAlerts, els.pushCatBriefing, els.pushCatMessages]) {
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
// Phase 7: Overseer + team dashboards. Dashboard is the default landing view (see
// index.html — #dashboardTabBtn starts .active, #main starts .hidden); switching to Chat
// leaves chat's own behavior completely untouched underneath. Within Dashboard, two
// sub-views: Overseer (all 5 teams) and one team's drill-down, never both at once.
// ---------------------------------------------------------------------------

function showDashboardView() {
  state.activeView = "dashboard";
  els.main.classList.add("hidden");
  els.dashboardView.classList.remove("hidden");
  els.dashboardTabBtn.classList.add("active");
  els.chatTabBtn.classList.remove("active");
  refreshCurrentDashboardView();
}

function showChatView() {
  state.activeView = "chat";
  els.dashboardView.classList.add("hidden");
  els.main.classList.remove("hidden");
  els.chatTabBtn.classList.add("active");
  els.dashboardTabBtn.classList.remove("active");
}

els.dashboardTabBtn.addEventListener("click", showDashboardView);
els.chatTabBtn.addEventListener("click", showChatView);

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

// Re-fetches whichever dashboard sub-view is actually on screen — the single place every
// live-update trigger (approval_request/resolved, skill_updated, cross_team_incident)
// funnels through, so none of them need to know which sub-view is currently open.
function refreshCurrentDashboardView() {
  if (state.activeView !== "dashboard") return;
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

// Instant in-place update on a live team_status_changed broadcast — avoids a full
// re-fetch for something this cheap, while refreshCurrentDashboardView() (triggered by
// the less frequent events) stays the correctness backstop.
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
  // Tier 4 > Tier 3 > proposed skill, newest first within each — mirrors the server's own
  // _overseer_snapshot() ordering; team-scoped queues (showTeamLabel=false) get the same
  // treatment for consistency even though it's already pre-sorted server-side there too.
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
// Voice input — Web Audio API capture (local mic, resampled to raw PCM16/16kHz in the
// browser), posted to /transcribe, which reuses the same local Vosk engine the wake-word
// companion uses. Nothing here is cloud STT — no Web Speech API, deliberately, so this
// stays consistent with the rest of this app's zero-API-key voice story. TTS playback of
// the reply uses the browser's own built-in speechSynthesis — also fully local, nothing
// sent anywhere, just the system voice reading text out loud.
// ---------------------------------------------------------------------------

let audioCtx = null;
let micStream = null;
let micSource = null;
let micProcessor = null;
let micSilentGain = null;
let recordedChunks = [];
let recordingSampleRate = 16000;

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendToolNote("This browser doesn't support microphone capture.");
    return;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    appendToolNote("Microphone access was denied or is unavailable.");
    return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  recordingSampleRate = audioCtx.sampleRate;
  micSource = audioCtx.createMediaStreamSource(micStream);
  micProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
  recordedChunks = [];

  micProcessor.onaudioprocess = (e) => {
    recordedChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };

  // ScriptProcessorNode only fires onaudioprocess while connected through to the
  // destination in some browsers — route through a silent gain so nothing is actually
  // heard (no mic-to-speaker feedback) while still satisfying that requirement.
  micSilentGain = audioCtx.createGain();
  micSilentGain.gain.value = 0;
  micSource.connect(micProcessor);
  micProcessor.connect(micSilentGain);
  micSilentGain.connect(audioCtx.destination);

  state.isRecording = true;
  els.micBtn.classList.add("recording");
  send({ type: "voice_status", state: "listening" });
}

function stopRecording() {
  if (!state.isRecording) return;
  state.isRecording = false;
  els.micBtn.classList.remove("recording");
  send({ type: "voice_status", state: "idle" });

  try { micProcessor.disconnect(); } catch (e) {}
  try { micSilentGain.disconnect(); } catch (e) {}
  try { micSource.disconnect(); } catch (e) {}
  if (micStream) micStream.getTracks().forEach(t => t.stop());
  const sourceRate = recordingSampleRate;
  const chunks = recordedChunks;
  recordedChunks = [];
  if (audioCtx) audioCtx.close();

  if (!chunks.length) return;
  const merged = mergeFloat32(chunks);
  const resampled = downsampleTo16k(merged, sourceRate);
  const pcm16 = floatTo16BitPCM(resampled);
  transcribeAndFill(pcm16);
}

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

async function transcribeAndFill(pcm16Buffer) {
  const blob = new Blob([pcm16Buffer], { type: "application/octet-stream" });
  const formData = new FormData();
  formData.append("audio", blob, "clip.raw");
  els.micBtn.title = "Transcribing…";
  try {
    const resp = await fetch("/transcribe", { method: "POST", body: formData });
    const data = await resp.json();
    if (data.text) {
      const existing = els.composerInput.value;
      els.composerInput.value = existing ? existing + " " + data.text : data.text;
      els.composerInput.style.height = "auto";
      els.composerInput.style.height = Math.min(els.composerInput.scrollHeight, 160) + "px";
      state.pendingVoiceOrigin = true;
      els.composerInput.focus();
    } else {
      appendToolNote("(didn't catch that)");
    }
  } catch (e) {
    appendToolNote(`Transcription failed: ${e}`);
  }
  els.micBtn.title = "Voice input";
}

function speakAloud(text) {
  if (!text || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();  // don't stack replies if one's already speaking
    const utter = new SpeechSynthesisUtterance(text);
    window.speechSynthesis.speak(utter);
  } catch (e) { /* best-effort — silence isn't worth surfacing an error for */ }
}

els.micBtn.addEventListener("click", () => {
  if (state.isRecording) stopRecording();
  else startRecording();
});

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
    } else if (event.data && event.data.type === "open_dashboard" && event.data.team) {
      openDashboardTeam(event.data.team);
    }
  });
}

// Phase 7: shared by the postMessage handler above and the ?team= deep link below —
// switches to the Dashboard tab and drills straight into one team, same "tapping a phone
// alert lands you on the exact queue item" requirement item 6's conversation deep link
// already satisfies, just for a team dashboard instead of a chat.
function openDashboardTeam(teamKey) {
  showDashboardView();
  showTeamDashboard(teamKey);
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

// ---------------------------------------------------------------------------
boot();

// Deep link: a notification tap that had to open a fresh tab/window (sw.js's
// notificationclick openWindow fallback) lands here with ?conv=<id> or ?team=<key> — open
// it once we're actually connected (state.conversationId gets set by the "ready" handler
// first; a team dashboard has no such prerequisite but waits on the same open socket so
// get_team_dashboard has something to send to).
(function _handleDeepLinkOnLoad() {
  const params = new URLSearchParams(window.location.search);
  const conv = params.get("conv");
  const team = params.get("team");
  if (!conv && !team) return;
  const tryOpen = () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      if (team) openDashboardTeam(team);
      else openConversation(conv);
    } else {
      setTimeout(tryOpen, 300);
    }
  };
  tryOpen();
  history.replaceState(null, "", window.location.pathname);
})();
