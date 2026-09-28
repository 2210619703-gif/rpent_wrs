const $ = (id) => document.getElementById(id);

const transcript = $("transcript");
const timeline = $("timeline");
let afterId = 0;
let sessionStatus = "idle";
let evN = 0;
let stepN = 0;
let toolGroup = null;

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function fmtArgs(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

function short(value) {
  const text = fmtArgs(value);
  return text.length > 180 ? `${text.slice(0, 177)}…` : text;
}

function setBadge(status) {
  const el = $("statusBadge");
  el.className = "badge";
  const map = {
    idle: ["b-stale", "空闲"],
    running: ["b-running", "运行中"],
    succeeded: ["b-succeeded", "成功"],
    failed: ["b-failed", "失败"],
  };
  const [cls, label] = map[status] || ["b-stale", status];
  el.classList.add(cls);
  el.textContent = label;
}

function ensureTranscript() {
  if (transcript.querySelector(".empty")) transcript.innerHTML = "";
}

function bumpEvents() {
  evN += 1;
  $("evCount").textContent = evN ? `${evN} 条事件` : "";
  if ($("autoscroll").checked) transcript.scrollTop = transcript.scrollHeight;
}

function addEv(cls, html, attrs) {
  toolGroup = null;
  ensureTranscript();
  const div = document.createElement("div");
  div.className = `ev ${cls}`;
  div.innerHTML = html;
  if (attrs) Object.assign(div.dataset, attrs);
  transcript.appendChild(div);
  bumpEvents();
  return div;
}

function makeToolEl(type, tool, payload) {
  const div = document.createElement("div");
  div.className = `ev ${type}`;
  const name = document.createElement("span");
  name.className = "tname";
  if (type === "tool_result" && payload && payload.is_error) name.classList.add("err");
  name.textContent = tool || "";
  const args = document.createElement("span");
  args.className = "args";
  args.textContent = fmtArgs(payload);
  div.append(type === "tool_call" ? "→ " : "← ", name, " ", args);
  return div;
}

function appendTool(type, tool, payload) {
  ensureTranscript();
  if (!toolGroup) {
    const box = document.createElement("div");
    box.className = "toolgroup";
    box.innerHTML = `<div class="tg-head"><span class="tg-count">0</span> 次工具调用</div><div class="tg-body"></div>`;
    box.querySelector(".tg-head").addEventListener("click", () => box.classList.toggle("open"));
    transcript.appendChild(box);
    toolGroup = box;
  }
  toolGroup.querySelector(".tg-body").appendChild(makeToolEl(type, tool, payload));
  const n = toolGroup.querySelectorAll(".tg-body .ev.tool_call").length;
  toolGroup.querySelector(".tg-count").textContent = String(n);
  bumpEvents();
}

function resetView() {
  transcript.innerHTML = `<div class="empty">在下方输入任务并启动后，这里会显示管家过程。</div>`;
  timeline.innerHTML = `<div class="empty">还没有动作。</div>`;
  $("clipTimeline").innerHTML = "";
  $("clipTimeline").classList.add("hidden");
  timeline.classList.remove("hidden");
  $("replayBar").classList.add("hidden");
  $("replayBtn").textContent = "查看动作回放";
  evN = 0;
  stepN = 0;
  toolGroup = null;
  $("evCount").textContent = "";
  $("stepCount").textContent = "";
  $("resultBadge").style.display = "none";
}

function addStep(name, detail) {
  if (timeline.querySelector(".empty")) timeline.innerHTML = "";
  stepN += 1;
  const row = document.createElement("div");
  row.className = "step";
  row.innerHTML = `<div class="idx">${stepN}</div><div class="body"><div class="act">${esc(name)}</div><div class="det">${esc(detail)}</div></div>`;
  timeline.appendChild(row);
  $("stepCount").textContent = `${stepN}`;
}

function handle(ev) {
  if (ev.type === "start") {
    resetView();
    addEv("user", esc(ev.instruction || "开始"), { roleLabel: "你" });
    $("taskMeta").textContent = ev.instruction || "";
    if (ev.scene_title) $("rt-scene").textContent = ev.scene_title;
    $("rt-steward").className = "runtime-item runtime-ready";
  } else if (ev.type === "turn") {
    addEv("turn", `回合 ${ev.n}/${ev.max || "?"}`);
  } else if (ev.type === "model") {
    addEv("text", esc(ev.content));
  } else if (ev.type === "operator") {
    addEv("user", esc(ev.text), { roleLabel: "你" });
  } else if (ev.type === "nudge") {
    addEv("text", "模型没有调工具，已催促。");
  } else if (ev.type === "tool_call") {
    appendTool("tool_call", ev.tool, ev.args);
    addStep(ev.tool, short(ev.args));
  } else if (ev.type === "tool_result") {
    appendTool("tool_result", ev.tool, ev.result);
  } else if (ev.type === "done") {
    const ok = !!ev.solved;
    $("resultBadge").style.display = "";
    $("resultBadge").className = `badge ${ok ? "b-ok" : "b-fail"}`;
    $("resultBadge").textContent = ok ? "scene_success" : (ev.error || "未成功");
    addEv("text", ok ? "任务成功（场景判定）。" : esc(ev.error || ev.message || "未成功"));
    refreshState();
  }
}

async function poll() {
  try {
    const res = await fetch(`/api/events?after=${afterId}`);
    const data = await res.json();
    for (const ev of data.events || []) {
      afterId = Math.max(afterId, ev.id);
      handle(ev);
    }
    $("connMeta").textContent = "已连接";
  } catch {
    $("connMeta").textContent = "重连中…";
  }
  setTimeout(poll, 400);
}

let scenes = [];
let monitorOpen = false;
let stayOnLauncher = false;

function selectedScene() {
  return scenes.find((item) => item.id === $("f-scene").value) || scenes[0];
}

function renderSceneHint(spec) {
  if (!spec) {
    $("sceneHint").textContent = "还没有可选场景。";
    return;
  }
  const lines = [
    `<b>${esc(spec.title)}</b>：${esc(spec.summary)}`,
    `物体：${esc((spec.objects || []).join("、"))}`,
  ];
  if (spec.places && spec.places.length) {
    lines.push(`放置点：${esc(spec.places.join("、"))}`);
  }
  $("sceneHint").innerHTML = lines.join("<br />");
  const example = (spec.examples || [])[0] || "";
  $("chatInput").placeholder = example
    ? `在这里自己输入任务，例如：${example}`
    : "在这里自己输入任务";
  const note = spec.instruction_note || "运行中可输入 pause / resume，或直接打新目标。";
  $("monitorExamples").innerHTML = [
    `当前场景 ${esc(spec.title)}。例如：${esc((spec.examples || []).join(" · "))}`,
    esc(note),
  ].join("<br />");
  $("rt-scene").textContent = spec.title;
}

function fillScenes(list, currentId) {
  if (!list || !list.length || $("f-scene").dataset.ready) return;
  scenes = list;
  $("f-scene").innerHTML = "";
  for (const item of list) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = item.title;
    $("f-scene").appendChild(opt);
  }
  $("f-scene").value = currentId || list[0].id;
  $("f-scene").dataset.ready = "1";
  renderSceneHint(selectedScene());
}

function showMonitor(viewerUrl) {
  stayOnLauncher = false;
  monitorOpen = true;
  $("launcher").classList.add("hidden");
  $("app").classList.remove("hidden");
  document.title = "rpent_wrs · 实时监控";
  const frame = $("sceneFrame");
  const url = viewerUrl || "http://127.0.0.1:8000/";
  $("openViewer").href = url;
  if (frame.dataset.loaded !== url) {
    frame.src = url;
    frame.dataset.loaded = url;
  }
}

function showLauncher() {
  stayOnLauncher = true;
  monitorOpen = false;
  $("app").classList.add("hidden");
  $("launcher").classList.remove("hidden");
  document.title = "rpent_wrs · 新建会话";
}

async function refreshState() {
  try {
    const data = await (await fetch("/api/state")).json();
    const shown = data.model.includes(":") ? data.model : `openai-chat:${data.model}`;
    if (!$("f-model").dataset.touched) $("f-model").value = shown;
    const preset = $("f-model-preset");
    if ([...preset.options].some((opt) => opt.value === shown)) preset.value = shown;
    if (data.defaults) {
      if (!$("f-max-turns").dataset.touched) $("f-max-turns").value = data.defaults.max_turns;
      if (!$("f-timeout").dataset.touched) $("f-timeout").value = data.defaults.timeout;
    }
    fillScenes(data.scenes, data.scene_id);
    sessionStatus = data.status;
    setBadge(data.status);
    const spec = scenes.find((item) => item.id === data.scene_id) || selectedScene();
    if (spec) {
      if (monitorOpen) renderSceneHint(spec);
      $("rt-scene").textContent = spec.title;
      $("rt-scene").className = "runtime-item runtime-ready";
    }
    const goal = data.scene?.goal;
    if (goal) {
      $("rt-goal").textContent = `${goal.object} → ${goal.place}`;
      $("rt-goal").className = `runtime-item ${data.scene.scene_success ? "runtime-ready" : "runtime-pending"}`;
    }
    $("runBtn").textContent = data.status === "running" ? "发送新目标" : "启动";
    $("runBtn").disabled = false;
    $("interactionStatus").textContent = data.inbox?.paused
      ? "已暂停。输入 resume 继续，或输入新目标。"
      : data.status === "running"
        ? "运行中：输入新目标，或 pause / resume。"
        : "输入任务后点启动。";
    if (data.revealed && !monitorOpen && !stayOnLauncher) showMonitor(data.viewer_url);
    const clips = data.scene?.replay?.clips || [];
    const canReplay = data.status !== "running" && clips.length > 0;
    const clipOpen = !$("clipTimeline").classList.contains("hidden");
    $("replayBar").classList.toggle("hidden", !(canReplay || clipOpen));
    const playing = data.scene?.replay?.clip;
    $("frameCap").textContent = playing
      ? `正在回放 ${playing}`
      : "F 单步 · G 播放 · R 重来 · P 暂停管家";
    document.querySelectorAll("#clipTimeline .step").forEach((row) => {
      row.classList.toggle("playing", row.dataset.name === playing);
    });
  } catch {
    $("rt-scene").className = "runtime-item runtime-failed";
  }
}

$("showtools").onchange = () => transcript.classList.toggle("alltools", $("showtools").checked);
$("f-scene").onchange = () => renderSceneHint(selectedScene());
$("f-max-turns").addEventListener("input", () => { $("f-max-turns").dataset.touched = "1"; });
$("f-timeout").addEventListener("input", () => { $("f-timeout").dataset.touched = "1"; });
$("f-model").addEventListener("input", () => { $("f-model").dataset.touched = "1"; });
$("f-model-preset").addEventListener("change", () => {
  $("f-model").value = $("f-model-preset").value;
  $("f-model").dataset.touched = "1";
});

async function openScene() {
  $("launchBtn").disabled = true;
  $("launchStatus").textContent = "正在打开场景…";
  const res = await fetch("/api/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scene_id: $("f-scene").value,
      max_turns: Number($("f-max-turns").value),
      timeout: Number($("f-timeout").value),
      model: $("f-model").value.trim(),
    }),
  });
  const data = await res.json();
  $("launchBtn").disabled = false;
  if (!data.ok) {
    $("launchStatus").textContent = data.error || "打开失败";
    return;
  }
  $("launchStatus").textContent = "";
  showMonitor();
  refreshState();
}

async function submitTask() {
  const instruction = $("chatInput").value.trim();
  if (!instruction) return;
  $("runBtn").disabled = true;
  if (sessionStatus === "running") {
    const note = await fetch("/api/inbox", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: instruction }),
    });
    const data = await note.json();
    $("inboxStatus").textContent = data.ok ? "已发给管家…" : (data.error || "发送失败");
    $("inboxStatus").classList.toggle("is-error", !data.ok);
    if (data.ok) $("chatInput").value = "";
    $("runBtn").disabled = false;
    refreshState();
    return;
  }
  $("inboxStatus").textContent = "正在启动…";
  $("inboxStatus").classList.remove("is-error");
  const res = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      instruction,
      max_turns: Number($("f-max-turns").value),
      timeout: Number($("f-timeout").value),
      scripted: $("f-scripted").checked,
    }),
  });
  const data = await res.json();
  $("inboxStatus").textContent = data.ok ? "" : (data.error || "启动失败");
  $("inboxStatus").classList.toggle("is-error", !data.ok);
  $("runBtn").disabled = false;
  refreshState();
}

function renderClipTimeline(clips) {
  const box = $("clipTimeline");
  box.innerHTML = "";
  clips.forEach((clip, i) => {
    const row = document.createElement("div");
    row.className = "step hasclip";
    row.dataset.name = clip.name;
    row.innerHTML = `<div class="idx">${i + 1}</div><div class="body"><div class="act">${esc(clip.title)}</div><div class="det">${esc(clip.detail || `${clip.waypoints} 路点`)}</div></div>`;
    row.onclick = () => playClip({ id: clip.id });
    box.appendChild(row);
  });
  const all = document.createElement("div");
  all.className = "step episode";
  all.dataset.name = "episode";
  all.innerHTML = `<div class="idx">全</div><div class="body"><div class="act">完整回放</div><div class="det">整段抓放过程</div></div>`;
  all.onclick = () => playClip({ whole: true });
  box.appendChild(all);
  $("stepCount").textContent = `${clips.length + 1}`;
}

function showClipTimeline(clips) {
  timeline.classList.add("hidden");
  $("clipTimeline").classList.remove("hidden");
  renderClipTimeline(clips);
  $("replayBtn").textContent = "返回动作时间线";
}

function showLiveTimeline() {
  $("clipTimeline").classList.add("hidden");
  timeline.classList.remove("hidden");
  $("replayBtn").textContent = "查看动作回放";
  $("stepCount").textContent = stepN ? `${stepN}` : "";
}

async function playClip(payload) {
  await fetch("/api/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  refreshState();
}

$("replayBtn").onclick = async () => {
  if (!$("clipTimeline").classList.contains("hidden")) {
    showLiveTimeline();
    return;
  }
  const data = await (await fetch("/api/state")).json();
  const clips = data.scene?.replay?.clips || [];
  if (!clips.length) return;
  showClipTimeline(clips);
};

$("launchBtn").onclick = openScene;
$("runBtn").onclick = submitTask;
$("newSessionBtn").onclick = () => {
  if (sessionStatus === "running") {
    $("inboxStatus").textContent = "当前还在跑，先 pause 或等它结束。";
    return;
  }
  showLauncher();
};
$("chatInput").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || ev.shiftKey) return;
  ev.preventDefault();
  submitTask();
});

refreshState();
setInterval(refreshState, 1500);
poll();
