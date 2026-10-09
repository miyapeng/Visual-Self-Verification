// Offline replay of reviewed original run-code functions. No planner or judge.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');
const {chromium} = require(process.env.VSV_PLAYWRIGHT_PACKAGE);

function locator(page, target) {
  const root = target.scope ? page.locator(target.scope) : page;
  if (target.selector) return root.locator(target.selector);
  return root.getByRole(target.role, {name: target.name, exact: target.exact !== false});
}

async function readRule(page, rule) {
  if (rule.type === 'url_contains') return page.url();
  if (rule.type === 'text_visible') return page.getByText(rule.value, {exact: rule.exact !== false}).first().isVisible();
  const target = locator(page, rule.target);
  if (rule.type === 'visible') return target.isVisible();
  if (rule.type === 'count' || rule.type === 'count_increases') {
    return target.evaluateAll(nodes => nodes.filter(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden').length);
  }
  if (rule.type === 'anchored_top') {
    return {box: await target.boundingBox(), scrollY: await page.evaluate(() => window.scrollY)};
  }
  throw new Error('Unsupported assertion: ' + rule.type);
}

function matchesRule(rule, observed, baseline) {
  if (rule.type === 'url_contains') return observed.includes(rule.value);
  if (rule.type === 'visible' || rule.type === 'text_visible') return observed === true;
  if (rule.type === 'count_increases') return observed > baseline;
  if (rule.type === 'count') return rule.operator === 'ge' ? observed >= rule.value : observed === rule.value;
  if (rule.type === 'anchored_top') {
    return !!(observed.box && baseline.box && observed.scrollY - baseline.scrollY >= rule.min_scroll &&
      Math.abs(observed.box.y - baseline.box.y) <= rule.tolerance && Math.abs(observed.box.y) <= rule.tolerance);
  }
  return false;
}

async function assertRule(page, rule, baseline, timeout) {
  const deadline = Date.now() + timeout;
  let observed;
  do {
    observed = await readRule(page, rule);
    if (matchesRule(rule, observed, baseline)) return {rule, baseline, observed, passed: true};
    await new Promise(resolve => setTimeout(resolve, 50));
  } while (Date.now() < deadline);
  return {rule, baseline, observed, passed: false};
}

const input = readline.createInterface({input: process.stdin});
const replies = input[Symbol.asyncIterator]();
async function ground(packet) {
  process.stdout.write(JSON.stringify({gui_step: packet}) + '\n');
  const reply = await replies.next();
  if (reply.done) throw new Error('GUI grounding channel closed');
  return JSON.parse(reply.value);
}

async function performAction(page, action, s, evidenceId, history, versionId) {
  if (action.type === 'scroll' && !action.target) return page.mouse.wheel(action.axis === 'x' ? action.amount : 0, action.axis === 'x' ? 0 : action.amount);
  if (action.type === 'go_back') return page.goBack({waitUntil: 'domcontentloaded'});
  if (action.type === 'press' && !action.target) return page.keyboard.press(action.key);
  let target = locator(page, action.target);
  let selection;
  let grounded = false;
  try { await target.waitFor({state: 'visible'}); }
  catch (e) {
    if (e.name !== 'TimeoutError' && !String(e).includes('strict mode violation')) throw e;
    grounded = true;
  }
  if ((grounded || await target.count() !== 1) && action.target.name &&
      ['click', 'hover', 'scroll'].includes(action.type)) {
    const root = action.target.scope ? page.locator(action.target.scope) : page;
    const escaped = action.target.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const exactText = root.getByText(new RegExp('^' + escaped + '$', 'i')).filter({visible: true});
    // A unique unchanged label is usable even when a plan predicted the wrong HTML role.
    if (await exactText.count() === 1) { target = exactText; grounded = false; }
  }
  if (grounded || await target.count() !== 1) {
    const observation = {...await s.capture('ground', false), evidence_id: evidenceId};
    const scope = action.target.scope ? page.locator(action.target.scope) : page;
    const elements = await scope.locator('a,button,input,select,textarea,h1,h2,h3,[role="button"],[role="link"]').elementHandles();
    const handles = new Map(), controls = [];
    for (const handle of elements) {
      if (!await handle.isVisible()) continue;
      const ref = 'e' + controls.length;
      handles.set(ref, handle);
      controls.push({element_ref: ref, ...await handle.evaluate(el => ({
        tag: el.tagName.toLowerCase(), text: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || '').slice(0, 200),
        href: el.getAttribute('href'), context: el.closest('header,nav,footer,main,section')?.tagName.toLowerCase() || 'body'
      }))});
    }
    selection = await ground({version_id: versionId, guided_action: action, observation, controls, history});
    if (selection.action === 'blocked') {
      const error = new Error(selection.reason || 'Cannot ground the prescribed action');
      error.run_status = 'blocked'; error.selection = selection; throw error;
    }
    if (selection.action !== action.type || !handles.has(selection.element_ref)) throw new Error('Invalid grounded action');
    target = handles.get(selection.element_ref);
  }
  if (action.type === 'click') await target.click();
  else if (action.type === 'hover') await target.hover();
  else if (action.type === 'fill') await target.fill(action.value);
  else if (action.type === 'select') await target.selectOption({label: action.value});
  else if (action.type === 'press') await target.press(action.key);
  else if (action.type === 'scroll') {
    await target.scrollIntoViewIfNeeded();
    if (action.amount != null) {
      await target.hover();
      await page.mouse.wheel(action.axis === 'x' ? action.amount : 0, action.axis === 'x' ? 0 : action.amount);
    }
  }
  else throw new Error('Unsupported UI action: ' + action.type);
  return selection;
}

async function runWorkflows(spec, session) {
  const rows = [], limits = spec.execution_limits;
  for (const workflow of spec.acceptance_workflows) {
    const s = await session('workflow-' + rows.length, workflow.setup.viewport);
    s.page.setDefaultTimeout(limits.timeout_ms);
    s.page.setDefaultNavigationTimeout(limits.timeout_ms + 10000);
    const row = {workflow_id: workflow.workflow_id, origin: 'independent', setup: workflow.setup, nodes: []};
    let actions = 0, blocked = false;
    try {
      await s.page.goto(spec.base_url + workflow.setup.route, {waitUntil: 'domcontentloaded'});
      row.initial = await s.capture('reset', false);
      for (const [index, node] of workflow.nodes.entries()) {
        const current = {evidence_id: `${spec.version_id}:workflow:${workflow.workflow_id}:node:${index}`,
          source_ref: node.source_ref, check_ids: Object.keys(node.checks), actions: [], checks: [], run_status: 'completed'};
        row.nodes.push(current);
        if (blocked) {
          current.run_status = 'blocked'; current.reason = 'prerequisite_blocked';
        } else {
          try {
            const baselines = new Map();
            for (const rules of Object.values(node.checks)) for (const rule of rules) {
              if (['count_increases', 'anchored_top'].includes(rule.type)) baselines.set(rule, await readRule(s.page, rule));
            }
            for (const [ai, action] of (node.actions || []).entries()) {
              if (++actions > limits.max_ui_actions) {
                const e = new Error('UI action budget exceeded'); e.run_status = 'blocked'; throw e;
              }
              const entry = {action, run_status: 'completed'};
              current.actions.push(entry);
              try {
                const history = row.nodes.flatMap(n => n.actions).slice(0, -1);
                const selection = await performAction(s.page, action, s, current.evidence_id + ':action:' + ai, history, spec.version_id);
                if (selection) entry.selection = selection;
                entry.observed_url = s.page.url();
              } catch (e) { entry.run_status = e.run_status || (e.name === 'TimeoutError' ? 'blocked' : 'evaluator_error');
                entry.error = String(e); if (e.selection) entry.selection = e.selection; throw e; }
            }
            for (const [checkId, rules] of Object.entries(node.checks)) {
              const assertions = [];
              for (const rule of rules) assertions.push(await assertRule(s.page, rule, baselines.get(rule), limits.timeout_ms));
              current.checks.push({check_id: checkId, assertions});
            }
          } catch (e) {
            current.run_status = e.run_status || (e.name === 'TimeoutError' ? 'blocked' : 'evaluator_error');
            current.reason = String(e); blocked = true;
          }
        }
        try { current.observation = await s.capture('node-' + index, node.fullpage === true); }
        catch (e) { current.run_status = 'evaluator_error'; current.reason = String(e); blocked = true; }
      }
    } catch (e) {
      for (const [index, node] of workflow.nodes.entries()) if (index >= row.nodes.length) {
        row.nodes.push({evidence_id: `${spec.version_id}:workflow:${workflow.workflow_id}:node:${index}`,
          check_ids: Object.keys(node.checks), run_status: 'evaluator_error', reason: String(e), checks: [], actions: []});
      }
    } finally { await s.context.close(); }
    rows.push(row);
  }
  return rows;
}

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
    const resourceRequests = [];
    // Bound external asset waits without changing application bytes or silently serving substitutes.
    await page.route('**/*', async route => {
      const request = route.request();
      if (new URL(request.url()).origin === new URL(spec.base_url).origin ||
          !['font', 'stylesheet'].includes(request.resourceType())) return route.continue();
      try {
        const response = await route.fetch({timeout: spec.execution_limits?.timeout_ms || 10000});
        await route.fulfill({response});
      } catch (error) {
        resourceRequests.push({type: request.resourceType(), url: request.url(),
          error: String(error), origin: 'evaluation_network_deadline'});
        await route.abort('timedout');
      }
    });
    page.on('pageerror', e => errors.push({type: 'runtime', text: String(e)}));
    page.on('console', e => { if (e.type() === 'error') errors.push({type: 'console', text: e.text()}); });
    page.on('response', response => {
      const type = response.request().resourceType();
      if (['font', 'stylesheet'].includes(type)) resourceRequests.push({type, url: response.url(), status: response.status()});
    });
    page.on('requestfailed', request => {
      const type = request.resourceType();
      if (['font', 'stylesheet'].includes(type)) resourceRequests.push({type, url: request.url(), error: request.failure()?.errorText});
    });
    const dir = path.join(out, name);
    fs.mkdirSync(dir, {recursive: true});
    let n = 0;
    async function capture(label, fullPage = true) {
      const stem = `${String(n++).padStart(3, '0')}-${label}`;
      const png = path.join(dir, stem + '.png');
      await page.screenshot({path: png, fullPage, timeout: 10000});
      const dom = path.join(dir, stem + '.html');
      const content = await page.content();
      fs.writeFileSync(dom, content);
      const loading = await page.evaluate(() => {
        const images = [...document.images];
        return {font_status: document.fonts.status, image_count: images.length,
          loaded_images: images.filter(i => i.complete && i.naturalWidth > 0).length,
          pending_images: images.filter(i => !i.complete).length,
          failed_images: images.filter(i => i.complete && !i.naturalWidth).map(i => i.currentSrc || i.src)};
      });
      return {screenshot: png, dom, dom_sha256: crypto.createHash('sha256').update(content).digest('hex'),
        url: page.url(), errors: [...errors], loading, resource_requests: [...resourceRequests], timestamp: new Date().toISOString()};
    }
    return {context, page, capture, errors};
  }
  try {
    if (spec.acceptance_workflows) result.workflows = await runWorkflows(spec, session);
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
  } finally { await browser.close(); input.close(); }
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
}
main().catch(e => { input.close(); process.stdin.destroy(); console.error(e); process.exitCode = 1; });
