// Offline replay of reviewed original run-code functions. No planner or judge.
const fs = require('fs');
const path = require('path');
const {chromium} = require(process.env.VSV_PLAYWRIGHT_PACKAGE);

async function main() {
  const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = path.resolve(process.argv[3]);
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.VSV_CHROMIUM});
  const result = {browser_version: browser.version(), viewport: {width: 1440, height: 900}, routes: {}, checks: []};
  async function session(name, viewport) {
    const context = await browser.newContext({viewport: viewport || result.viewport});
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', e => errors.push({type: 'runtime', text: String(e)}));
    page.on('console', e => { if (e.type() === 'error') errors.push({type: 'console', text: e.text()}); });
    const dir = path.join(out, name);
    fs.mkdirSync(dir, {recursive: true});
    let n = 0;
    async function capture(label) {
      const stem = `${String(n++).padStart(3, '0')}-${label}`;
      const png = path.join(dir, stem + '.png');
      await page.screenshot({path: png, fullPage: true});
      const dom = path.join(dir, stem + '.html');
      fs.writeFileSync(dom, await page.content());
      return {screenshot: png, dom, url: page.url(), errors: [...errors], timestamp: new Date().toISOString()};
    }
    return {context, page, capture, errors};
  }
  try {
    for (const route of spec.routes) {
      const s = await session('page-' + (route.slice(1) || 'home'));
      try {
        await s.page.goto(spec.base_url + route, {waitUntil: 'networkidle'});
        await s.page.waitForTimeout(700);
        const image = await s.capture('full');
        result.routes[route] = {status: 'executed', ...image};
      } catch (e) { result.routes[route] = {status: 'execution_failed', error: String(e)}; }
      finally { await s.context.close(); }
    }
    for (const check of spec.checks) {
      const s = await session(check.name, check.viewport);
      const row = {name: check.name, source_ordinal: check.ordinal, reset_route: check.route,
                   source_function: check.body, steps: [], status: 'executed'};
      try {
        await s.page.goto(spec.base_url + check.route, {waitUntil: 'networkidle'});
        row.initial = await s.capture('reset');
        const observed = new Set(['click','fill','hover','press','goto','waitForTimeout']);
        const proxy = new Proxy(s.page, {get(target, prop) {
          const value = Reflect.get(target, prop, target);
          if (typeof value !== 'function') return value;
          if (!observed.has(prop)) return value.bind(target);
          return async (...args) => {
            const start = new Date().toISOString();
            try {
              const v = await value.apply(target, args);
              row.steps.push({action: prop, args, start, status: 'executed', observation: await s.capture(prop)});
              return v;
            } catch (e) {
              row.steps.push({action: prop, args, start, status: 'execution_failed', error: String(e)});
              throw e;
            }
          };
        }});
        // check.body comes only from explicitly reviewed source ordinals in the pilot spec.
        const fn = new Function('return (' + check.body + ')')();
        row.output = await fn(proxy);
        row.final = await s.capture('result');
      } catch (e) {
        row.status = 'execution_failed'; row.error = String(e);
        try { row.final = await s.capture('error'); } catch (_) {}
      } finally { await s.context.close(); }
      result.checks.push(row);
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
}
main().catch(e => { console.error(e); process.exitCode = 1; });
