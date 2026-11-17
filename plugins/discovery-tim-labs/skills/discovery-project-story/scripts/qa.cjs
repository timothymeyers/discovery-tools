#!/usr/bin/env node
/* QA for a built discovery-work.html.
 *
 *   node qa.cjs path/to/discovery-work.html [--out DIR] [--quick]
 *
 * Offline (every non-file request is blocked and counted as an error), across
 * light/dark × 1600×1000 / 1280×720 / 390×844 × every beat. Checks text
 * overflow, card/node collisions, routes crossing unrelated cards, clipped
 * header/panel text, page-level horizontal scroll, internal ids on the main
 * surface, quote/no-quote panel state, reverse seek, Play beat / Play scene
 * timing (gap, stop at scene end), reduced motion, drawers showing exact
 * text, keyboard and deep links. Screenshots + contact sheet go to DIR
 * (default: <html dir>/qa). Exit 1 on any error.
 *
 * Needs playwright-core and a Chromium. Override with PLAYWRIGHT_CORE and
 * CHROMIUM_PATH if the defaults below are not present.
 */
const fs = require("fs"), path = require("path");
const PW = process.env.PLAYWRIGHT_CORE || [
  "/Applications/Discovery App - Preview.app/Contents/Resources/Code/Discovery App - Preview.app/Contents/Resources/app/node_modules/playwright-core",
  "playwright-core"].find(p => { try { require.resolve(p); return true; } catch { return false; } });
if (!PW) { console.error("playwright-core not found; set PLAYWRIGHT_CORE"); process.exit(2); }
const { chromium } = require(PW);
const CHROME = process.env.CHROMIUM_PATH || (() => {
  const base = path.join(process.env.HOME || "", "Library/Caches/ms-playwright");
  try {
    const d = fs.readdirSync(base).filter(x => x.startsWith("chromium-")).sort().reverse()[0];
    const p = path.join(base, d, "chrome-mac/Chromium.app/Contents/MacOS/Chromium");
    if (fs.existsSync(p)) return p;
  } catch { /* fall through */ }
  return undefined;
})();

const args = process.argv.slice(2);
const file = path.resolve(args.find(a => !a.startsWith("--")) || "");
if (!fs.existsSync(file)) { console.error("usage: node qa.cjs discovery-work.html [--out DIR] [--quick]"); process.exit(2); }
const outDir = path.resolve(args.includes("--out") ? args[args.indexOf("--out") + 1] : path.join(path.dirname(file), "qa"));
const quick = args.includes("--quick");
fs.mkdirSync(outDir, { recursive: true });
const url = "file://" + file;
const THEMES = ["light", "dark"], VIEWS = quick ? [[1600, 1000]] : [[1600, 1000], [1280, 720], [390, 844]];
const errors = [], warnings = [], shots = [];
const err = (w, m) => errors.push(`${w}: ${m}`), warn = (w, m) => warnings.push(`${w}: ${m}`);

/* Runs in the page: geometry + surface checks for the current beat. */
const PAGE_CHECK = () => {
  const out = { errors: [], warnings: [] };
  const D = STORY.data, st = STORY.state(), sc = D.scenes[st.scene], b = sc.beats[st.beat];
  const inside = (r, R, pad = 0) => r.x >= R.x - pad && r.y >= R.y - pad && r.x + r.width <= R.x + R.width + pad && r.y + r.height <= R.y + R.height + pad;
  const overlap = (a, c, pad = 0) => a.x < c.x + c.width - pad && c.x < a.x + a.width - pad && a.y < c.y + c.height - pad && c.y < a.y + a.height - pad;
  document.querySelectorAll("#stage [data-overflow]").forEach(t => out.errors.push(`SVG text truncated: “${t.textContent.slice(0, 60)}”`));
  const boxes = [...document.querySelectorAll("#stage .card, #stage .node")].map(g => ({ id: g.dataset.id, g, R: g.querySelector(".box").getBBox() }));
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) if (overlap(boxes[i].R, boxes[j].R, 1)) out.errors.push(`boxes overlap: ${boxes[i].id} / ${boxes[j].id}`);
    const lbl = boxes[i].g.querySelector(".lbl");
    if (lbl && lbl.textContent && !inside(lbl.getBBox(), boxes[i].R, 2)) out.errors.push(`label escapes its box: ${boxes[i].id}`);
  }
  const vb = { x: 0, y: 0, width: 1440, height: 780 };
  document.querySelectorAll("#stage text").forEach(t => { if (t.textContent && t.getAttribute("visibility") !== "hidden" && !inside(t.getBBox(), vb, 1)) out.errors.push(`text outside stage: “${t.textContent.slice(0, 40)}”`); });
  // routes must not pass through boxes other than their endpoints
  const ends = new Map();
  if (sc.mode === "tree") b.tree.routes.forEach((r, i) => ends.set(document.querySelector(`#stage [data-route="${i}"]`), [r.from, r.to]));
  if (sc.mode === "flow") sc.graph.edges.forEach(e => ends.set(document.querySelector(`#stage [data-edge="${e.id}"]`), [e.from, e.to]));
  for (const [p, [a, c]] of ends) {
    if (!p) continue;
    const L = p.getTotalLength();
    for (const bx of boxes) {
      if (bx.id === a || bx.id === c) continue;
      const R = { x: bx.R.x + 6, y: bx.R.y + 6, width: bx.R.width - 12, height: bx.R.height - 12 };
      for (let k = 1; k < 40; k++) {
        const pt = p.getPointAtLength(L * k / 40);
        if (pt.x > R.x && pt.x < R.x + R.width && pt.y > R.y && pt.y < R.y + R.height) {
          (sc.mode === "tree" ? out.errors : out.warnings).push(`route ${a}→${c} crosses ${bx.id}`); break;
        }
      }
    }
  }
  // metrics: value labels must not collide with bars or each other
  const mv = [...document.querySelectorAll("#stage .m-val")].map(t => t.getBBox());
  const mb = [...document.querySelectorAll("#stage .m-bar, #stage .m-cached, #stage .m-out")].map(r => r.getBBox()).filter(r => r.width > 0);
  mv.forEach((a, i) => { if (mb.some(r => overlap(a, r, 1)) || mv.some((c, j) => j !== i && overlap(a, c, 1))) out.errors.push("metric value label collides with a bar or another label"); });
  // clipped HTML text
  for (const id of ["scene-title", "story-title", "eyebrow", "beat-title", "progress", "human-heading", "human-summary", "human-quote", "action", "artifact-label", "artifact-preview"]) {
    const e = document.getElementById(id);
    if (!e || e.closest("[hidden]") || !e.textContent) continue;
    const cs = getComputedStyle(e);
    const clipped = (/(hidden|clip)/.test(cs.overflow + cs.overflowX + cs.overflowY)) && (e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1);
    if (clipped && cs.textOverflow !== "ellipsis" && !(+cs.webkitLineClamp > 0)) out.errors.push(`clipped text in #${id}`);
    const r = e.getBoundingClientRect();
    if (r.right > innerWidth + 1 || r.left < -1) out.errors.push(`#${id} outside the viewport horizontally`);
  }
  if (document.documentElement.scrollWidth > innerWidth + 1) out.errors.push(`page scrolls horizontally (${document.documentElement.scrollWidth} > ${innerWidth})`);
  // main surface identifiers (dialogs excluded)
  const BANNED = /\bDX-\d+\b|\bH\d{3}\b|\b[0-9a-f]{12,40}\b|\/Users\/|\.discovery\/|\bresearch\/|\b[\w-]+\.(md|json|jsonl|tex|py|csv|html|txt)\b/i;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    if (n.parentElement.closest("dialog, script, style, [hidden]")) continue;
    const m = BANNED.exec(n.textContent); if (m) out.errors.push(`internal id on main surface: ${m[0]}`);
  }
  // quote panel state
  const q = b.human && b.human.quoteId, wrap = document.getElementById("quote-wrap");
  if (!q && !wrap.hidden) out.errors.push("quote shown on a beat without a primary quote");
  if (q) {
    if (wrap.hidden) out.errors.push("primary quote hidden");
    const shown = document.getElementById("human-quote").textContent.replace(/^… /, "").replace(/ …$/, "");
    if (!D.quotes[q].text.includes(shown)) out.errors.push("displayed quote is not an exact substring of the quote");
  }
  const art = document.getElementById("artifact-open");
  if (!art.hidden && !(b.artifact && (b.artifact.preview || b.artifact.detail))) out.errors.push("artifact button promises a record that has no content");
  return out;
};

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext();
  let external = 0;
  await ctx.route("**/*", r => { if (r.request().url().startsWith("file:")) return r.continue(); external++; err("network", r.request().url()); return r.abort(); });
  const page = await ctx.newPage();
  page.on("pageerror", e => err("pageerror", e.message));
  page.on("console", m => { if (m.type() === "error") err("console", m.text()); });

  for (const theme of THEMES) for (const [w, h] of VIEWS) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(`${url}?theme=${theme}`);
    await page.bringToFront();
    const D = await page.evaluate(() => ({ scenes: STORY.data.scenes.map(s => s.beats.length) }));
    for (let s = 0; s < D.scenes.length; s++) for (let b = 0; b < D.scenes[s]; b++) {
      await page.evaluate(([s, b]) => STORY.jump(s, b), [s, b]);
      const where = `${theme} ${w}x${h} S${s + 1}B${b + 1}`;
      const r = await page.evaluate(PAGE_CHECK);
      r.errors.forEach(m => err(where, m)); r.warnings.forEach(m => warn(where, m));
      if ((w === 1600 && theme === "light") || b === 0) {
        const f = `${theme}-${w}x${h}-S${s + 1}-B${b + 1}.png`;
        await page.screenshot({ path: path.join(outDir, f), fullPage: w < 700 });
        shots.push(f);
      }
    }
  }

  // ── behaviour (1600×1000 light) ──
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`${url}?theme=light#s=2&b=2`);
  await page.bringToFront();
  const deep = await page.evaluate(() => STORY.state());
  if (deep.scene !== 1 || deep.beat !== 1) err("deep link", `#s=2&b=2 opened scene ${deep.scene + 1} beat ${deep.beat + 1}`);
  const info = await page.evaluate(() => STORY.data.scenes.map(s => ({ mode: s.mode, beats: s.beats.map(b => ({ ms: b.animationMs, phases: b.phaseWindows.length, q: b.human && b.human.quoteId, art: !!b.artifact })) })));
  const gapMs = await page.evaluate(() => STORY.data.gapMs);
  const multi = info.findIndex(s => s.beats.length >= 2 && s.mode !== "metrics");
  const sample = async (ms, every = 40) => {
    const t0 = Date.now(), log = [];
    while (Date.now() - t0 < ms) {
      log.push(await page.evaluate(() => ({ ...STORY.state(), dots: [...document.querySelectorAll("#layer-dots .dot")].filter(d => d.getAttribute("visibility") === "visible").length, now: performance.now() })));
      if (log[log.length - 1].phase === "complete") break;
      await page.waitForTimeout(every);
    }
    return log;
  };
  if (multi >= 0) {
    // reverse seek
    await page.evaluate(s => STORY.jump(s, 0), multi);
    const seeks = await page.evaluate(() => { const d = STORY.data.scenes[STORY.state().scene].beats[0].animationMs, o = [];
      for (const t of [d, d / 2, 0]) { STORY.seek(t); o.push({ t: STORY.state().t, active: document.querySelectorAll("#stage .route.active").length }); } return o; });
    if (!(seeks[0].t > seeks[1].t && seeks[1].t > seeks[2].t)) err("seek", "reverse seek did not move time backwards");
    if (seeks[2].active < 1) err("seek", "seek(0) left no active route");
    // play beat
    await page.evaluate(s => STORY.jump(s, 0, 0), multi);
    await page.evaluate(() => STORY.playBeat());
    let log = await sample(info[multi].beats[0].ms + 2500);
    const end = log[log.length - 1];
    if (end.phase !== "complete" || end.beat !== 0) err("play beat", `ended in phase ${end.phase} at beat ${end.beat + 1}`);
    if (info[multi].beats[0].phases && !log.some(x => x.dots > 0)) err("play beat", "no moving dots were visible during playback");
    const took = end.now - log[0].now;
    if (Math.abs(took - info[multi].beats[0].ms) > 700) warn("play beat", `took ${Math.round(took)} ms, expected ~${info[multi].beats[0].ms}`);
    // play scene
    await page.evaluate(s => STORY.jump(s, 0, 0), multi);
    await page.evaluate(() => STORY.playScene());
    const total = info[multi].beats.reduce((n, b) => n + b.ms, 0) + gapMs * (info[multi].beats.length - 1);
    log = await sample(total + 4000);
    const last = log[log.length - 1];
    if (last.phase !== "complete" || last.scene !== multi || last.beat !== info[multi].beats.length - 1) err("play scene", `stopped at scene ${last.scene + 1} beat ${last.beat + 1} (${last.phase}); must stop at end of the scene`);
    const gaps = []; let g0 = null;
    for (const x of log) { if (x.phase === "gap" && g0 === null) g0 = x.now; if (x.phase !== "gap" && g0 !== null) { gaps.push(x.now - g0); g0 = null; } }
    if (gaps.length !== info[multi].beats.length - 1) err("play scene", `saw ${gaps.length} gaps, expected ${info[multi].beats.length - 1}`);
    gaps.forEach(g => { if (Math.abs(g - gapMs) > 400) warn("play scene", `gap ${Math.round(g)} ms, expected ~${gapMs}`); });
    if (log.some(x => x.phase === "gap" && x.dots > 0)) err("play scene", "dots visible during the pause between beats");
    // reduced motion
    await page.evaluate(s => { document.getElementById("reduced").checked = true; document.getElementById("reduced").dispatchEvent(new Event("change")); STORY.jump(s, 0, 0); STORY.playBeat(); }, multi);
    log = await sample(1500);
    if (log.some(x => x.dots > 0)) err("reduced motion", "dots visible with reduced motion on");
    await page.evaluate(() => { document.getElementById("reduced").checked = false; document.getElementById("reduced").dispatchEvent(new Event("change")); });
    // keyboard
    await page.evaluate(s => STORY.jump(s, 0), multi);
    await page.locator("body").click({ position: { x: 5, y: 5 } }).catch(() => {});
    await page.evaluate(s => STORY.jump(s, 0), multi);
    await page.keyboard.press("ArrowRight");
    const k = await page.evaluate(() => STORY.state());
    if (k.beat !== 1) err("keyboard", "ArrowRight did not advance a beat");
  } else warn("behaviour", "no tree/flow scene with ≥ 2 beats to test playback");

  // drawers
  const qs = []; info.forEach((s, si) => s.beats.forEach((b, bi) => { if (b.q) qs.push([si, bi]); }));
  if (qs.length) {
    await page.evaluate(([s, b]) => STORY.jump(s, b), qs[0]);
    await page.click("#exchange-open");
    const ok = await page.evaluate(() => { const b = STORY.data.scenes[STORY.state().scene].beats[STORY.state().beat];
      return document.querySelector("#d-exchange[open] .exact").textContent === STORY.data.quotes[b.human.quoteId].text; });
    if (!ok) err("exchange drawer", "exact text differs from the embedded quote");
    await page.keyboard.press("Escape");
  }
  const ai = []; info.forEach((s, si) => s.beats.forEach((b, bi) => { if (b.art) ai.push([si, bi]); }));
  if (ai.length) {
    await page.evaluate(([s, b]) => STORY.jump(s, b), ai[0]);
    await page.click("#artifact-open");
    const txt = await page.evaluate(() => document.querySelector("#d-artifact[open]").innerText.length);
    if (txt < 20) err("artifact drawer", "record drawer is nearly empty");
    await page.screenshot({ path: path.join(outDir, "drawer-artifact.png") }); shots.push("drawer-artifact.png");
    await page.keyboard.press("Escape");
  }
  for (const id of ["notes-open", "chron-open", "about-open"]) {
    await page.click("#" + id);
    const open = await page.evaluate(() => { const d = document.querySelector("dialog[open]"); return d ? d.innerText.length : 0; });
    if (!open) err("drawer", `#${id} did not open a dialog`);
    await page.screenshot({ path: path.join(outDir, `drawer-${id}.png`) }); shots.push(`drawer-${id}.png`);
    await page.keyboard.press("Escape");
  }
  await browser.close();

  const sheet = ["<!doctype html><meta charset=utf-8><title>QA contact sheet</title><style>body{font:13px system-ui;margin:16px}div{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}figure{margin:0}img{width:100%;border:1px solid #ccc}</style>",
    `<h1>QA — ${errors.length} errors, ${warnings.length} warnings</h1><div>`,
    ...shots.map(f => `<figure><img src="${f}" loading=lazy><figcaption>${f}</figcaption></figure>`), "</div>"].join("\n");
  fs.writeFileSync(path.join(outDir, "contact-sheet.html"), sheet);
  const uniq = a => [...new Set(a)];
  const report = { file, errors: uniq(errors), warnings: uniq(warnings), externalRequests: external, screenshots: shots.length };
  fs.writeFileSync(path.join(outDir, "qa-report.json"), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ errors: report.errors.length, warnings: report.warnings.length, screenshots: shots.length, out: outDir }, null, 1));
  report.errors.slice(0, 40).forEach(e => console.log("ERROR", e));
  report.warnings.slice(0, 15).forEach(e => console.log("warn ", e));
  process.exit(report.errors.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
