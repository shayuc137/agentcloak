// agentcloak Bridge — Chrome MV3 service worker
// Connects to the agentcloak daemon's /ext WebSocket with port auto-discovery.

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 18765;
const PORT_RANGE_START = 18765;
const PORT_RANGE_END = 18774;
const PROBE_TIMEOUT = 2000;
const RECONNECT_BASE = 1000;
const RECONNECT_MAX = 30000;

// declarativeNetRequest rule ids
// Range 10000..14999 is reserved for per-tab CSP strip rules.
const CSP_RULE_BASE_ID = 10000;
const CSP_RULE_RANGE = 5000;

let ws = null;
let reconnectDelay = RECONNECT_BASE;
let reconnectTimer = null;
let attachedTabs = new Map();
// Per-tab set of CDP domains the daemon has asked us to enable (7b reverse-
// engineering). Kept separate from attachedTabs so we don't disturb its
// JSON-serialized persistence (Sets don't round-trip through chrome.storage).
// Domains live only for the tab's lifetime — events flow through the global
// chrome.debugger.onEvent forwarder once enabled, so nothing else to wire.
let enabledDomainsByTab = new Map();
let currentHost = null;
let currentPort = null;
let currentService = null; // "agentcloak-daemon" when connected, else null
let isReconnecting = false;
let agentTabGroupId = null; // Chrome tab group for agent-managed tabs
let managedTabIds = new Set(); // tabs created or claimed by agent
// activeTabId is the implicit target for every command that doesn't carry
// an explicit tabId. Updated by navigate (when it creates a tab), claim,
// tab_new, tab_switch. Persisted to chrome.storage so the service worker
// can restore it after the MV3 worker terminates and respawns.
let activeTabId = null;

// --- Badge ---
// Four states map onto traffic-light semantics that match the badge skill:
//   on   — green, connected and healthy
//   wait — yellow, attempting / reconnecting
//   err  — red, last attempt failed (token wrong, port busy, etc.)
//   off  — grey/empty, not configured or manually stopped
// The grey "off" state intentionally clears the badge text so a dormant
// extension doesn't scream at the user — only failures earn a red label.

function setBadge(state) {
  const badges = {
    on: { text: "ON", color: "#4caf50" },
    wait: { text: "...", color: "#ff9800" },
    err: { text: "ERR", color: "#f44336" },
    off: { text: "", color: "#9e9e9e" },
  };
  const b = badges[state] || badges.off;
  chrome.action.setBadgeText({ text: b.text });
  chrome.action.setBadgeBackgroundColor({ color: b.color });
}

setBadge("off");

// --- Error feedback ---
// Persist the most recent failure so the options page can show a hint
// instead of just "disconnected". Cleared whenever a fresh connection
// succeeds so stale info doesn't outlive a working connection.

async function recordLastError(code, reason) {
  try {
    await chrome.storage.local.set({
      _last_error: {
        code,
        reason: reason || "",
        timestamp: Date.now(),
      },
    });
  } catch {
    // Storage failures shouldn't crash the worker.
  }
}

async function clearLastError() {
  try {
    await chrome.storage.local.remove("_last_error");
  } catch {
    // ignore
  }
}

// --- MV3 Service Worker Keepalive (chrome.alarms) ---

chrome.alarms.create("agentcloak-probe", { periodInMinutes: 0.1 });
chrome.alarms.create("agentcloak-keepalive", { periodInMinutes: 0.4 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "agentcloak-probe") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connect();
    }
  }
  if (alarm.name === "agentcloak-keepalive") {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }
});

// --- State Persistence ---

async function saveAttachedTabs() {
  const entries = Array.from(attachedTabs.entries());
  await chrome.storage.local.set({ _attached_tabs: entries });
}

async function restoreAttachedTabs() {
  const data = await chrome.storage.local.get([
    "_attached_tabs",
    "_agent_tab_group_id",
    "_managed_tab_ids",
    "_active_tab_id",
  ]);
  if (data._attached_tabs) {
    for (const [tabId, val] of data._attached_tabs) {
      try {
        await chrome.tabs.get(tabId);
        attachedTabs.set(tabId, val);
      } catch {
        // tab no longer exists
      }
    }
  }
  // Restore tab group — verify it still exists
  if (data._agent_tab_group_id != null) {
    try {
      const groups = await chrome.tabGroups.query({});
      if (groups.some((g) => g.id === data._agent_tab_group_id)) {
        agentTabGroupId = data._agent_tab_group_id;
      }
    } catch {
      // tabGroups API unavailable or group gone
    }
  }
  // Restore managed tab set — prune closed tabs
  if (data._managed_tab_ids) {
    for (const tabId of data._managed_tab_ids) {
      try {
        await chrome.tabs.get(tabId);
        managedTabIds.add(tabId);
      } catch {
        // tab no longer exists
      }
    }
  }
  // Restore activeTabId — verify the tab still exists. If it's gone we'll
  // get null back and the next command requiring an active tab will fail
  // cleanly with "no active tab" instead of trying to drive a dead tabId.
  if (data._active_tab_id != null) {
    try {
      await chrome.tabs.get(data._active_tab_id);
      activeTabId = data._active_tab_id;
    } catch {
      activeTabId = null;
    }
  }
}

// --- Tab Group Management ---

async function saveTabGroupState() {
  await chrome.storage.local.set({
    _agent_tab_group_id: agentTabGroupId,
    _managed_tab_ids: Array.from(managedTabIds),
  });
}

async function ensureTabGroup(tabId) {
  /**
   * Add a tab to the "agentcloak" tab group, creating the group on first use.
   */
  try {
    // Verify existing group still exists
    if (agentTabGroupId != null) {
      try {
        const groups = await chrome.tabGroups.query({});
        if (!groups.some((g) => g.id === agentTabGroupId)) {
          agentTabGroupId = null;
        }
      } catch {
        agentTabGroupId = null;
      }
    }

    if (agentTabGroupId == null) {
      // Create new tab group
      const groupId = await chrome.tabs.group({ tabIds: [tabId] });
      await chrome.tabGroups.update(groupId, {
        title: "agentcloak",
        color: "blue",
        collapsed: false,
      });
      agentTabGroupId = groupId;
    } else {
      // Add tab to existing group
      await chrome.tabs.group({ tabIds: [tabId], groupId: agentTabGroupId });
    }

    managedTabIds.add(tabId);
    await saveTabGroupState();
  } catch (e) {
    // Tab grouping is non-fatal — log and continue
    console.log(`[agentcloak] tab group error: ${e.message}`);
  }
}

// --- Config ---

async function loadConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(
      [
        "bridge_host",
        "bridge_port",
        "bridge_token",
        "last_connected_host",
        "last_connected_port",
        "last_connected_service",
      ],
      (data) => resolve(data || {})
    );
  });
}

async function saveLastConnected(host, port, service) {
  return new Promise((resolve) => {
    chrome.storage.local.set(
      {
        last_connected_host: host,
        last_connected_port: port,
        last_connected_service: service,
      },
      resolve
    );
  });
}

// --- Auto-Discovery (daemon /ext only) ---

async function probeHealth(host, port) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT);
  try {
    const resp = await fetch(`http://${host}:${port}/health`, {
      signal: controller.signal,
    });
    const data = await resp.json();
    clearTimeout(timer);
    if (data.ok && data.service === "agentcloak-daemon") {
      return { host, port, service: data.service };
    }
    return null;
  } catch {
    clearTimeout(timer);
    return null;
  }
}

async function discoverTarget(config) {
  const hosts = [];
  if (config.last_connected_host) hosts.push(config.last_connected_host);
  if (config.bridge_host && !hosts.includes(config.bridge_host)) {
    hosts.push(config.bridge_host);
  }
  if (!hosts.includes(DEFAULT_HOST)) hosts.push(DEFAULT_HOST);

  const userPort = config.bridge_port || null;
  let allResults = [];

  for (const host of hosts) {
    if (userPort) {
      const result = await probeHealth(host, userPort);
      if (result) allResults.push(result);
    }

    if (
      config.last_connected_port &&
      config.last_connected_port !== userPort &&
      host === config.last_connected_host
    ) {
      const result = await probeHealth(host, config.last_connected_port);
      if (result) allResults.push(result);
    }

    const probes = [];
    for (let p = PORT_RANGE_START; p <= PORT_RANGE_END; p++) {
      if (p === userPort || p === config.last_connected_port) continue;
      probes.push(probeHealth(host, p));
    }
    const results = await Promise.allSettled(probes);
    for (const r of results) {
      if (r.status === "fulfilled" && r.value) allResults.push(r.value);
    }
  }

  // Prefer the daemon we successfully connected to last time, else the first
  // daemon that answered the probe.
  if (config.last_connected_port) {
    const preferred = allResults.find(
      (r) => r.host === config.last_connected_host &&
        r.port === config.last_connected_port
    );
    if (preferred) return preferred;
  }
  const daemon = allResults.find((r) => r.service === "agentcloak-daemon");
  if (daemon) return daemon;

  return {
    host: config.bridge_host || DEFAULT_HOST,
    port: config.bridge_port || DEFAULT_PORT,
    service: null,
  };
}

// --- WebSocket Connection ---

async function connect() {
  if (ws && ws.readyState <= 1) return;

  isReconnecting = true;
  setBadge("wait");

  const config = await loadConfig();
  const target = await discoverTarget(config);
  const wsUrl = `ws://${target.host}:${target.port}/ext`;

  console.log(
    `[agentcloak] connecting to ${wsUrl} (${target.service || "unknown"})`
  );

  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    console.log(`[agentcloak] WebSocket creation failed: ${e.message}`);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log(
      `[agentcloak] connected to ${target.service} at ${target.host}:${target.port}`
    );
    reconnectDelay = RECONNECT_BASE;
    isReconnecting = false;
    currentHost = target.host;
    currentPort = target.port;
    currentService = target.service;
    setBadge("on");
    // A clean connect means whatever previously failed is fixed — drop
    // the stored error so the options page stops showing the old hint.
    clearLastError();

    saveLastConnected(target.host, target.port, target.service);

    const hello = { type: "hello", agent: "agentcloak-extension" };
    if (config.bridge_token) {
      hello.token = config.bridge_token;
    }
    ws.send(JSON.stringify(hello));
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!msg.id || !msg.cmd) return;
    const result = await handleCommand(msg);
    ws.send(JSON.stringify({ id: msg.id, ...result }));
  };

  ws.onclose = async (event) => {
    console.log(
      `[agentcloak] disconnected (code=${event.code}, reason=${event.reason})`
    );
    // Snapshot the previous "we were connected" state *before* we tear
    // it down — we need it to tell "connection refused" (never opened)
    // apart from "server cut us off" (was healthy, now isn't).
    const wasConnected = currentHost !== null;
    currentHost = null;
    currentPort = null;
    currentService = null;

    // Drop agent state on disconnect. The agent owns these tabs only as
    // long as the WS is live; once the channel breaks we hand them back
    // to the user (ungroup + detach) so they're not stuck in an orphan
    // blue group with no way to be controlled. State will be re-built
    // when the agent reconnects and reclaims.
    if (managedTabIds.size > 0) {
      const tids = Array.from(managedTabIds);
      for (const tid of tids) {
        try {
          await chrome.tabs.ungroup(tid);
        } catch {
          // tab may already be ungrouped / closed
        }
        try {
          await detachTab(tid);
        } catch {
          // ignore
        }
      }
      managedTabIds.clear();
      agentTabGroupId = null;
      await setActiveTabId(null);
      await saveTabGroupState();
      await removeAllCspRules();
    }

    // Auth / mutual-exclusion failures are user-actionable, so we flag
    // them as "err" (red badge) and persist the close code+reason for
    // the options page. Plain 1000/1001 disconnects just look like
    // network blips — fall through to the wait state and let the
    // reconnect timer retry quietly.
    const closeCode = event.code;
    const closeReason = event.reason || "";
    if (closeCode === 4001 || closeCode === 4002 || closeCode === 4003) {
      setBadge("err");
      recordLastError(closeCode, closeReason);
    } else if (closeCode === 1006 && !wasConnected) {
      // 1006 without a successful prior handshake means the TCP layer
      // failed — almost always "connection refused" or wrong host/port.
      // Don't flip to red on this (the reconnect timer might cure it
      // when the daemon comes up), but do persist the hint so the
      // options page can explain *why* we're stuck in "..."
      recordLastError(closeCode, closeReason || "connection refused");
    }
    scheduleReconnect();
  };

  ws.onerror = () => {
    ws.close();
  };
}

function scheduleReconnect() {
  isReconnecting = true;
  setBadge("wait");
  // Clear any prior pending reconnect; multiple stacked timers cause
  // overlapping connect() calls when storage changes also trigger a reconnect.
  if (reconnectTimer != null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 1.5, RECONNECT_MAX);
    connect();
  }, reconnectDelay);
}

// Reconnect when config changes in options page
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  const relevant = ["bridge_host", "bridge_port", "bridge_token"];
  if (relevant.some((k) => k in changes)) {
    console.log("[agentcloak] config changed, reconnecting...");
    reconnectDelay = RECONNECT_BASE;
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws && ws.readyState <= 1) {
      ws.close();
    } else {
      connect();
    }
  }
});

// Respond to status queries from options page
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "get_status") {
    sendResponse({
      connected: ws !== null && ws.readyState === WebSocket.OPEN,
      reconnecting: isReconnecting,
      host: currentHost,
      port: currentPort,
      service: currentService,
    });
    return true;
  }
  if (msg.type === "force_reconnect") {
    // The options page "Test Connection" button drops here. We want
    // the reconnect to happen immediately (reset backoff) so the user
    // gets feedback in seconds rather than waiting out the current
    // back-off window.
    console.log("[agentcloak] force_reconnect requested");
    reconnectDelay = RECONNECT_BASE;
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws && ws.readyState <= 1) {
      ws.close();
    } else {
      connect();
    }
    sendResponse({ ok: true });
    return true;
  }
});

// --- Global CDP event forwarding ---
// Every attached tab streams events back through the WebSocket so the daemon
// can spot dialogs, navigations, console errors etc. without polling. This
// is the channel that makes Proactive State Feedback work for RemoteBridge.

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    ws.send(
      JSON.stringify({
        type: "cdp_event",
        method,
        params,
        tabId: source.tabId,
      })
    );
  } catch (e) {
    console.log(`[agentcloak] cdp_event forward failed: ${e.message}`);
  }
});

// --- Command Handlers ---

async function handleCommand(msg) {
  try {
    switch (msg.cmd) {
      case "navigate":
        return await cmdNavigate(msg);
      case "screenshot":
        return await cmdScreenshot(msg);
      case "evaluate":
        return await cmdEvaluate(msg);
      case "page_info":
        return await cmdPageInfo(msg);
      case "cookies":
        return await cmdCookies(msg);
      case "tabs":
        return await cmdTabs(msg);
      case "tab_new":
        return await cmdTabNew(msg);
      case "tab_close":
        return await cmdTabClose(msg);
      case "tab_switch":
        return await cmdTabSwitch(msg);
      case "fetch":
        return await cmdFetch(msg);
      case "cdp":
        return await cmdCDP(msg);
      case "enable_domain":
        return await cmdEnableDomain(msg);
      case "batch":
        return await cmdBatch(msg);
      case "claim":
        return await cmdClaim(msg);
      case "finalize":
        return await cmdFinalize(msg);
      case "ping":
        return { ok: true, data: { pong: true } };
      default:
        return { ok: false, error: `unknown command: ${msg.cmd}` };
    }
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// --- Per-tab CSP strip rules (declarativeNetRequest) ---

function cspRuleIdForTab(tabId) {
  // Map tabId into reserved range so we never collide with other rules.
  // Modulo keeps the id stable per session even with large tabIds.
  return CSP_RULE_BASE_ID + (tabId % CSP_RULE_RANGE);
}

async function addCspRuleForTab(tabId) {
  const id = cspRuleIdForTab(tabId);
  try {
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [id],
      addRules: [
        {
          id,
          priority: 1,
          action: {
            type: "modifyHeaders",
            responseHeaders: [
              { header: "content-security-policy", operation: "remove" },
              {
                header: "content-security-policy-report-only",
                operation: "remove",
              },
            ],
          },
          condition: {
            tabIds: [tabId],
            resourceTypes: ["main_frame", "sub_frame"],
          },
        },
      ],
    });
  } catch (e) {
    console.log(
      `[agentcloak] CSP rule add failed for tab ${tabId}: ${e.message}`
    );
  }
}

async function removeCspRuleForTab(tabId) {
  const id = cspRuleIdForTab(tabId);
  try {
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [id],
    });
  } catch (e) {
    console.log(
      `[agentcloak] CSP rule remove failed for tab ${tabId}: ${e.message}`
    );
  }
}

async function removeAllCspRules() {
  try {
    const existing = await chrome.declarativeNetRequest.getDynamicRules();
    const ids = existing
      .filter((r) => r.id >= CSP_RULE_BASE_ID && r.id < CSP_RULE_BASE_ID + CSP_RULE_RANGE)
      .map((r) => r.id);
    if (ids.length) {
      await chrome.declarativeNetRequest.updateDynamicRules({
        removeRuleIds: ids,
      });
    }
  } catch (e) {
    console.log(`[agentcloak] CSP rule clear failed: ${e.message}`);
  }
}

async function reapplyCspRulesFromAttachedTabs() {
  // Called on browser restart — re-add per-tab CSP rules for any tabs the
  // service worker still considers attached (state restored from storage).
  for (const tabId of attachedTabs.keys()) {
    try {
      await chrome.tabs.get(tabId);
      await addCspRuleForTab(tabId);
    } catch {
      // tab gone; will be cleaned up by onRemoved
    }
  }
}

async function ensureAttached(tabId) {
  if (attachedTabs.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, "1.3");
  attachedTabs.set(tabId, true);
  await saveAttachedTabs();
  await addCspRuleForTab(tabId);
  // Enable the CDP domains we want events from. These are best-effort —
  // a failure here shouldn't block the attach (e.g. devtools already open).
  try {
    await chrome.debugger.sendCommand({ tabId }, "Page.enable", {});
  } catch (e) {
    console.log(`[agentcloak] Page.enable failed: ${e.message}`);
  }
  try {
    await chrome.debugger.sendCommand({ tabId }, "Runtime.enable", {});
  } catch (e) {
    console.log(`[agentcloak] Runtime.enable failed: ${e.message}`);
  }
  // Add to agent tab group for visual isolation
  await ensureTabGroup(tabId);
}

async function detachTab(tabId) {
  if (!attachedTabs.has(tabId)) return;
  try {
    await chrome.debugger.detach({ tabId });
  } catch {}
  attachedTabs.delete(tabId);
  // Detaching the debugger drops every enabled domain, so forget our
  // per-tab record — a future re-attach starts from a clean slate.
  enabledDomainsByTab.delete(tabId);
  await removeCspRuleForTab(tabId);
  await saveAttachedTabs();
}

function resolveTabId(msg) {
  // Order: explicit param > top-level field > module-level activeTabId.
  // We deliberately do NOT fall back to chrome.tabs.query({active:true})
  // here — that would hijack whatever tab the user happens to be looking
  // at, which is the exact behaviour the dogfood report flagged as wrong
  // for first-time navigate.
  return (
    msg.params?.tabId ||
    msg.params?.tab_id ||
    msg.tabId ||
    msg.tab_id ||
    activeTabId ||
    null
  );
}

async function setActiveTabId(tabId) {
  activeTabId = tabId;
  try {
    await chrome.storage.local.set({ _active_tab_id: tabId });
  } catch {
    // best-effort persistence
  }
}

// --- Navigate with CDP event wait (replaces setTimeout) ---

function waitForDebuggerEvent(tabId, eventName, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.debugger.onEvent.removeListener(listener);
      resolve(); // timeout is not fatal, page may still be usable
    }, timeoutMs);

    function listener(source, method) {
      if (source.tabId === tabId && method === eventName) {
        clearTimeout(timer);
        chrome.debugger.onEvent.removeListener(listener);
        resolve();
      }
    }
    chrome.debugger.onEvent.addListener(listener);
  });
}

async function cmdNavigate(msg) {
  const url = msg.params?.url || msg.url;
  if (!url) return { ok: false, error: "url required" };

  const waitUntil = msg.params?.waitUntil || "load";
  let tabId = resolveTabId(msg);

  // No active tab yet → create one rather than hijacking the user's tab.
  // This matches the dogfood expectation: first navigate after switching
  // to remote_bridge should open a fresh tab in the agentcloak group,
  // not redirect whatever page the user is currently reading.
  if (!tabId) {
    const tab = await chrome.tabs.create({ url, active: false });
    tabId = tab.id;
    await setActiveTabId(tabId);
    await ensureAttached(tabId);

    // Wait for the new tab to finish loading. chrome.tabs.create resolves
    // before the navigation finishes, so we listen for the same CDP event
    // as the in-place path below.
    const eventName =
      waitUntil === "domcontentloaded"
        ? "Page.domContentEventFired"
        : "Page.loadEventFired";
    await waitForDebuggerEvent(tabId, eventName, 30000);

    const finalTab = await chrome.tabs.get(tabId);
    return {
      ok: true,
      data: {
        url: finalTab.url || url,
        title: finalTab.title || "",
        frameId: null,
      },
    };
  }

  await ensureAttached(tabId);

  const eventName =
    waitUntil === "domcontentloaded"
      ? "Page.domContentEventFired"
      : "Page.loadEventFired";

  const loadPromise = waitForDebuggerEvent(tabId, eventName, 30000);
  const navResult = await chrome.debugger.sendCommand(
    { tabId },
    "Page.navigate",
    { url }
  );
  await loadPromise;

  const tab = await chrome.tabs.get(tabId);

  return {
    ok: true,
    data: {
      url: tab.url,
      title: tab.title,
      frameId: navResult.frameId,
    },
  };
}

async function cmdScreenshot(msg) {
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };

  await ensureAttached(tabId);
  const result = await chrome.debugger.sendCommand(
    { tabId },
    "Page.captureScreenshot",
    { format: "png" }
  );

  return { ok: true, data: { base64: result.data } };
}

// page_info — return current url/title/tabId without touching evaluate.
// snapshot() needs this for every call, so going through Runtime.evaluate
// is both slower and a single point of failure (the evaluate bug from A1
// dragged down snapshot too). chrome.tabs.get is the canonical source and
// always works, even when the page is mid-navigation or blank.
async function cmdPageInfo(msg) {
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };
  try {
    const tab = await chrome.tabs.get(tabId);
    return {
      ok: true,
      data: { url: tab.url || "", title: tab.title || "", tab_id: tab.id },
    };
  } catch (e) {
    return { ok: false, error: (e && e.message) || "page_info failed" };
  }
}

// --- evaluate via CDP Runtime.evaluate ---
//
// Old design used chrome.scripting.executeScript first (Path A) and fell back
// to CDP only on CSP errors. That path had two fatal bugs:
//   1. executeScript wraps in `new Function("return " + code)()` so an async
//      expression returns a Promise object instead of its resolved value.
//   2. The structured-clone boundary between content script and service worker
//      silently drops non-cloneable values, producing `undefined`.
// CDP Runtime.evaluate with awaitPromise:true + returnByValue:true gives us
// resolved values for both sync and async expressions, and surfaces real
// errors via exceptionDetails. CSP isn't a concern because we strip CSP
// headers via declarativeNetRequest for every attached tab (see ensureAttached).

async function cmdEvaluate(msg) {
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };

  const js = msg.params?.js || msg.params?.expression || msg.js;
  if (!js) return { ok: false, error: "js expression required" };

  await ensureAttached(tabId);

  const timeoutMs = msg.params?.timeout || 30000;

  try {
    const result = await chrome.debugger.sendCommand(
      { tabId },
      "Runtime.evaluate",
      {
        expression: js,
        awaitPromise: true,
        returnByValue: true,
        timeout: timeoutMs,
      }
    );

    if (result.exceptionDetails) {
      const exc = result.exceptionDetails;
      const genericText = new Set([
        "uncaught",
        "uncaught (in promise)",
        "script error",
      ]);
      const rawText = String(exc.text || "").trim();
      const usefulText = genericText.has(rawText.replace(/\.$/, "").toLowerCase())
        ? ""
        : rawText;
      let errMsg = String(
        exc.exception?.description ||
          exc.exception?.value ||
          usefulText ||
          "evaluation error"
      ).trim();
      const frame = exc.stackTrace?.callFrames?.[0];
      const url = frame?.url || exc.url || "";
      const line = frame?.lineNumber ?? exc.lineNumber;
      const column = frame?.columnNumber ?? exc.columnNumber;
      if (url || Number.isInteger(line) || Number.isInteger(column)) {
        let location = url || "<anonymous>";
        if (Number.isInteger(line)) {
          location += `:${line + 1}`;
          if (Number.isInteger(column)) location += `:${column + 1}`;
        }
        if (!errMsg.includes(location)) errMsg += `\n  at ${location}`;
      }
      const marker = " ... [truncated]";
      if (errMsg.length > 400) {
        errMsg = errMsg.slice(0, 400 - marker.length).trimEnd() + marker;
      }
      return { ok: false, error: errMsg };
    }

    // Runtime.evaluate returns the result wrapped in a RemoteObject. With
    // returnByValue we get the actual value in .value; without it (e.g. for
    // non-serializable objects) we get a description in .description.
    const remote = result.result;
    let value;
    if (remote && Object.prototype.hasOwnProperty.call(remote, "value")) {
      value = remote.value;
    } else if (remote && remote.type === "undefined") {
      value = null;
    } else if (remote && remote.description) {
      value = remote.description;
    } else {
      value = null;
    }

    return { ok: true, data: { result: value } };
  } catch (e) {
    return { ok: false, error: (e && e.message) || "evaluation error" };
  }
}

async function cmdCookies(msg) {
  const url = msg.params?.url;
  let cookies;
  if (url) {
    cookies = await chrome.cookies.getAll({ url });
  } else {
    const tabId = resolveTabId(msg);
    if (tabId) {
      const tab = await chrome.tabs.get(tabId);
      cookies = await chrome.cookies.getAll({ url: tab.url });
    } else {
      cookies = await chrome.cookies.getAll({});
    }
  }
  return { ok: true, data: cookies };
}

async function cmdTabs(_msg) {
  const tabs = await chrome.tabs.query({});
  const data = tabs
    .filter((t) => t.url && !t.url.startsWith("chrome://"))
    .map((t) => ({
      id: t.id,
      url: t.url,
      title: t.title,
      active: t.active,
      windowId: t.windowId,
    }));
  return { ok: true, data };
}

async function cmdTabNew(msg) {
  const url = msg.params?.url || msg.url;
  const createOpts = {};
  if (url) createOpts.url = url;
  const tab = await chrome.tabs.create(createOpts);
  // Wait briefly for the new tab to be eligible for debugger attach.
  // chrome.tabs.create resolves before the tab finishes loading, but the
  // tab id is valid immediately so the attach call works.
  try {
    await ensureAttached(tab.id);
  } catch (e) {
    console.log(
      `[agentcloak] tab_new attach failed for tab ${tab.id}: ${e.message}`
    );
  }
  // A freshly created tab is now the implicit target for subsequent
  // commands. Without this the agent has to do an explicit tab_switch
  // before snapshot/click, which was a recurring papercut in the dogfood.
  await setActiveTabId(tab.id);
  return {
    ok: true,
    data: {
      tab_id: tab.id,
      url: tab.url || url || "",
      title: tab.title || "",
      active: tab.active,
    },
  };
}

async function cmdTabClose(msg) {
  const tabId = msg.params?.tab_id ?? msg.params?.tabId ?? msg.tabId;
  if (tabId == null) {
    return { ok: false, error: "tab_id required" };
  }
  try {
    await detachTab(tabId);
  } catch {}
  try {
    await chrome.tabs.remove(tabId);
  } catch (e) {
    return { ok: false, error: e.message };
  }
  managedTabIds.delete(tabId);
  if (activeTabId === tabId) {
    // Active tab vanished — clear activeTabId so the next command either
    // creates a fresh tab (navigate) or fails with a clean "no active tab".
    await setActiveTabId(null);
  }
  await saveTabGroupState();
  return { ok: true, data: { closed: true, tab_id: tabId } };
}

async function cmdTabSwitch(msg) {
  const tabId = msg.params?.tab_id ?? msg.params?.tabId ?? msg.tabId;
  if (tabId == null) {
    return { ok: false, error: "tab_id required" };
  }
  try {
    const tab = await chrome.tabs.update(tabId, { active: true });
    await setActiveTabId(tab.id);
    return {
      ok: true,
      data: {
        switched: true,
        tab_id: tab.id,
        url: tab.url || "",
        title: tab.title || "",
      },
    };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function cmdFetch(msg) {
  // Run fetch inside the active tab's page context so we inherit its
  // cookies + origin headers — this is what callers expect when they fetch
  // via a logged-in remote browser.
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };

  const url = msg.params?.url;
  if (!url) return { ok: false, error: "url required" };

  const method = (msg.params?.method || "GET").toUpperCase();
  const headers = msg.params?.headers || {};
  const body = msg.params?.body;
  const timeoutMs = (msg.params?.timeout || 30) * 1000;

  try {
    const [frame] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: async (fetchUrl, fetchMethod, fetchHeaders, fetchBody, timeout) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
          const init = {
            method: fetchMethod,
            headers: fetchHeaders,
            signal: controller.signal,
          };
          if (fetchBody != null && fetchMethod !== "GET" && fetchMethod !== "HEAD") {
            init.body = fetchBody;
          }
          const resp = await fetch(fetchUrl, init);
          const respHeaders = {};
          resp.headers.forEach((v, k) => {
            respHeaders[k] = v;
          });
          const text = await resp.text();
          return {
            status: resp.status,
            url: resp.url,
            headers: respHeaders,
            body: text,
          };
        } catch (e) {
          return { error: e.message || String(e) };
        } finally {
          clearTimeout(timer);
        }
      },
      args: [url, method, headers, body ?? null, timeoutMs],
    });
    const result = frame?.result;
    if (!result) {
      return { ok: false, error: "fetch returned no result" };
    }
    if (result.error) {
      return { ok: false, error: result.error };
    }
    return { ok: true, data: result };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function cmdCDP(msg) {
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };

  const method = msg.params?.method || msg.method;
  const params = msg.params?.params || {};

  if (!method) return { ok: false, error: "CDP method required" };

  await ensureAttached(tabId);
  const result = await chrome.debugger.sendCommand({ tabId }, method, params);
  return { ok: true, data: result };
}

// enable_domain — idempotently turn on a CDP domain for the active tab so the
// reverse-engineering managers (debugger / streaming / sourcemap) receive its
// events. ensureAttached already enables Page + Runtime; this covers the
// on-demand domains (Debugger, Network, Fetch, ...). Events then arrive via
// the global chrome.debugger.onEvent forwarder, so there's nothing further to
// route here. Tracked per-tab so a second request for the same domain is a
// no-op rather than a redundant <Domain>.enable.
async function cmdEnableDomain(msg) {
  const tabId = resolveTabId(msg);
  if (!tabId) return { ok: false, error: "no active tab" };

  const domain = msg.params?.domain || msg.domain;
  if (!domain) return { ok: false, error: "domain required" };

  await ensureAttached(tabId);

  let domains = enabledDomainsByTab.get(tabId);
  if (!domains) {
    domains = new Set();
    enabledDomainsByTab.set(tabId, domains);
  }
  if (domains.has(domain)) {
    return { ok: true, data: { domain, enabled: true, already: true } };
  }

  await chrome.debugger.sendCommand({ tabId }, `${domain}.enable`, {});
  domains.add(domain);
  return { ok: true, data: { domain, enabled: true } };
}

async function cmdBatch(msg) {
  const commands = msg.params?.commands || msg.commands || [];
  const results = [];

  for (const cmd of commands) {
    if (!cmd.tabId && msg.tabId) cmd.tabId = msg.tabId;
    const result = await handleCommand({ ...cmd, id: msg.id });
    results.push(result);
  }

  return { ok: true, data: { results, completed: results.length } };
}

// --- Tab Claiming ---
//
// Two scenarios:
//   1. First claim — tab isn't in managedTabIds. ensureAttached adds it to
//      the blue "agentcloak" group (creating the group if needed).
//   2. Hand-back — the tab is in managedTabIds but we're in handoff state
//      (group recolored green by cmdFinalize). Restore the group label so
//      the user sees the agent has retaken control. ensureAttached still
//      runs to re-establish the debugger session.

async function cmdClaim(msg) {
  const tabId = msg.params?.tabId || msg.params?.tab_id;
  const urlPattern = msg.params?.urlPattern || msg.params?.url_pattern;

  if (!tabId && !urlPattern) {
    return { ok: false, error: "tabId or urlPattern required" };
  }

  let targetTab = null;

  if (tabId) {
    try {
      targetTab = await chrome.tabs.get(tabId);
    } catch {
      return { ok: false, error: `tab ${tabId} not found` };
    }
  } else {
    // Find first tab matching URL substring
    const allTabs = await chrome.tabs.query({});
    targetTab = allTabs.find((t) => t.url && t.url.includes(urlPattern));
    if (!targetTab) {
      return {
        ok: false,
        error: `no tab matching URL pattern: ${urlPattern}`,
      };
    }
  }

  const isHandBack = managedTabIds.has(targetTab.id);

  // Attach debugger to the claimed tab (also adds it to the agent group
  // via ensureTabGroup).
  await ensureAttached(targetTab.id);

  // Hand-back: restore the group to the active blue label. ensureTabGroup
  // re-adds the tab but doesn't relabel an existing group, so we have to
  // explicitly flip it back from the green "handing off..." state.
  if (isHandBack && agentTabGroupId != null) {
    try {
      await chrome.tabGroups.update(agentTabGroupId, {
        title: "agentcloak",
        color: "blue",
        collapsed: false,
      });
    } catch (e) {
      console.log(
        `[agentcloak] claim group restore failed: ${e.message}`
      );
    }
  }

  // Claiming sets the implicit active tab. Without this the agent has to
  // do an explicit tab_switch right after claim, which was a sharp edge
  // surfaced in the dogfood report.
  await setActiveTabId(targetTab.id);

  return {
    ok: true,
    data: {
      tabId: targetTab.id,
      url: targetTab.url,
      title: targetTab.title,
      claimed: true,
      handBack: isHandBack,
    },
  };
}

// --- Session Finalize ---
//
// Two modes only — the old "deliverable" variant was redundant once we
// landed on the "blue group = agent has permission to take over" mental
// model. `deliverable` is kept as a deprecated alias for `close` so older
// scripts don't break, but it just routes to the close path.
//
//   close   — agent fully releases the session: close every tab it touched,
//             delete the group, drop all state.
//   handoff — agent steps back but the tabs stay open for the user. The
//             group is kept (just recolored green/"handing off...") so the
//             agent can re-claim those tabs later via cmdClaim, which
//             restores them to blue/"agentcloak" automatically.

async function cmdFinalize(msg) {
  let mode = msg.params?.mode || "close";
  // Deprecated alias: `deliverable` collapses into `close`. Kept for
  // backwards compatibility with the v0.2.0 RemoteBridge surface.
  if (mode === "deliverable") mode = "close";

  const validModes = ["close", "handoff"];
  if (!validModes.includes(mode)) {
    return {
      ok: false,
      error: `invalid mode: ${mode}. Use: close, handoff (deliverable → close)`,
    };
  }

  const tabIds = Array.from(managedTabIds);
  const result = { mode, tabsAffected: 0 };

  if (mode === "close") {
    let closed = 0;
    for (const tid of tabIds) {
      try {
        await detachTab(tid);
        await chrome.tabs.remove(tid);
        closed++;
      } catch {
        // tab may already be closed
      }
    }
    managedTabIds.clear();
    agentTabGroupId = null;
    await setActiveTabId(null);
    result.tabsAffected = closed;
  } else {
    // handoff: detach debugger, KEEP group (recolored green), KEEP tabs.
    // Crucially we do NOT call chrome.tabs.ungroup and we do NOT clear
    // managedTabIds — that's what makes hand-back via cmdClaim work.
    if (agentTabGroupId != null) {
      try {
        await chrome.tabGroups.update(agentTabGroupId, {
          title: "handing off...",
          color: "green",
          collapsed: false,
        });
      } catch (e) {
        console.log(
          `[agentcloak] handoff group update failed: ${e.message}`
        );
      }
    }
    for (const tid of tabIds) {
      try {
        await detachTab(tid);
      } catch {
        // ignore
      }
    }
    // Drop active focus so subsequent commands fail loudly rather than
    // silently driving a tab the agent is supposed to have released.
    await setActiveTabId(null);
    result.tabsAffected = tabIds.length;
  }

  // After finalize there should be no per-tab CSP rules left.
  await removeAllCspRules();
  await saveTabGroupState();
  await saveAttachedTabs();
  return { ok: true, data: result };
}

// Clean up debugger attachments and managed tabs when tabs close.
// Also forward the event upstream so the daemon can clear its own
// active_tab_id if the gone tab was the one it was driving.
chrome.tabs.onRemoved.addListener((tabId) => {
  attachedTabs.delete(tabId);
  enabledDomainsByTab.delete(tabId);
  managedTabIds.delete(tabId);
  if (activeTabId === tabId) {
    activeTabId = null;
    chrome.storage.local.set({ _active_tab_id: null }).catch(() => {});
  }
  removeCspRuleForTab(tabId);
  saveAttachedTabs();
  saveTabGroupState();

  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({ type: "tab_event", event: "removed", tabId }));
    } catch {
      // best-effort notification
    }
  }
});

// Notify the daemon when the user (or the page itself) navigates a
// managed tab — the daemon may want to refresh cached page info or, in
// future, surface a "user took control" signal to the agent. We only
// fire on URL changes, not loading/status flips, to avoid drowning the
// daemon in noise from every page lifecycle event.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!managedTabIds.has(tabId)) return; // only forward for tabs we manage
  try {
    ws.send(
      JSON.stringify({
        type: "tab_event",
        event: "updated",
        tabId,
        url: changeInfo.url,
        title: tab?.title || "",
      })
    );
  } catch {
    // best-effort notification
  }
});

// On install, ensure any leftover global CSP rule (from older extension
// versions) is dropped. Per-tab rules are added by ensureAttached().
chrome.runtime.onInstalled.addListener(async () => {
  try {
    // Old extension versions used rule id 9999 globally — clear it if present.
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [9999],
    });
  } catch {}
});

// On browser restart, re-apply per-tab CSP rules for tabs we still consider
// attached (state restored from chrome.storage.local).
chrome.runtime.onStartup.addListener(async () => {
  await restoreAttachedTabs();
  await reapplyCspRulesFromAttachedTabs();
  connect();
});

// Restore state and start connection
restoreAttachedTabs().then(() => connect());
