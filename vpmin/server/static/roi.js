/* ROI 绘制/编辑：矩形拖拽 + 多边形点选 + 已有 ROI 拖动编辑。只负责「画」和「坐标换算」，不碰网络。
   坐标换算：<img> 用 object-fit:contain，画面在元素里居中留边，
   所以按 naturalWidth/Height 与元素尺寸先算出真实显示区域，否则 ROI 会错位。 */
const HISTORY_LIMIT = 50;

class RoiEditor {
  constructor(img, canvas, onApply) {
    this.img = img;
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onApply = onApply || (() => {});
    this.roi = null;              // 已生效的 ROI（用于显示）
    this.mode = null;             // null | 'rect' | 'polygon' | 'edit'
    this.drag = null;             // 矩形拖拽中 {x0,y0,x1,y1}（帧坐标）
    this.points = [];             // 多边形已点下的顶点（帧坐标）
    this.cursor = null;           // 多边形当前光标（帧坐标）
    this.lastClickAt = 0;
    this.lastClickPos = null;   // 上一次点击的屏幕坐标（区分「双击收尾」和「快速点两个顶点」）
    this.edit = null;             // 编辑中的副本 {kind, rect|points}
    this.grab = null;             // 编辑拖拽中 {hit, inside, start, origin, handle}
    this.hover = null;            // 悬停中的把手索引
    this.history = [];            // 编辑会话内的撤销栈（JSON 快照，上限 HISTORY_LIMIT）
    this.future = [];             // 重做栈

    canvas.addEventListener('pointerdown', (e) => this._down(e));
    canvas.addEventListener('pointermove', (e) => this._move(e));
    canvas.addEventListener('pointerup', (e) => this._up(e));
    canvas.addEventListener('dblclick', (e) => this._dblclick(e));
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (this.mode === 'edit') this._cancelEdit(); else this.setMode(null);
      }
      if (e.key === 'Enter' && this.mode === 'polygon') this._finishPolygon();
      if (this.mode === 'edit' && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) this.redo(); else this.undo();
      }
      if (this.mode === 'edit' && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        this.redo();
      }
    });
    window.addEventListener('resize', () => this.redraw());
    this.redraw();
  }

  setRoi(roi) {
    this.roi = roi || null;
    if (this.mode === 'edit') this._beginEdit();   // 外部换了 ROI，编辑副本跟着重建
    this.redraw();
  }

  setMode(mode) {
    this.mode = mode || null;
    this.drag = null;
    this.points = [];
    this.cursor = null;
    this.lastClickPos = null;
    this.grab = null;
    this.hover = null;
    if (this.mode === 'edit') {
      this.history = [];            // 每次进入编辑是全新会话
      this.future = [];
      this._beginEdit();
    } else { this.edit = null; this.canvas.style.cursor = 'default'; }
    this.canvas.classList.toggle('draw', !!this.mode);
    this.redraw();
  }

  /* ---------- 编辑 ---------- */
  _clone(roi) {
    if (!roi) return null;
    if (roi.kind === 'polygon') {
      return { kind: 'polygon', points: (roi.points || []).map((p) => [p[0], p[1]]) };
    }
    const r = roi.rect || roi.bbox;
    return r ? { kind: 'rect', rect: r.slice(0, 4) } : null;
  }

  _beginEdit() {
    this.edit = this._clone(this.roi);
    this.grab = null;
    this.hover = null;
    if (!this.edit) this.mode = null;
    this.canvas.classList.toggle('draw', !!this.mode);
  }

  /* ---------- 撤销 / 重做 ---------- */
  _snapshot() {
    return this.edit ? JSON.stringify(this.edit) : null;
  }

  _pushUndo() {
    const snap = this._snapshot();
    if (!snap) return;
    this.history.push(snap);
    if (this.history.length > HISTORY_LIMIT) this.history.shift();
    this.future = [];               // 新动作让重做栈失效
  }

  undo() {
    if (!this.history.length || !this.edit) return;
    this.future.push(this._snapshot());
    this.edit = JSON.parse(this.history.pop());
    this._commit();
  }

  redo() {
    if (!this.future.length || !this.edit) return;
    this.history.push(this._snapshot());
    this.edit = JSON.parse(this.future.pop());
    this._commit();
  }

  /* 双击多边形的一条边 → 在该处插入顶点（帧坐标吸附到边上的投影点） */
  _dblclick(e) {
    if (this.mode !== 'edit' || !this.edit || this.edit.kind !== 'polygon') return;
    e.preventDefault();
    const p = this.toFrame(e.clientX, e.clientY);
    const hit = this._nearestEdge(p);
    if (!hit) return;
    this._pushUndo();
    this.edit.points.splice(hit.index + 1, 0, [hit.x, hit.y]);
    this._commit();
  }

  /* 最近的边：返回 {index, x, y, dist(屏幕像素)}；超过 10px 视为没点到 */
  _nearestEdge(p) {
    const pts = this.edit.points;
    const scale = this.metrics().scale || 1;
    let best = null;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((p.x - a[0]) * dx + (p.y - a[1]) * dy) / len2)) : 0;
      const x = a[0] + t * dx, y = a[1] + t * dy;
      const dist = Math.hypot(p.x - x, p.y - y) * scale;   // 换算成屏幕像素
      if (dist <= 10 && (!best || dist < best.dist)) best = { index: i, x: Math.round(x), y: Math.round(y), dist };
    }
    return best;
  }

  _cancelEdit() {
    this.edit = null;
    this.grab = null;
    this.hover = null;
    this.setMode(null);
  }

  _commit() {
    if (!this.edit) return;
    const roi = this._clone(this.edit);
    if (roi) {
      this.roi = roi;                 // 先本地生效，再交给上层落库
      this.onApply(roi);
    }
    this.redraw();
  }

  /* 编辑把手（全部用帧坐标）：矩形 8 个，多边形每顶点 1 个 */
  _handles() {
    if (!this.edit) return [];
    if (this.edit.kind === 'polygon') {
      return this.edit.points.map((p, i) => ({ type: 'vertex', idx: i, x: p[0], y: p[1] }));
    }
    const [x, y, w, h] = this.edit.rect;
    const cx = x + w / 2, cy = y + h / 2;
    return [
      { type: 'corner', idx: 0, x, y },
      { type: 'corner', idx: 1, x: x + w, y },
      { type: 'corner', idx: 2, x: x + w, y: y + h },
      { type: 'corner', idx: 3, x, y: y + h },
      { type: 'edge', idx: 4, x: cx, y },
      { type: 'edge', idx: 5, x: x + w, y: cy },
      { type: 'edge', idx: 6, x: cx, y: y + h },
      { type: 'edge', idx: 7, x, y: cy },
    ];
  }

  _hitHandle(clientX, clientY) {
    const handles = this._handles();
    for (let i = 0; i < handles.length; i++) {
      const h = handles[i];
      const c = this.toCanvas(h.x, h.y);
      const { box } = this.metrics();
      if (Math.hypot(clientX - box.left - c.x, clientY - box.top - c.y) <= 9) return i;
    }
    return -1;
  }

  _insideRoi(fx, fy) {
    if (!this.edit) return false;
    if (this.edit.kind === 'polygon') {
      const pts = this.edit.points;
      let inside = false;
      for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const [xi, yi] = pts[i], [xj, yj] = pts[j];
        if ((yi > fy) !== (yj > fy) && fx < ((xj - xi) * (fy - yi)) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    }
    const [x, y, w, h] = this.edit.rect;
    return fx >= x && fx <= x + w && fy >= y && fy <= y + h;
  }

  _startEdit(e, p) {
    const hit = this._hitHandle(e.clientX, e.clientY);
    const inside = this._insideRoi(p.x, p.y);
    if (hit < 0 && !inside) return false;
    this.canvas.setPointerCapture?.(e.pointerId);
    this._pushUndo();
    const h = hit >= 0 ? this._handles()[hit] : null;
    this.grab = {
      hit,
      inside,
      start: [p.x, p.y],
      origin: this._clone(this.edit),
      handle: h ? { type: h.type, idx: h.idx } : null,
    };
    this.canvas.style.cursor = 'grabbing';
    return true;
  }

  _dragEdit(p) {
    const g = this.grab;
    if (!g || !this.edit) return;
    const dx = p.x - g.start[0], dy = p.y - g.start[1];

    if (this.edit.kind === 'polygon') {
      if (g.handle && g.handle.type === 'vertex') {
        this.edit.points[g.handle.idx] = [
          Math.max(0, Math.min(this.img.naturalWidth || 1e9, p.x)),
          Math.max(0, Math.min(this.img.naturalHeight || 1e9, p.y)),
        ];
      } else {
        this.edit.points = g.origin.points.map(([x, y]) => [
          Math.max(0, Math.min(this.img.naturalWidth || 1e9, x + dx)),
          Math.max(0, Math.min(this.img.naturalHeight || 1e9, y + dy)),
        ]);
      }
      this.redraw();
      return;
    }

    let [x, y, w, h] = g.origin.rect;
    let left = x, top = y, right = x + w, bottom = y + h;
    const maxW = this.img.naturalWidth || 1e9, maxH = this.img.naturalHeight || 1e9;
    const hd = g.handle;
    if (!hd) {                                   // 整体平移
      left = Math.max(0, Math.min(maxW - w, x + dx));
      top = Math.max(0, Math.min(maxH - h, y + dy));
      right = left + w; bottom = top + h;
    } else {
      const px = Math.max(0, Math.min(maxW, p.x));
      const py = Math.max(0, Math.min(maxH, p.y));
      const k = hd.type === 'corner' ? hd.idx : hd.idx - 4;
      if (hd.type === 'corner') {
        if (k === 0 || k === 3) left = Math.min(px, right - 8);
        if (k === 1 || k === 2) right = Math.max(px, left + 8);
        if (k === 0 || k === 1) top = Math.min(py, bottom - 8);
        if (k === 2 || k === 3) bottom = Math.max(py, top + 8);
      } else {
        if (hd.idx === 4) top = Math.min(py, bottom - 8);       // 上边
        if (hd.idx === 5) right = Math.max(px, left + 8);       // 右边
        if (hd.idx === 6) bottom = Math.max(py, top + 8);       // 下边
        if (hd.idx === 7) left = Math.min(px, right - 8);       // 左边
      }
    }
    this.edit.rect = [Math.round(left), Math.round(top),
                      Math.round(right - left), Math.round(bottom - top)];
    this.redraw();
  }

  /* ---------- 坐标换算 ----------
     视频用 object-fit: contain：画面按比例缩放并居中留边，
     所以先求出真实显示区域，再按统一 scale 换算（X/Y 同比例，不会错位）。 */
  metrics() {
    const box = this.img.getBoundingClientRect();
    const nw = this.img.naturalWidth || 1;
    const nh = this.img.naturalHeight || 1;
    const scale = Math.min(box.width / nw, box.height / nh);
    return { box, nw, nh, scale,
             ox: (box.width - nw * scale) / 2, oy: (box.height - nh * scale) / 2 };
  }

  toFrame(clientX, clientY) {
    const { box, nw, nh, scale, ox, oy } = this.metrics();
    const fx = (clientX - box.left - ox) / scale;
    const fy = (clientY - box.top - oy) / scale;
    return {
      x: Math.round(Math.min(Math.max(fx, 0), nw)),
      y: Math.round(Math.min(Math.max(fy, 0), nh)),
    };
  }

  toCanvas(fx, fy) {
    const { scale, ox, oy } = this.metrics();
    return { x: ox + fx * scale, y: oy + fy * scale };
  }

  /* ---------- 指针 ---------- */
  _down(e) {
    if (!this.mode) return;
    if (this.mode === 'edit') {
      const p = this.toFrame(e.clientX, e.clientY);
      this._startEdit(e, p);
      this.redraw();
      return;
    }
    if (this.mode === 'rect') {
      this.canvas.setPointerCapture?.(e.pointerId);
      const p = this.toFrame(e.clientX, e.clientY);
      this.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      this.redraw();
      return;
    }

    // 多边形：点一个顶点；点回起点 / 双击 / 回车 收尾
    const p = this.toFrame(e.clientX, e.clientY);
    const c = this.toCanvas(p.x, p.y);
    const now = performance.now();

    if (this.points.length >= 3) {
      const first = this.toCanvas(this.points[0][0], this.points[0][1]);
      if (Math.hypot(c.x - first.x, c.y - first.y) < 12) { this._finishPolygon(); return; }
    }
    const last = this.lastClickPos;
    const near = last && Math.hypot(c.x - last.x, c.y - last.y) < 10;
    if (now - this.lastClickAt < 300 && near && this.points.length >= 3) { this._finishPolygon(); return; }

    this.points.push([p.x, p.y]);
    this.lastClickAt = now;
    this.lastClickPos = c;
    this.cursor = [p.x, p.y];
    this.redraw();
  }

  _move(e) {
    if (!this.mode) return;
    const p = this.toFrame(e.clientX, e.clientY);
    if (this.mode === 'edit') {
      if (this.grab) { this._dragEdit(p); return; }
      const hit = this._hitHandle(e.clientX, e.clientY);
      this.hover = hit >= 0 ? hit : null;
      this.canvas.style.cursor = hit >= 0 ? 'grab'
        : (this._insideRoi(p.x, p.y) ? 'move' : 'default');
      this.redraw();
      return;
    }
    if (this.mode === 'rect' && this.drag) {
      this.drag.x1 = p.x;
      this.drag.y1 = p.y;
      this.redraw();
    } else if (this.mode === 'polygon' && this.points.length) {
      this.cursor = [p.x, p.y];
      this.redraw();
    }
  }

  _up(e) {
    if (this.mode === 'edit') {
      if (this.grab) {
        this.grab = null;
        this.canvas.style.cursor = 'grab';
        this._commit();
      }
      return;
    }
    if (this.mode !== 'rect' || !this.drag) return;
    const p = this.toFrame(e.clientX, e.clientY);
    const d = this.drag;
    this.drag = null;

    const x = Math.min(d.x0, p.x), y = Math.min(d.y0, p.y);
    const w = Math.abs(p.x - d.x0), h = Math.abs(p.y - d.y0);
    if (w < 8 || h < 8) { this.redraw(); return; }

    const roi = { kind: 'rect', rect: [x, y, w, h] };
    this.setMode(null);
    this.onApply(roi);
  }

  _finishPolygon() {
    if (this.points.length < 3) { this.setMode(null); return; }
    const roi = { kind: 'polygon', points: this.points.map((p) => [p[0], p[1]]) };
    this.setMode(null);
    this.onApply(roi);
  }

  /* ---------- 画 ---------- */
  _resize() {
    const box = this.img.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.round(box.width * dpr));
    this.canvas.height = Math.max(1, Math.round(box.height * dpr));
    this.canvas.style.width = box.width + 'px';
    this.canvas.style.height = box.height + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  redraw() {
    this._resize();
    const ctx = this.ctx;
    const box = this.img.getBoundingClientRect();
    ctx.clearRect(0, 0, box.width, box.height);

    if (this.mode === 'edit' && this.edit) {
      this._drawShape(this.edit, '#1890ff', 2);
      this._drawHandles();
    } else if (this.roi) {
      this._drawShape(this.roi, '#1890ff', 2);
    }

    if (this.mode === 'rect' && this.drag) {
      const d = this.drag;
      const a = this.toCanvas(Math.min(d.x0, d.x1), Math.min(d.y0, d.y1));
      const b = this.toCanvas(Math.max(d.x0, d.x1), Math.max(d.y0, d.y1));
      this._dashedRect(a, b);
    }

    if (this.mode === 'polygon' && this.points.length) {
      const pts = this.points.map(([x, y]) => this.toCanvas(x, y));
      ctx.strokeStyle = '#1890ff';
      ctx.fillStyle = 'rgba(24,144,255,0.12)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      if (this.cursor) {
        const c = this.toCanvas(this.cursor[0], this.cursor[1]);
        ctx.lineTo(c.x, c.y);
      }
      ctx.stroke();

      pts.forEach((p) => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.strokeStyle = '#1890ff';
        ctx.stroke();
      });
      const first = pts[0];
      ctx.beginPath();
      ctx.arc(first.x, first.y, 7, 0, Math.PI * 2);
      ctx.strokeStyle = '#c62828';
      ctx.stroke();
    }
  }

  /* 编辑把手：黄底蓝框，悬停/拖动时放大变红，方便对准 */
  _drawHandles() {
    const ctx = this.ctx;
    const handles = this._handles();
    handles.forEach((h, i) => {
      const c = this.toCanvas(h.x, h.y);
      const active = (this.grab && this.grab.handle &&
                      this.grab.handle.type === h.type && this.grab.handle.idx === h.idx) ||
                     this.hover === i;
      const r = active ? 6 : 4.5;
      ctx.beginPath();
      ctx.rect(c.x - r, c.y - r, r * 2, r * 2);
      ctx.fillStyle = active ? '#c62828' : '#ffdd00';
      ctx.fill();
      ctx.strokeStyle = '#1e70c9';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });
  }

  _dashedRect(a, b) {
    const ctx = this.ctx;
    ctx.strokeStyle = '#1890ff';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.setLineDash([]);
  }

  _drawShape(roi, color, width) {
    const ctx = this.ctx;
    ctx.strokeStyle = color;
    ctx.fillStyle = 'rgba(24,144,255,0.12)';
    ctx.lineWidth = width;

    if (roi.kind === 'polygon' && roi.points?.length >= 3) {
      ctx.beginPath();
      roi.points.forEach(([x, y], i) => {
        const p = this.toCanvas(x, y);
        i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      return;
    }

    const rect = roi.rect || roi.bbox;
    if (!rect) return;
    const [x, y, w, h] = rect;
    const a = this.toCanvas(x, y);
    const b = this.toCanvas(x + w, y + h);
    ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
  }
}
