// =============================================================
// B站AI伴侣 WebUI 脚本
// 优先使用框架注入的 window.AstrBotPluginPage（必须提供 apiGet 或 apiPost）；
// 未注入时依次尝试 parent / top 上的同名对象；
// 都不可用则兜底为多路径候选的直接 fetch 调用。
// =============================================================

const PLUGIN_NAME = "astrbot_plugin_bili_ai_companion";

// ---------------------------------------------------------------------
// Bridge 获取 + 初始化
// ---------------------------------------------------------------------

let _bridge = null;
let _fallbackFetch = false;
let _fetchBasePrefix = null;
let _bridgeDiagnostics = [];

const BRIDGE_NAMES = [
  "AstrBotPluginPage",
  "PluginBridge",
  "astrbotPlugin",
  "astrbotPluginBridge",
  "AstrBotPluginBridge",
  "AstrBotBridge",
  "pluginBridge",
  "__ASTRBOT_BRIDGE__",
  "__BRIDGE__",
];

function scanWindowsForBridges() {
  const wins = [];
  wins.push({ w: window, label: "window" });
  try { if (window.parent && window.parent !== window) wins.push({ w: window.parent, label: "parent" }); } catch (_) {}
  try { if (window.top && window.top !== window && window.top !== window.parent) wins.push({ w: window.top, label: "top" }); } catch (_) {}
  const found = [];
  for (const { w, label } of wins) {
    for (const name of BRIDGE_NAMES) {
      try {
        const cand = w[name];
        if (!cand) continue;
        const keys = typeof cand === "object"
          ? Object.keys(cand).filter(k => typeof cand[k] === "function").slice(0, 10).join(",")
          : typeof cand;
        found.push({ label, name, type: typeof cand, fn: keys, validStrict: isBridgeStrictValid(cand), validLoose: isBridgeLooseValid(cand) });
      } catch (e) {
        found.push({ label, name, error: String(e).slice(0, 120) });
      }
    }
  }
  return found;
}

_bridgeDiagnostics = scanWindowsForBridges();

function findBridge(mode) {
  const wins = [];
  wins.push(window);
  try { if (window.parent && window.parent !== window) wins.push(window.parent); } catch (_) {}
  try { if (window.top && window.top !== window && window.top !== window.parent) wins.push(window.top); } catch (_) {}
  const check = mode === "strict" ? isBridgeStrictValid : isBridgeLooseValid;
  for (const w of wins) {
    for (const name of BRIDGE_NAMES) {
      try {
        const cand = w[name];
        if (check(cand)) return cand;
      } catch (_) {}
    }
  }
  return null;
}

function isBridgeStrictValid(b) {
  if (!b || typeof b !== "object") return false;
  return typeof b.apiGet === "function" && typeof b.apiPost === "function";
}

function isBridgeLooseValid(b) {
  if (!b || typeof b !== "object") return false;
  return typeof b.apiGet === "function" || typeof b.apiPost === "function";
}

_bridge = findBridge("strict");
if (!_bridge) _bridge = findBridge("loose");

if (_bridge) {
  if (typeof _bridge.ready !== "function") _bridge.ready = () => Promise.resolve();
  if (typeof _bridge.apiGet !== "function") {
    _bridge.apiGet = () => Promise.reject(new Error("bridge 不支持 apiGet，已切直连"));
  }
  if (typeof _bridge.apiPost !== "function") {
    _bridge.apiPost = () => Promise.reject(new Error("bridge 不支持 apiPost，已切直连"));
  }
} else {
  // 兜底直连模式
  _fallbackFetch = true;
  _bridge = {
    ready: () => Promise.resolve(),
    apiGet: (path, params) => fetchJson(path, "GET", null, params),
    apiPost: (path, body) => fetchJson(path, "POST", body, null),
  };
}

async function ensureBridgeReady() {
  try {
    if (typeof _bridge.ready === "function") await _bridge.ready();
  } catch (_) {}
  // ready 之后再探测一次
  if (_fallbackFetch) return;
  const postReady = findBridge("strict") || findBridge("loose");
  if (postReady) {
    if (typeof postReady.apiGet === "function") _bridge.apiGet = (p, q) => postReady.apiGet(p, q);
    if (typeof postReady.apiPost === "function") _bridge.apiPost = (p, b) => postReady.apiPost(p, b);
  }
}

function candidatePrefixes() {
  const absolute = [
    `/${PLUGIN_NAME}/`,
    `/api/plugin/${PLUGIN_NAME}/`,
    `/api/${PLUGIN_NAME}/`,
    `/plugin_api/${PLUGIN_NAME}/`,
    `/plugins/${PLUGIN_NAME}/`,
    `/plugin/${PLUGIN_NAME}/`,
  ];
  const relative = [];
  try {
    const base = window.location.pathname.replace(/[^/]*$/, "");
    relative.push(`${base}${PLUGIN_NAME}/`);
    relative.push(`${base}api/${PLUGIN_NAME}/`);
    relative.push(`${base}plugin/${PLUGIN_NAME}/`);
    const sub = base.replace(/^(.*?)(?:plugins?|extensions?|pages?)\//, "$1");
    if (sub !== base) {
      relative.push(`${sub}${PLUGIN_NAME}/`);
      relative.push(`${sub}api/${PLUGIN_NAME}/`);
      relative.push(`${sub}plugin/${PLUGIN_NAME}/`);
    }
  } catch (_) {}
  return [...absolute, ...relative];
}

async function fetchJson(path, method, body, qs) {
  const qparts = [];
  if (qs && typeof qs === "object") {
    for (const [k, v] of Object.entries(qs)) {
      if (v === undefined || v === null || v === "") continue;
      qparts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
    }
  }
  const qsStr = qparts.length ? "?" + qparts.join("&") : "";
  const subPath = `${path}${qsStr}`;

  const opts = {
    method: method || "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (method !== "GET" && method !== "HEAD") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body || {});
  }

  const prefixes = _fetchBasePrefix ? [_fetchBasePrefix] : candidatePrefixes();
  let lastErr = null;
  for (const prefix of prefixes) {
    let url;
    try {
      url = prefix + subPath.replace(/^\/+/, "");
    } catch (e) { lastErr = e; continue; }
    try {
      const res = await fetch(url, opts);
      let text = "";
      try { text = await res.text(); } catch (_) {}
      if (res.ok) {
        if (!_fetchBasePrefix) _fetchBasePrefix = prefix;
        let data = null;
        try { data = JSON.parse(text); } catch (_) { data = { data: text, raw: text }; }
        return data;
      } else {
        lastErr = new Error(`HTTP ${res.status}${text ? "：" + text.slice(0, 160) : ""} (${url})`);
      }
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("所有路径均不可达");
}

// ---------------------------------------------------------------------
// API 封装
// ---------------------------------------------------------------------

async function apiGet(path, params) {
  return _bridge.apiGet(path, params);
}

async function apiPost(path, body) {
  return _bridge.apiPost(path, body);
}

// ---------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------

function toast(msg, type = "info") {
  const box = document.getElementById("toastBox");
  if (!box) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateX(40px)";
    el.style.transition = "all 0.3s ease";
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

// ---------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(t) {
  if (!t) return "-";
  return String(t).slice(0, 19);
}

function levelTag(level) {
  const cls = { today: "today", recent: "recent", long_term: "long_term" }[level] || "";
  const label = { today: "今日", recent: "近期", long_term: "长期" }[level] || level;
  return `<span class="tag ${cls}">${esc(label)}</span>`;
}

function affectionTag(level) {
  const labels = { special: "主人💖", close: "好友✨", friend: "熟人😊", normal: "粉丝👋", stranger: "陌生人🌙", cold: "厌恶🖤" };
  return `<span class="tag ${level}">${esc(labels[level] || level)}</span>`;
}

// ---------------------------------------------------------------------
// 标签切换
// ---------------------------------------------------------------------

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    const target = tab.dataset.tab;
    const pane = document.getElementById(`pane-${target}`);
    if (pane) pane.classList.add("active");
    loadTab(target);
  });
});

document.querySelectorAll(".sub-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".sub-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".subpane").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    const target = tab.dataset.subtab;
    const pane = document.getElementById(`subpane-${target}`);
    if (pane) pane.classList.add("active");
    loadSubTab(target);
  });
});

// ---------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------

async function loadStatus() {
  try {
    const res = await apiGet("status");
    const d = res.data || res;
    const summary = document.getElementById("summary");
    const cards = document.getElementById("cards");

    const runIcon = d.running ? '<span class="ok">🟢 运行中</span>' : '<span class="err">🔴 未运行</span>';
    const cookieIcon = d.cookie_valid ? '<span class="ok">✅ 有效</span>' : '<span class="err">❌ 无效</span>';
    const syncMode = d.sync.mode || "dual";
    const syncModeLabel = d.sync.mode_label || "双轨模式（本地+同步）";
    const syncIcon = d.sync.enabled && d.sync.companion_available
      ? '<span class="ok">✅ 已连接</span>'
      : `<span class="err">❌ ${d.sync.enabled ? "companion 不可用" : "未同步"}</span>`;

    summary.innerHTML = `
      <b>📺 BiliCompanion ${esc(d.version)}</b><br/>
      运行状态：${runIcon} ｜ Cookie：${cookieIcon} ${esc(d.cookie_info)}<br/>
      🎭 心情：<b>${esc(d.mood)}</b> ｜ 🌱 性格v${esc(d.personality_version)}（${esc(d.personality_last_evolve)}）<br/>
      🧠 记忆模式：<b>${esc(syncModeLabel)}</b>（${esc(syncMode)}）｜ ${syncIcon} ｜ 已同步 ${esc(d.sync.synced_count)} 次 ｜ 最后同步 ${esc(d.sync.last_sync || "从未")}<br/>
      🕐 ${esc(d.now)}
    `;

    const lv = d.memory_levels || {};
    cards.innerHTML = `
      <div class="card"><div class="num">${d.memory_count}</div><div class="label">总记忆</div></div>
      <div class="card blue"><div class="num">${lv.today || 0}</div><div class="label">今日</div></div>
      <div class="card orange"><div class="num">${lv.recent || 0}</div><div class="label">近期</div></div>
      <div class="card green"><div class="num">${lv.long_term || 0}</div><div class="label">长期</div></div>
      <div class="card gray"><div class="num">${d.aged_count}</div><div class="label">老化</div></div>
      <div class="card"><div class="num">${d.permanent_count}</div><div class="label">永久记忆</div></div>
      <div class="card blue"><div class="num">${d.profile_count}</div><div class="label">用户画像</div></div>
      <div class="card green"><div class="num">${d.today_watched}</div><div class="label">今日看片</div></div>
      <div class="card orange"><div class="num">${d.today_dynamic}</div><div class="label">今日动态</div></div>
      <div class="card"><div class="num">${d.today_replies}</div><div class="label">今日回复</div></div>
    `;
  } catch (e) {
    document.getElementById("summary").innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

async function loadMemory() {
  try {
    const level = document.getElementById("memLevel").value;
    const q = document.getElementById("memSearch").value;
    const res = await apiGet("memory", { level, q, limit: 100 });
    const d = res.data || res;
    const tbody = document.getElementById("memTbody");
    const empty = document.getElementById("memEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((m, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${levelTag(m.level)}</td>
          <td>${esc(m.text).slice(0, 120)}${m.text && m.text.length > 120 ? "…" : ""}</td>
          <td>${esc(m.username || m.user_id || "-")}</td>
          <td>${fmtTime(m.time)}</td>
          <td>${m.aged ? '<span class="tag aged">老化</span>' : "—"}</td>
        </tr>`).join("");
    }
    // 永久记忆
    const permRes = await apiGet("permanent-memory", { limit: 50 });
    const permD = permRes.data || permRes;
    const permTbody = document.getElementById("permTbody");
    const permEmpty = document.getElementById("permEmpty");
    if (!permD.items || permD.items.length === 0) {
      permTbody.innerHTML = "";
      permEmpty.hidden = false;
    } else {
      permEmpty.hidden = true;
      permTbody.innerHTML = permD.items.map((m, i) => `
        <tr><td>${i + 1}</td><td>${esc(typeof m === "string" ? m : (m.text || m.content || JSON.stringify(m))).slice(0, 200)}</td></tr>
      `).join("");
    }
  } catch (e) {
    toast("记忆加载失败: " + e.message, "error");
  }
}

async function loadAffection() {
  try {
    const res = await apiGet("affection");
    const d = res.data || res;
    const tbody = document.getElementById("affTbody");
    const empty = document.getElementById("affEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((a, i) => {
        const pct = Math.max(0, Math.min(100, a.score));
        const cls = a.score >= 60 ? "high" : a.score >= 30 ? "mid" : a.score > 0 ? "low" : "neg";
        return `
          <tr>
            <td>${i + 1}</td>
            <td>${esc(a.uid)}</td>
            <td class="num">${a.score}</td>
            <td>${affectionTag(a.level)}</td>
            <td class="bar-col"><div class="bar"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div></td>
          </tr>`;
      }).join("");
    }
  } catch (e) {
    toast("好感度加载失败: " + e.message, "error");
  }
}

async function loadProfiles() {
  try {
    const res = await apiGet("profiles");
    const d = res.data || res;
    const tbody = document.getElementById("profileTbody");
    const empty = document.getElementById("profileEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((p, i) => {
        const tags = (p.tags || []).map(t => `<span class="tag normal">${esc(t)}</span>`).join(" ");
        return `
          <tr>
            <td>${i + 1}</td>
            <td>${esc(p.uid)}</td>
            <td>${esc(p.username || "-")}</td>
            <td>${esc(p.summary || "-").slice(0, 150)}</td>
            <td>${tags || "—"}</td>
            <td>${fmtTime(p.updated_at)}</td>
          </tr>`;
      }).join("");
    }
  } catch (e) {
    toast("画像加载失败: " + e.message, "error");
  }
}

async function loadPersonality() {
  try {
    const res = await apiGet("personality");
    const d = res.data || res;
    const summary = document.getElementById("personalitySummary");
    const raw = document.getElementById("personalityRaw");
    const ver = d.version || 0;
    const last = d.last_evolve || "从未";
    const traits = d.traits || d.personality || {};
    summary.innerHTML = `<b>🎭 性格演化</b> v${esc(ver)} ｜ 最后演化：${esc(last)}<br/>当前性格特征数：${Object.keys(traits).length}`;
    raw.textContent = JSON.stringify(d, null, 2);
  } catch (e) {
    document.getElementById("personalitySummary").innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

async function loadWatchLog() {
  try {
    const res = await apiGet("watch-log", { limit: 50 });
    const d = res.data || res;
    const tbody = document.getElementById("watchTbody");
    const empty = document.getElementById("watchEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((l, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${esc(l.title || l.name || "-").slice(0, 80)}</td>
          <td>${esc(l.bvid || l.aid || "-")}</td>
          <td>${fmtTime(l.time)}</td>
        </tr>`).join("");
    }
  } catch (e) {
    toast("看片日志加载失败: " + e.message, "error");
  }
}

async function loadDynamicLog() {
  try {
    const res = await apiGet("dynamic-log", { limit: 50 });
    const d = res.data || res;
    const tbody = document.getElementById("dynamicTbody");
    const empty = document.getElementById("dynamicEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((l, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${esc(l.content || l.text || "-").slice(0, 150)}</td>
          <td>${fmtTime(l.time)}</td>
        </tr>`).join("");
    }
  } catch (e) {
    toast("动态日志加载失败: " + e.message, "error");
  }
}

async function loadReplyLog() {
  try {
    const res = await apiGet("reply-log", { limit: 50 });
    const d = res.data || res;
    const tbody = document.getElementById("replyTbody");
    const empty = document.getElementById("replyEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((l, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${esc(l.original || l.source || l.comment || "-").slice(0, 100)}</td>
          <td>${esc(l.reply || l.response || l.content || "-").slice(0, 100)}</td>
          <td>${fmtTime(l.time)}</td>
        </tr>`).join("");
    }
  } catch (e) {
    toast("回复日志加载失败: " + e.message, "error");
  }
}

async function loadBangumiLog() {
  try {
    const res = await apiGet("bangumi-log", { limit: 50 });
    const d = res.data || res;
    const tbody = document.getElementById("bangumiTbody");
    const empty = document.getElementById("bangumiEmpty");
    if (!d.items || d.items.length === 0) {
      tbody.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      tbody.innerHTML = d.items.map((l, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${esc(l.title || l.name || "-").slice(0, 80)}</td>
          <td>${esc(l.episode || l.ep || "-")}</td>
          <td>${fmtTime(l.time)}</td>
        </tr>`).join("");
    }
  } catch (e) {
    toast("番剧日志加载失败: " + e.message, "error");
  }
}

async function loadSchedule() {
  try {
    const res = await apiGet("schedule");
    const d = res.data || res;
    const summary = document.getElementById("scheduleSummary");
    const pt = (d.proactive_times || []).join("、") || "未生成";
    const dt = (d.dynamic_times || []).join("、") || "未生成";
    const sft = (d.special_follow_times || []).join("、") || "未启用";
    const ptrig = (d.proactive_triggered || []).join("、") || "暂无";
    const dtrig = (d.dynamic_triggered || []).join("、") || "暂无";
    summary.innerHTML = `
      <b>🎯 今日计划</b><br/>
      主动时间：${esc(pt)}<br/>
      动态时间：${esc(dt)}<br/>
      特关时间：${esc(sft)}<br/>
      已触发主动：${esc(ptrig)}<br/>
      已触发动态：${esc(dtrig)}
    `;
  } catch (e) {
    document.getElementById("scheduleSummary").innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

async function loadSyncStatus() {
  try {
    const res = await apiGet("sync-status");
    const d = res.data || res;
    const summary = document.getElementById("syncSummary");
    const info = document.getElementById("syncInfo");
    const mode = d.mode || "dual";
    const modeLabel = d.mode_label || "双轨模式（本地+同步）";
    const syncIcon = d.enabled ? '<span class="ok">✅ 启用</span>' : '<span class="err">❌ 未同步</span>';
    const localIcon = d.local_writable ? '<span class="ok">✅ 写入</span>' : '<span class="err">❌ 仅缓存</span>';
    const recallIcon = d.recall_enabled ? '<span class="ok">✅ 读取</span>' : '<span class="err">❌ 关闭</span>';
    const compIcon = d.companion_available ? '<span class="ok">✅ 可用</span>' : '<span class="err">❌ 不可用</span>';
    const bridgeIcon = d.bridge_available ? '<span class="ok">✅ 可读</span>' : '<span class="err">❌ 无桥接</span>';
    summary.innerHTML = `
      <b>🧠 记忆系统模式</b><br/>
      当前模式：<b>${esc(modeLabel)}</b>（${esc(mode)}）<br/>
      本地B站记忆：${localIcon} ｜ 同步到memory_companion：${syncIcon} ｜ 读取跨平台记忆：${recallIcon}<br/>
      companion 插件：${compIcon} ｜ bridge 桥接：${bridgeIcon}<br/>
      已同步次数：<b>${esc(d.synced_count)}</b> ｜ 最后同步：${esc(d.last_sync || "从未")}<br/>
      🕐 ${esc(d.now)}
    `;
    const modeDesc = {
      standalone: "📦 <b>独立模式</b>：仅使用本地B站记忆，不与 memory_companion 交互（不同步、不读取）。所有记忆存储在本地 memory.json。",
      dual: "✅ <b>双轨模式</b>：本地B站记忆为主，关键事件同步副本到 memory_companion；B站交互时会读取 memory_companion 的跨平台共同记忆注入LLM上下文。",
      companion: "🐝 <b>伴侣模式</b>：优先写入 memory_companion，本地记忆仍保留用于快速检索；每次本地写入会自动触发同步；B站交互时同样读取跨平台记忆。",
    };
    const desc = modeDesc[mode] || modeDesc.dual;
    if (d.enabled && !d.companion_available) {
      info.innerHTML = desc + "<br/>⚠️ 已启用同步但 memory_companion 插件未加载，请确认已安装 astrbot_plugin_memory_companion。";
    } else if (d.enabled && d.companion_available) {
      info.innerHTML = desc + "<br/>✅ memory_companion 已就绪，点击「手动同步一次」可写入测试事件。";
    } else {
      info.innerHTML = desc;
    }
  } catch (e) {
    document.getElementById("syncSummary").innerHTML = `<span class="err">加载失败：${esc(e.message)}</span>`;
  }
}

async function loadConfig() {
  try {
    const res = await apiGet("config");
    const d = res.data || res;
    document.getElementById("configRaw").textContent = JSON.stringify(d, null, 2);
  } catch (e) {
    document.getElementById("configRaw").textContent = "加载失败：" + e.message;
  }
}

// ---------------------------------------------------------------------
// 标签路由
// ---------------------------------------------------------------------

function loadTab(tab) {
  switch (tab) {
    case "status": loadStatus(); break;
    case "memory": loadMemory(); break;
    case "affection": loadAffection(); break;
    case "profiles": loadProfiles(); break;
    case "personality": loadPersonality(); break;
    case "sync": loadSyncStatus(); break;
    case "config": loadConfig(); break;
    case "logs": loadSubTab("watch"); break;
  }
}

function loadSubTab(sub) {
  switch (sub) {
    case "watch": loadWatchLog(); break;
    case "dynamic": loadDynamicLog(); break;
    case "reply": loadReplyLog(); break;
    case "bangumi": loadBangumiLog(); break;
    case "schedule": loadSchedule(); break;
  }
}

// ---------------------------------------------------------------------
// 操作按钮
// ---------------------------------------------------------------------

document.getElementById("refreshBtn").addEventListener("click", () => {
  const active = document.querySelector(".tab.active");
  if (active) loadTab(active.dataset.tab);
  toast("已刷新", "success");
});

document.getElementById("startBtn").addEventListener("click", async () => {
  try {
    const res = await apiPost("actions/start");
    toast((res.data || res).msg || "已启动", "success");
    loadStatus();
  } catch (e) { toast("启动失败: " + e.message, "error"); }
});

document.getElementById("stopBtn").addEventListener("click", async () => {
  try {
    const res = await apiPost("actions/stop");
    toast((res.data || res).msg || "已停止", "success");
    loadStatus();
  } catch (e) { toast("停止失败: " + e.message, "error"); }
});

document.getElementById("refreshCookieBtn").addEventListener("click", async () => {
  try {
    const res = await apiPost("actions/refresh-cookie");
    const d = res.data || res;
    toast(d.ok ? "Cookie 刷新成功：" + d.msg : "Cookie 刷新失败：" + d.msg, d.ok ? "success" : "error");
    loadStatus();
  } catch (e) { toast("刷新失败: " + e.message, "error"); }
});

document.getElementById("syncRunBtn").addEventListener("click", async () => {
  try {
    const res = await apiPost("sync/run");
    const d = res.data || res;
    toast("同步成功：" + (d.last_sync || d.msg || "OK"), "success");
    loadSyncStatus();
  } catch (e) { toast("同步失败: " + e.message, "error"); }
});

document.getElementById("memSearchBtn").addEventListener("click", loadMemory);
document.getElementById("memClearBtn").addEventListener("click", () => {
  document.getElementById("memSearch").value = "";
  document.getElementById("memLevel").value = "";
  loadMemory();
});

// ---------------------------------------------------------------------
// 自动刷新
// ---------------------------------------------------------------------

let _timer = null;
function setupAutoRefresh() {
  const chk = document.getElementById("autoRefresh");
  function tick() {
    const active = document.querySelector(".tab.active");
    if (active && active.dataset.tab === "status") loadStatus();
  }
  function restart() {
    if (_timer) clearInterval(_timer);
    if (chk.checked) _timer = setInterval(tick, 30000);
  }
  chk.addEventListener("change", restart);
  restart();
}

// ---------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------

(async function init() {
  await ensureBridgeReady();
  loadStatus();
  setupAutoRefresh();
})();
