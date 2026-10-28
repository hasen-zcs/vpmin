/* ROI 编辑回归（真浏览器）：顶点/边/角拖动、整体平移、撤销重做、双击边插点、Esc 退出、还原。
   用法：node roi_edit.js [BASE]    默认 http://127.0.0.1:5099 */
const { chromium } = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:5099';
const R = {};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console:' + m.text()); });

  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof roiEditor !== 'undefined' && roiEditor && roiEditor.roi,
    null, { timeout: 15000 });
  await page.waitForTimeout(500);

  const settings = async () => (await (await page.request.get(BASE + '/api/settings')).json()).roi;
  const orig = await settings();
  const handle = (i) => page.evaluate((idx) => {
    const h = roiEditor._handles()[idx];
    const c = roiEditor.toCanvas(h.x, h.y);
    const box = roiEditor.img.getBoundingClientRect();
    return { x: box.left + c.x, y: box.top + c.y };
  }, i);
  const drag = async (from, to) => {
    await page.mouse.move(from.x, from.y); await page.waitForTimeout(60);
    await page.mouse.down(); await page.mouse.move(to.x, to.y, { steps: 10 }); await page.mouse.up();
    await page.waitForTimeout(600);
  };
  /* 找一个「在多边形内、离所有把手 > 14px」的落点：否则会命中顶点，测的就不是整体平移 */
  const bodySpot = () => page.evaluate(() => {
    const e = roiEditor, pts = e.roi.points, box = e.img.getBoundingClientRect();
    const handles = e._handles().map((h) => e.toCanvas(h.x, h.y));
    const cx = pts.reduce((s, q) => s + q[0], 0) / pts.length;
    const cy = pts.reduce((s, q) => s + q[1], 0) / pts.length;
    let best = null;
    for (let dy = -40; dy <= 40; dy += 4) for (let dx = -40; dx <= 40; dx += 4) {
      if (!e._insideRoi(cx + dx, cy + dy)) continue;
      const c = e.toCanvas(cx + dx, cy + dy);
      const near = Math.min(...handles.map((h) => Math.hypot(h.x - c.x, h.y - c.y)));
      if (!best || near > best.near) best = { near, x: box.left + c.x, y: box.top + c.y, scale: e.metrics().scale };
    }
    return best;
  });

  // ---- 多边形 ----
  await page.click('#roiEdit'); await page.waitForTimeout(200);
  const h0 = await handle(0);
  await drag(h0, { x: h0.x + 12, y: h0.y + 8 });
  const afterVertex = await settings();
  R.polyVertex = JSON.stringify(afterVertex.points[0]) !== JSON.stringify(orig.points[0]);

  const spot = await bodySpot();
  await drag({ x: spot.x, y: spot.y }, { x: spot.x + 30, y: spot.y + 18 });
  const afterMove = await settings();
  R.polyBody = Math.abs((afterMove.points[0][0] - afterVertex.points[0][0]) - Math.round(30 / spot.scale)) <= 2;

  // 撤销 / 重做
  await page.keyboard.press('Control+z'); await page.waitForTimeout(600);
  R.undo = JSON.stringify((await settings()).points) === JSON.stringify(afterVertex.points);
  await page.keyboard.press('Control+Shift+z'); await page.waitForTimeout(600);
  R.redo = JSON.stringify((await settings()).points) === JSON.stringify(afterMove.points);
  await page.click('#roiUndo'); await page.waitForTimeout(600);
  R.undoButton = JSON.stringify((await settings()).points) === JSON.stringify(afterVertex.points);

  // 双击边插点
  const edge = await page.evaluate(() => {
    const e = roiEditor, pts = e.roi.points, box = e.img.getBoundingClientRect();
    const a = pts[0], b = pts[1];
    const c = e.toCanvas((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    return { x: box.left + c.x, y: box.top + c.y, n: pts.length };
  });
  await page.mouse.dblclick(edge.x, edge.y); await page.waitForTimeout(600);
  R.insertVertex = (await settings()).points.length === edge.n + 1;
  await page.keyboard.press('Control+z'); await page.waitForTimeout(600);
  R.insertUndo = (await settings()).points.length === edge.n;

  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  R.escExit = await page.evaluate(() => roiEditor.mode === null);

  // ---- 矩形：角 / 边 / 平移 ----
  await page.request.post(BASE + '/api/roi', { data: { kind: 'rect', rect: [200, 200, 300, 200] } });
  await page.reload();
  await page.waitForFunction(() => typeof roiEditor !== 'undefined' && roiEditor && roiEditor.roi,
    null, { timeout: 15000 });
  await page.waitForTimeout(400);
  await page.click('#roiEdit'); await page.waitForTimeout(200);
  const c2 = await handle(2);
  await drag(c2, { x: c2.x + 40, y: c2.y + 30 });
  const r1 = await settings();
  R.rectCorner = r1.rect[2] > 300 && r1.rect[3] > 200;
  const e6 = await handle(6);
  await drag(e6, { x: e6.x, y: e6.y + 25 });
  const r2 = await settings();
  R.rectEdge = r2.rect[3] > r1.rect[3];
  const inner = await page.evaluate(() => {
    const e = roiEditor, [x, y, w, h] = e.roi.rect, box = e.img.getBoundingClientRect();
    const c = e.toCanvas(x + w / 2, y + h / 2);
    return { x: box.left + c.x, y: box.top + c.y, scale: e.metrics().scale };
  });
  await drag({ x: inner.x, y: inner.y }, { x: inner.x - 20, y: inner.y + 10 });
  const r3 = await settings();
  R.rectBody = Math.abs((r3.rect[0] - r2.rect[0]) - Math.round(-20 / inner.scale)) <= 2;

  await page.request.post(BASE + '/api/roi', { data: orig });
  R.restored = JSON.stringify(await settings()) === JSON.stringify(orig);
  R.jsErrors = errs;

  const failed = Object.entries(R).filter(([k, v]) => k !== 'jsErrors' && v !== true);
  console.log(JSON.stringify(R, null, 2));
  console.log(failed.length ? `FAIL ${failed.map(([k]) => k).join(',')}` : 'ALL PASS');
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
