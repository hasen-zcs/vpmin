/* 跨源/嵌入回归：另起一个源（127.0.0.1:5097）模拟管理页面，验证
   ① 跨源 GET ② 跨源 POST（含 OPTIONS 预检）③ iframe 嵌入能正常渲染。
   用法：node cors_embed.js [BASE]  （需要 http://127.0.0.1:5097 上有 mock 页面，run.sh 会起） */
const { chromium } = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:5099';
const MOCK = 'http://127.0.0.1:5097/index.html';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(MOCK);
  await page.waitForTimeout(2500);

  const text = await page.textContent('#out');
  const flags = await page.evaluate(() => ({ get: window.__corsGet, post: window.__corsPost }));
  const frame = page.frames().find((f) => f.url().startsWith(BASE));
  const iframe = frame ? await frame.evaluate(() => ({
    title: document.title,
    video: !!document.querySelector('#view'),
    roiBtn: !!document.querySelector('#roiEdit'),
  })) : null;

  const R = {
    corsGet: flags.get === 'ok',
    corsPost: flags.post === 200,
    iframeRendered: !!(iframe && iframe.video && iframe.roiBtn),
    jsErrors: errs,
  };
  const failed = Object.entries(R).filter(([k, v]) => k !== 'jsErrors' && v !== true);
  console.log(JSON.stringify({ ...R, mockText: text, iframe }, null, 2));
  console.log(failed.length ? `FAIL ${failed.map(([k]) => k).join(',')}` : 'ALL PASS');
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
