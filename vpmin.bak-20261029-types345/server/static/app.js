/* 单页前端：只管取数与画图，不含业务判断。 */
const $ = (id) => document.getElementById(id);
const state = { points: [], since: 0, reaction: -1 };
let roiEditor = null;
let msgUntil = 0;
const say = (text, ms = 4000) => { msgUntil = Date.now() + ms; $("hint").textContent = text; };

const TEXT = { WAITING: "等待反应", CANDIDATE_START: "起判中", STARTED: "进行中",
               CANDIDATE_END: "止判中", ENDED: "已结束", COOLDOWN: "冷却中" };

async function getJSON(url) {
  const resp = await fetch(url, { cache: "no-store" });
  return resp.json();
}

async function post(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return resp.json();
}

/* ---------- 状态 ---------- */
async function pollStatus() {
  try {
    const s = await getJSON("/api/status");
    const sys = $("sys");
    sys.textContent = "系统正常";
    sys.className = "pill online";

    const st = $("state");
    const frames = s.counts?.frames ?? 0;
    const stopText = s.stop_reason === "max_seconds" ? "已停止（超时）"
      : s.stop_reason === "manual" ? "已停止（手动）" : "已停止";
    st.textContent = s.running ? (TEXT[s.state] || s.state)
                               : (frames > 0 ? stopText : "未开始");
    st.className = "chip" + (s.running ? " run" : "");

    const r = s.reaction || {};
    $("count").textContent = r.reaction_count ?? 0;
    $("dur").textContent = r.duration_str || "--";
    $("win").textContent = (r.standard_min_sec == null)
      ? "--" : `${r.standard_min_sec}~${r.standard_max_sec}`;
    const verdict = $("verdict");
    verdict.textContent = r.success_text || "--";
    verdict.className = "v " + (r.success === false ? "bad" : (r.success === true ? "ok" : ""));

    if (Date.now() > msgUntil) {
        const sh = s.standard_health || {};
        const enabled = (s.standard || {}).enabled;
        const health = !enabled ? "本地参数（未接后端标准）"
          : sh.last_error ? `标准取数失败（累计 ${sh.fail_count || 0} 次）`
          : sh.fresh ? "标准新鲜" : "标准未更新";
        $("hint").innerHTML = `判定依据：${r.window_kind || "--"} · 已分析 ${frames} 帧`
          + ` · <span class="${enabled && !sh.fresh ? "bad" : "ok"}">${health}</span>`;
        $("hint").title = sh.last_error || "";
    }

    const cams = s.cameras || [];
    updateCamera(1, cams[0]);
    $("box2").style.display = cams.length > 1 ? "" : "none";
    if (cams[1]) updateCamera(2, cams[1]);

    renderShots(s.curve_images || []);
  } catch (_) {
    const sys = $("sys");
    sys.textContent = "系统离线";
    sys.className = "pill offline";
    $("hint").textContent = "连接中断，正在重试…";
  }
}

function updateCamera(index, cam) {
  const live = $(`live${index}`);
  const ph = $(`ph${index}`);
  const label = $(`label${index}`);
  const box = $(`box${index}`);
  if (!cam) { if (live) live.className = "live off"; return; }
  if (label) label.textContent = cam.label;
  if (live) {
    const reconnecting = (cam.message || "").includes("重连") || (cam.message || "").includes("连接");
    live.textContent = cam.connected ? "视频流" : (reconnecting ? "重连中" : "未连接");
    live.className = "live" + (cam.connected ? "" : " off");
    live.title = cam.message || "";
  }
  const frameCount = cam.frame_id ?? cam.frames ?? 0;   // 原版字段名是 frame_id
  if (ph) ph.style.display = cam.connected && frameCount > 0 ? "none" : "";
  if (index === 1) roiEditor?.redraw();   // 画面高度随分辨率变化，叠加层跟着重算
  if (index === 1) {
    const fps = cam.fps ?? cam.source_fps ?? 0;           // 原版字段名是 fps
    $("caminfo").textContent = `${cam.width}×${cam.height} @${fps}fps`;
  }
}

/* ---------- 曲线 ---------- */
async function pollCurve() {
  try {
    const c = await getJSON(`/api/curve?since=${state.since}`);
    if (c.reaction !== state.reaction) {
      state.reaction = c.reaction;
      state.points = [];
      state.since = 0;
    }
    if (c.reset) { state.points = []; state.since = 0; }
    if (c.points?.length) {
      state.points.push(...c.points);
      state.since = state.points.length;
      if (state.points.length > 20000) state.points = state.points.slice(-20000);
    }
    draw();
  } catch (_) { /* ignore */ }
}

function draw() {
  const cv = $("curve");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, M = 34;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H);

  const pts = state.points;
  const t0 = pts.length ? pts[0][0] : 0;
  const t1 = pts.length ? Math.max(pts[pts.length - 1][0], t0 + 0.001) : 1;
  const grays = pts.map((p) => p[1]);
  const g0 = pts.length ? Math.min(...grays) : 0;
  const g1 = pts.length ? Math.max(...grays, g0 + 1) : 255;

  // 网格 + 刻度
  ctx.strokeStyle = "#f0f2f6"; ctx.fillStyle = "#98a2b3";
  ctx.font = "11px 'Microsoft YaHei',Arial"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const x = M + (W - 2 * M) * i / 4;
    const y = H - M - (H - 2 * M) * i / 4;
    ctx.beginPath(); ctx.moveTo(x, M); ctx.lineTo(x, H - M); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(M, y); ctx.lineTo(W - M, y); ctx.stroke();
    if (pts.length) {
      ctx.fillText((t0 + (t1 - t0) * i / 4).toFixed(0), x - 10, H - M + 15);
      ctx.fillText((g0 + (g1 - g0) * i / 4).toFixed(0), 4, y + 4);
    }
  }
  ctx.strokeStyle = "#e6eaf0";
  ctx.strokeRect(M, M, W - 2 * M, H - 2 * M);
  ctx.fillStyle = "#98a2b3";
  ctx.fillText("时间 (s)", W / 2 - 18, H - 6);
  ctx.fillText("灰度", 4, M - 10);

  if (pts.length < 2) {
    ctx.fillStyle = "#999";
    ctx.fillText("暂无曲线（判到开始后显示）", W / 2 - 70, H / 2);
    return;
  }

  const X = (t) => M + (t - t0) / (t1 - t0) * (W - 2 * M);
  const Y = (v) => H - M - (v - g0) / (g1 - g0) * (H - 2 * M);

  ctx.beginPath();
  pts.forEach(([t, v], i) => (i ? ctx.lineTo(X(t), Y(v)) : ctx.moveTo(X(t), Y(v))));
  ctx.strokeStyle = "#1677ff"; ctx.lineWidth = 1.8; ctx.stroke();

  // 面积
  ctx.lineTo(X(t1), H - M); ctx.lineTo(X(t0), H - M); ctx.closePath();
  ctx.fillStyle = "rgba(22,119,255,0.08)"; ctx.fill();
}

/* ---------- 事件 / 存档 ---------- */
async function pollEvents() {
  try {
    const data = await getJSON("/api/events?limit=40");
    const rows = (data.events || []).slice().reverse();   // 后端正序，界面最新在前（同原版前端）
    $("eventCount").textContent = `${rows.length} 条`;
    $("events").innerHTML = rows.length
      ? rows.map((e) => `<tr>
          <td><span class="dot ${e.event_type === "START" ? "start" : "end"}"></span>${e.event_type}</td>
          <td class="num">${e.time_sec.toFixed(2)}</td>
          <td class="num">${e.mean_gray.toFixed(1)}</td>
          <td class="num">${e.duration_sec == null ? "--" : e.duration_sec.toFixed(2)}</td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="empty">暂无事件</td></tr>`;
  } catch (_) { /* ignore */ }
}

function renderShots(list) {
  $("shotCount").textContent = `${list.length} 张`;
  $("shots").innerHTML = list.length
    ? list.slice(0, 24).map((i) =>
        `<a href="/images/${i.file}" target="_blank">#${i.index}${i.partial ? " · 半" : ""}</a>`).join("")
    : `<span class="empty">暂无存档</span>`;
}

/* ---------- 交互 ---------- */
$("start").onclick = async () => { await post("/api/start"); pollStatus(); };
$("stop").onclick = async () => { await post("/api/stop"); pollStatus(); };
$("refresh").onclick = async () => { await post("/api/standard/refresh"); pollStatus(); };

$("params").onsubmit = async (ev) => {
  ev.preventDefault();
  const body = {};
  new FormData(ev.target).forEach((v, k) => { if (v !== "") body[k] = Number(v); });
  const out = await post("/api/params", body);
  say(out.params ? "参数已保存（下一帧生效）" : "保存失败");
};

/* ---------- ROI ---------- */
function roiLabel(roi) {
  if (!roi) return "ROI：未设置";
  if (roi.kind === "polygon") return `ROI：多边形 ${roi.points?.length || 0} 点`;
  const r = roi.rect || roi.bbox;
  return r ? `ROI：x=${r[0]} y=${r[1]} w=${r[2]} h=${r[3]}` : "ROI：未设置";
}

async function loadRois(selectFirst) {
  try {
    const data = await getJSON("/api/rois");
    const list = data.rois || [];
    const sel = $("roiHistory");
    sel.innerHTML = `<option value="">历史 ROI（${list.length}）…</option>` +
      list.map((r) => `<option value="${r.id}">${r.shape} · ${r.created_at.slice(5, 16)}</option>`).join("");
    if (selectFirst && list.length) sel.value = list[0].id;
    return list;
  } catch (_) { return []; }
}

async function applyRoi(roi) {
  const out = await post("/api/roi", roi);
  if (out.roi) {
    roiEditor.setRoi(out.roi);
    $("roiInfo").textContent = roiLabel(out.roi);
    say("ROI 已更新（下一帧生效）");
    loadRois(true);
  } else {
    say(out.error || "ROI 保存失败");
  }
}

function initRoi() {
  roiEditor = new RoiEditor($("view"), $("overlay"), applyRoi);

  const toggle = (mode) => {
    const next = roiEditor.mode === mode ? null : mode;
    if (next === "edit" && !roiEditor.roi) { say("还没有 ROI，先画一个"); return; }
    roiEditor.setMode(next);
    $("roiRect").className = "btn" + (next === "rect" ? " on" : "");
    $("roiPoly").className = "btn" + (next === "polygon" ? " on" : "");
    $("roiEdit").className = "btn" + (next === "edit" ? " on" : "");
    say(next === "rect" ? "在画面上按住鼠标拖出矩形 ROI"
      : next === "polygon" ? "逐点单击画多边形，点回起点 / 双击 / 回车收尾"
      : next === "edit" ? "拖黄块改顶点/边、拖框内平移；多边形双击边可加点；Ctrl+Z 撤销、Ctrl+Shift+Z 重做；Esc 退出"
      : "", next ? 30000 : 1);
  };

  $("roiRect").onclick = () => toggle("rect");
  $("roiPoly").onclick = () => toggle("polygon");
  $("roiEdit").onclick = () => toggle("edit");
  $("roiUndo").onclick = () => { if (roiEditor.mode !== "edit") say("先点「编辑坐标」"); else roiEditor.undo(); };
  $("roiRedo").onclick = () => { if (roiEditor.mode !== "edit") say("先点「编辑坐标」"); else roiEditor.redo(); };
  $("roiCancel").onclick = () => toggle(null);

  $("roiApply").onclick = async () => {
    const id = $("roiHistory").value;
    if (!id) { say("先选一条历史 ROI"); return; }
    const out = await post("/api/rois/apply", { id });
    if (out.roi) {
      roiEditor.setRoi(out.roi);
      $("roiInfo").textContent = roiLabel(out.roi);
      say("已套用历史 ROI（下一帧生效）");
    } else {
      say(out.error || "套用失败");
    }
  };
}

/* ---------- 启动 ---------- */
async function loadSettings() {
  try {
    const s = await getJSON("/api/settings");
    for (const [k, v] of Object.entries(s.params || {})) {
      const input = document.querySelector(`#params [name="${k}"]`);
      if (input) input.value = v;
    }
    if (roiEditor) {
      roiEditor.setRoi(s.roi);
      $("roiInfo").textContent = s.roi_summary || roiLabel(s.roi);
    }
    const list = await loadRois(false);
    if (!list.length && s.roi) await post("/api/roi", s.roi);   // 首次：把当前 ROI 存进历史
  } catch (_) { /* ignore */ }
}

initRoi();
loadSettings();
pollStatus(); pollCurve(); pollEvents();
setInterval(pollStatus, 1000);
setInterval(pollCurve, 500);
setInterval(pollEvents, 3000);
