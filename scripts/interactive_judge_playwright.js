#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

function writeJson(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function safePart(value) {
  return String(value).replace(/[^A-Za-z0-9_.-]+/g, "_").slice(0, 100) || "item";
}

function locatorFor(page, action) {
  if (action.selector) return page.locator(action.selector).first();
  if (action.role) {
    const options = action.name == null ? {} : { name: action.name, exact: true };
    return page.getByRole(action.role, options).first();
  }
  if (action.text) return page.getByText(action.text, { exact: true }).first();
  throw new Error(`${action.type} requires selector, role/name, or text`);
}

function compare(actual, expected, match) {
  const left = String(actual == null ? "" : actual);
  const right = String(expected == null ? "" : expected);
  if (match === "equals") return left === right;
  if (match === "regex") return new RegExp(right).test(left);
  return left.includes(right);
}

function decodedPng(driverPackage, buffer) {
  const { PNG } = require(path.join(driverPackage, "lib", "utilsBundle"));
  return PNG.sync.read(buffer);
}

function changedPixelRatio(driverPackage, before, after, channelThreshold = 12) {
  const left = decodedPng(driverPackage, before);
  const right = decodedPng(driverPackage, after);
  if (left.width !== right.width || left.height !== right.height) return 1;
  let changed = 0;
  const pixels = left.width * left.height;
  for (let offset = 0; offset < left.data.length; offset += 4) {
    const delta = Math.max(
      Math.abs(left.data[offset] - right.data[offset]),
      Math.abs(left.data[offset + 1] - right.data[offset + 1]),
      Math.abs(left.data[offset + 2] - right.data[offset + 2]),
      Math.abs(left.data[offset + 3] - right.data[offset + 3])
    );
    if (delta >= channelThreshold) changed += 1;
  }
  return pixels ? changed / pixels : 0;
}

function drawnPixelRatio(driverPackage, buffer) {
  const image = decodedPng(driverPackage, buffer);
  let drawn = 0;
  const pixels = image.width * image.height;
  for (let offset = 0; offset < image.data.length; offset += 4) {
    if (image.data[offset + 3] > 8) drawn += 1;
  }
  return pixels ? drawn / pixels : 0;
}

function decodedImage(driverPackage, buffer) {
  const bundle = require(path.join(driverPackage, "lib", "utilsBundle"));
  const pngSignature = buffer.length >= 8 && buffer.slice(0, 8).equals(
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  );
  return pngSignature
    ? bundle.PNG.sync.read(buffer)
    : bundle.jpegjs.decode(buffer, { maxMemoryUsageInMB: 512 });
}

function visualFeatures(driverPackage, buffer) {
  const image = decodedImage(driverPackage, buffer);
  const width = 128;
  const height = 72;
  const gray = new Float64Array(width * height);
  const colorHistogram = new Float64Array(48);
  for (let y = 0; y < height; y += 1) {
    const sourceY = Math.min(image.height - 1, Math.floor((y + 0.5) * image.height / height));
    for (let x = 0; x < width; x += 1) {
      const sourceX = Math.min(image.width - 1, Math.floor((x + 0.5) * image.width / width));
      const source = (sourceY * image.width + sourceX) * 4;
      const target = y * width + x;
      const r = image.data[source];
      const g = image.data[source + 1];
      const b = image.data[source + 2];
      gray[target] = (r + g + b) / 3;
      colorHistogram[Math.min(15, Math.floor(r / 16))] += 1;
      colorHistogram[16 + Math.min(15, Math.floor(g / 16))] += 1;
      colorHistogram[32 + Math.min(15, Math.floor(b / 16))] += 1;
    }
  }
  const samples = width * height;
  for (let index = 0; index < colorHistogram.length; index += 1) {
    colorHistogram[index] /= samples;
  }
  const orientation = new Float64Array(8);
  let edgePixels = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const gx = gray[y * width + x + 1] - gray[y * width + x - 1];
      const gy = gray[(y + 1) * width + x] - gray[(y - 1) * width + x];
      const magnitude = Math.hypot(gx, gy);
      if (magnitude > 20) edgePixels += 1;
      let angle = (Math.atan2(gy, gx) + Math.PI) % Math.PI;
      const bin = Math.min(7, Math.floor(angle / Math.PI * 8));
      orientation[bin] += magnitude;
    }
  }
  const orientationTotal = orientation.reduce((sum, value) => sum + value, 0);
  if (orientationTotal > 0) {
    for (let index = 0; index < orientation.length; index += 1) {
      orientation[index] /= orientationTotal;
    }
  }
  return {
    colorHistogram,
    orientation,
    edgeDensity: edgePixels / samples,
  };
}

function cosineSimilarity(left, right) {
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let index = 0; index < left.length; index += 1) {
    dot += left[index] * right[index];
    leftNorm += left[index] * left[index];
    rightNorm += right[index] * right[index];
  }
  if (leftNorm === 0 && rightNorm === 0) return 1;
  if (leftNorm === 0 || rightNorm === 0) return 0;
  return dot / Math.sqrt(leftNorm * rightNorm);
}

function visualSignature(driverPackage, actualBuffer, referenceBuffer) {
  const actual = visualFeatures(driverPackage, actualBuffer);
  const reference = visualFeatures(driverPackage, referenceBuffer);
  let histogramIntersection = 0;
  for (let index = 0; index < actual.colorHistogram.length; index += 1) {
    histogramIntersection += Math.min(
      actual.colorHistogram[index], reference.colorHistogram[index]
    );
  }
  histogramIntersection /= 3;
  const orientationCosine = cosineSimilarity(actual.orientation, reference.orientation);
  const maxDensity = Math.max(actual.edgeDensity, reference.edgeDensity);
  const densitySimilarity = maxDensity === 0
    ? 1
    : Math.min(actual.edgeDensity, reference.edgeDensity) / maxDensity;
  return {
    score: 0.3 * histogramIntersection + 0.5 * orientationCosine + 0.2 * densitySimilarity,
    histogram_intersection: histogramIntersection,
    orientation_cosine: orientationCosine,
    edge_density_similarity: densitySimilarity,
    actual_edge_density: actual.edgeDensity,
    reference_edge_density: reference.edgeDensity,
  };
}

async function main() {
  const [driverPackage, specFile, resultFile] = process.argv.slice(2);
  if (!driverPackage || !specFile || !resultFile) {
    throw new Error(
      "Usage: interactive_judge_playwright.js DRIVER_PACKAGE SPEC_FILE RESULT_FILE"
    );
  }
  const { chromium } = require(driverPackage);
  const spec = JSON.parse(fs.readFileSync(specFile, "utf8"));
  const outputDir = path.resolve(spec.outputDir);
  const stateDir = path.join(outputDir, "states");
  const videoDir = path.join(outputDir, "video");
  fs.mkdirSync(stateDir, { recursive: true });
  if (spec.recordVideo) fs.mkdirSync(videoDir, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const contextOptions = {
    viewport: spec.viewport,
    locale: "en-US",
    timezoneId: "UTC",
    colorScheme: "light",
    reducedMotion: "reduce",
  };
  if (spec.recordVideo) {
    contextOptions.recordVideo = { dir: videoDir, size: spec.viewport };
  }
  const context = await browser.newContext(contextOptions);
  await context.addInitScript(() => {
    const observed = [];
    Object.defineProperty(globalThis, "__mmcodeEventListeners", {
      value: observed,
      configurable: false,
      enumerable: false,
      writable: false,
    });
    const original = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, listener, options) {
      if (type === "keydown" || type === "keyup" || type === "keypress") {
        const target = this === globalThis
          ? "window"
          : this === document
            ? "document"
            : this && this.tagName
              ? String(this.tagName).toLowerCase()
              : this && this.constructor
                ? this.constructor.name
                : "unknown";
        observed.push({ type: String(type), target });
      }
      return original.call(this, type, listener, options);
    };
  });
  const page = await context.newPage();
  await page.route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    const localHttp = ["localhost", "127.0.0.1", "::1"].includes(requestUrl.hostname);
    const allowed = ["file:", "data:", "blob:", "about:"].includes(requestUrl.protocol)
      || (["http:", "https:"].includes(requestUrl.protocol) && localHttp);
    if (allowed) await route.continue();
    else await route.abort("blockedbyclient");
  });
  const video = page.video();
  const consoleEvents = [];
  const loadedResources = [];
  const pendingResources = new Map();
  page.on("request", (request) => {
    const row = {
      sequence: loadedResources.length,
      url: request.url(),
      method: request.method(),
      resource_type: request.resourceType(),
      response_status: null,
      failure: null,
    };
    loadedResources.push(row);
    pendingResources.set(request, row);
  });
  page.on("response", (response) => {
    const row = pendingResources.get(response.request());
    if (row) row.response_status = response.status();
  });
  page.on("console", (message) => {
    consoleEvents.push({
      type: message.type(),
      text: message.text(),
      location: message.location(),
    });
  });
  page.on("pageerror", (error) => {
    consoleEvents.push({
      type: "pageerror",
      text: String(error),
      stack: error && error.stack ? String(error.stack) : "",
      location: {},
    });
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure() ? request.failure().errorText : "unknown";
    const row = pendingResources.get(request);
    if (row) row.failure = failure;
    consoleEvents.push({
      type: "requestfailed",
      text: `${request.method()} ${request.url()} ${failure}`,
      location: {},
    });
  });

  let consoleCursor = 0;
  let previousFingerprint = null;
  const actionResults = [];

  async function contentAfterNavigationSettles() {
    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await page.content();
      } catch (error) {
        lastError = error;
        const message = String(error && error.message ? error.message : error);
        if (!/navigat|execution context was destroyed/i.test(message) || attempt === 2) {
          throw error;
        }
        await page
          .waitForLoadState("domcontentloaded", {
            timeout: Math.min(1000, spec.timeoutMs),
          })
          .catch(() => {});
        await page.waitForTimeout(50);
      }
    }
    throw lastError || new Error("Unable to capture page content after navigation");
  }

  async function snapshot(index, label, consumeConsole = true) {
    const stem = `${String(index).padStart(4, "0")}-${safePart(label)}`;
    const screenshotPath = path.join(stateDir, `${stem}.png`);
    const domPath = path.join(stateDir, `${stem}.html`);
    const accessibilityPath = path.join(stateDir, `${stem}.aria.txt`);
    const consolePath = path.join(stateDir, `${stem}.console.json`);
    const html = await contentAfterNavigationSettles();
    let aria = "";
    try {
      if (typeof page.locator("body").ariaSnapshot === "function") {
        aria = await page.locator("body").ariaSnapshot();
      } else {
        aria = await page.locator("body").innerText();
      }
    } catch (error) {
      aria = `[accessibility snapshot failed: ${error.message || String(error)}]`;
    }
    await page.screenshot({
      path: screenshotPath,
      // Evidence is state-bound to the current viewport. Long-page coverage
      // must be expressed explicitly as scroll + screenshot actions in the
      // frozen plan; an implicit full-page capture can hang on animation or
      // lazy-loading and obscures which state the policy actually observed.
      fullPage: false,
      animations: "disabled",
      timeout: spec.timeoutMs,
    });
    fs.writeFileSync(domPath, html, "utf8");
    fs.writeFileSync(accessibilityPath, aria, "utf8");
    const consoleDelta = consoleEvents.slice(consoleCursor);
    if (consumeConsole) consoleCursor = consoleEvents.length;
    writeJson(consolePath, consoleDelta);
    const fingerprint = sha256(`${page.url()}\n${html}`);
    const browserState = await page.evaluate(() => ({
      viewport: { width: window.innerWidth, height: window.innerHeight },
      scroll: { x: window.scrollX, y: window.scrollY },
      active_element: document.activeElement
        ? {
            tag: document.activeElement.tagName.toLowerCase(),
            id: document.activeElement.id || null,
            name: document.activeElement.getAttribute("name"),
          }
        : null,
      history_length: window.history.length,
      visibility_state: document.visibilityState,
      keyboard_listeners: Array.from(globalThis.__mmcodeEventListeners || []),
    }));
    const state = {
      url: page.url(),
      title: await page.title(),
      fingerprint,
      changed: previousFingerprint != null && fingerprint !== previousFingerprint,
      previous_fingerprint: previousFingerprint,
      browser_state: browserState,
      evidence: {
        screenshot: screenshotPath,
        dom: domPath,
        accessibility: accessibilityPath,
        console_delta: consolePath,
      },
    };
    previousFingerprint = fingerprint;
    return state;
  }

  async function loadProgram() {
    if (spec.entryUrl) {
      await page.goto(String(spec.entryUrl), {
        waitUntil: "domcontentloaded",
        timeout: spec.timeoutMs,
      });
      await page.waitForTimeout(100);
      return;
    }
    let entry = path.resolve(spec.programPath);
    if (spec.isSite) entry = path.join(entry, "index.html");
    if (!fs.existsSync(entry)) throw new Error(`Program entry does not exist: ${entry}`);
    await page.goto(pathToFileURL(entry).href, {
      waitUntil: "domcontentloaded",
      timeout: spec.timeoutMs,
    });
    await page.waitForTimeout(100);
  }

  let initialState;
  let fatalError = null;
  try {
    await loadProgram();
    // Do not consume load-time errors here. The first frozen action must expose
    // them to the generator context rather than losing them in an unselected
    // initialization snapshot.
    initialState = await snapshot(0, "initial", false);
    const actions = spec.plan.actions || [];
    for (let index = 0; index < actions.length; index += 1) {
      const action = actions[index];
      const result = {
        index,
        type: action.type,
        checklist_id: action.checklist_id || null,
        status: "pass",
        expected: action.expected == null ? null : String(action.expected),
        observed: null,
        note: action.note || null,
      };
      const actionTimeout = Number(action.timeout_ms || spec.timeoutMs);
      const extraEvidence = {};
      try {
        switch (action.type) {
          case "navigate": {
            if (!action.url) throw new Error("navigate requires url");
            const target = new URL(action.url, page.url()).href;
            await page.goto(target, {
              waitUntil: "domcontentloaded",
              timeout: spec.timeoutMs,
            });
            result.observed = page.url();
            break;
          }
          case "reset": {
            // Reset is a mechanical isolation boundary between independent
            // checks.  It clears browser-controlled persistent state and then
            // reloads the public task entry point.  It never changes a check's
            // semantics or invents a follow-up action.
            await context.clearCookies();
            await page.evaluate(async () => {
              localStorage.clear();
              sessionStorage.clear();
              if (globalThis.caches && typeof globalThis.caches.keys === "function") {
                const keys = await globalThis.caches.keys();
                await Promise.all(keys.map((key) => globalThis.caches.delete(key)));
              }
            }).catch(() => {});
            await loadProgram();
            result.observed = "browser state cleared and task entry reloaded";
            break;
          }
          case "click":
            await locatorFor(page, action).click({ timeout: actionTimeout });
            result.observed = "clicked";
            break;
          case "fill":
            await locatorFor(page, action).fill(String(action.value || ""), {
              timeout: actionTimeout,
            });
            result.observed = String(action.value || "");
            break;
          case "select":
            result.observed = JSON.stringify(
              await locatorFor(page, action).selectOption(String(action.value || ""), {
                timeout: actionTimeout,
              })
            );
            break;
          case "hover":
            await locatorFor(page, action).hover({ timeout: actionTimeout });
            result.observed = "hovered";
            break;
          case "press":
            if (!action.key) throw new Error("press requires key");
            if (action.selector || action.role || action.text) {
              await locatorFor(page, action).press(action.key, { timeout: actionTimeout });
            } else {
              await page.keyboard.press(action.key);
            }
            result.observed = action.key;
            break;
          case "scroll":
            await page.evaluate(
              ({ x, y }) => window.scrollBy(Number(x || 0), Number(y || 0)),
              { x: action.x, y: action.y }
            );
            result.observed = JSON.stringify({ x: action.x || 0, y: action.y || 0 });
            break;
          case "wait":
            await page.waitForTimeout(Math.max(0, Number(action.milliseconds || 0)));
            result.observed = `${Number(action.milliseconds || 0)}ms`;
            break;
          case "screenshot":
            result.observed = "captured";
            break;
          case "assert_text": {
            const actual = action.selector || action.role || action.text
              ? await locatorFor(page, action).innerText({ timeout: actionTimeout })
              : await page.locator("body").innerText({ timeout: actionTimeout });
            const expected = action.expected == null ? action.value : action.expected;
            result.expected = String(expected == null ? "" : expected);
            result.observed = actual;
            if (!compare(actual, expected, action.match || "contains")) result.status = "fail";
            break;
          }
          case "assert_visible": {
            const actual = await locatorFor(page, action).isVisible({ timeout: actionTimeout });
            const expected = String(action.expected == null ? "true" : action.expected) !== "false";
            result.expected = String(expected);
            result.observed = String(actual);
            if (actual !== expected) result.status = "fail";
            break;
          }
          case "assert_url": {
            const actual = page.url();
            const expected = action.expected == null ? action.url : action.expected;
            result.expected = String(expected == null ? "" : expected);
            result.observed = actual;
            if (!compare(actual, expected, action.match || "contains")) result.status = "fail";
            break;
          }
          case "assert_no_console_errors": {
            const errors = consoleEvents.filter((event) =>
              ["error", "pageerror", "requestfailed"].includes(String(event.type))
            );
            result.expected = "no browser console/page/request errors";
            result.observed = errors.length
              ? JSON.stringify(errors.slice(0, 20))
              : "no browser console/page/request errors";
            if (errors.length) result.status = "fail";
            break;
          }
          case "assert_style": {
            if (!action.value) throw new Error("assert_style requires value as a CSS property");
            const locator = locatorFor(page, action);
            const property = String(action.value);
            const actual = await locator.evaluate(
              (element, cssProperty) => getComputedStyle(element).getPropertyValue(cssProperty),
              property
            );
            result.expected = String(action.expected == null ? "" : action.expected);
            result.observed = String(actual).trim();
            if (!compare(result.observed, result.expected, action.match || "equals")) {
              result.status = "fail";
            }
            break;
          }
          case "assert_canvas_drawn": {
            const locator = locatorFor(page, action);
            const dataUrl = await locator.evaluate((canvas) => {
              if (!(canvas instanceof HTMLCanvasElement)) {
                throw new Error("assert_canvas_drawn target is not a canvas");
              }
              return canvas.toDataURL("image/png");
            });
            const buffer = Buffer.from(String(dataUrl).split(",", 2)[1] || "", "base64");
            const ratio = drawnPixelRatio(driverPackage, buffer);
            const minimum = Number(action.minimum_ratio || 0.01);
            const evidencePath = path.join(
              stateDir,
              `${String(index + 1).padStart(4, "0")}-canvas-buffer.png`
            );
            fs.writeFileSync(evidencePath, buffer);
            extraEvidence.canvas_buffer = evidencePath;
            result.expected = `drawn pixel ratio >= ${minimum}`;
            result.observed = `drawn_pixel_ratio=${ratio.toFixed(6)}`;
            if (ratio < minimum) result.status = "fail";
            break;
          }
          case "assert_visual_change": {
            const locator = locatorFor(page, action);
            const before = await locator.screenshot({
              animations: "allow",
              timeout: actionTimeout,
            });
            const waitMs = Math.max(1, Number(action.milliseconds || 250));
            await page.waitForTimeout(waitMs);
            const after = await locator.screenshot({
              animations: "allow",
              timeout: actionTimeout,
            });
            const ratio = changedPixelRatio(driverPackage, before, after);
            const minimum = Number(action.minimum_ratio || 0.001);
            const beforePath = path.join(
              stateDir,
              `${String(index + 1).padStart(4, "0")}-visual-before.png`
            );
            const afterPath = path.join(
              stateDir,
              `${String(index + 1).padStart(4, "0")}-visual-after.png`
            );
            fs.writeFileSync(beforePath, before);
            fs.writeFileSync(afterPath, after);
            extraEvidence.visual_before = beforePath;
            extraEvidence.visual_after = afterPath;
            result.expected = `changed pixel ratio >= ${minimum} after ${waitMs}ms`;
            result.observed = `changed_pixel_ratio=${ratio.toFixed(6)}`;
            if (ratio < minimum) result.status = "fail";
            break;
          }
          case "assert_visual_signature": {
            if (!action.reference_image) {
              throw new Error("assert_visual_signature requires reference_image");
            }
            const locator = locatorFor(page, action);
            const actual = await locator.screenshot({
              animations: "allow",
              timeout: actionTimeout,
            });
            const programRoot = spec.isSite
              ? path.resolve(spec.programPath)
              : path.dirname(path.resolve(spec.programPath));
            const referencePath = path.resolve(programRoot, action.reference_image);
            if (!fs.existsSync(referencePath)) {
              throw new Error(`Reference image does not exist: ${referencePath}`);
            }
            const reference = fs.readFileSync(referencePath);
            const signature = visualSignature(driverPackage, actual, reference);
            const minimum = Number(action.minimum_ratio || 0.8);
            const actualPath = path.join(
              stateDir,
              `${String(index + 1).padStart(4, "0")}-visual-signature.png`
            );
            fs.writeFileSync(actualPath, actual);
            extraEvidence.visual_actual = actualPath;
            extraEvidence.visual_reference = referencePath;
            result.expected = `reference structural signature >= ${minimum}`;
            result.observed = JSON.stringify(signature);
            if (signature.score < minimum) result.status = "fail";
            break;
          }
          default:
            throw new Error(`Unsupported action type: ${action.type}`);
        }
        await page.waitForTimeout(50);
      } catch (error) {
        result.status = "fail";
        result.error = `${error.name || "Error"}: ${error.message || String(error)}`;
        result.observed = result.observed || result.error;
      }
      const state = await snapshot(index + 1, action.type);
      Object.assign(result, state);
      Object.assign(result.evidence, extraEvidence);
      actionResults.push(result);
    }
  } catch (error) {
    fatalError = `${error.name || "Error"}: ${error.message || String(error)}`;
  }

  const consolePath = path.join(outputDir, "console.json");
  writeJson(consolePath, consoleEvents);
  let videoPath = null;
  await page.close().catch(() => {});
  await context.close().catch(() => {});
  if (video) {
    try {
      videoPath = await video.path();
    } catch (_error) {
      videoPath = null;
    }
  }
  await browser.close();

  const checklistResults = [];
  for (const item of spec.plan.checklist || []) {
    const linked = actionResults.filter((result) => result.checklist_id === item.id);
    const assertions = linked.filter((result) => result.type.startsWith("assert_"));
    const firstFailure = linked.find((result) => result.status === "fail");
    let status = "pass";
    if (firstFailure) status = "fail";
    else if (assertions.length === 0) status = "blocked";
    const observed = firstFailure
      ? firstFailure.observed
      : assertions.length
        ? assertions.map((result) => result.observed).join(" | ")
        : "No deterministic assertion was executed for this checklist item.";
    checklistResults.push({
      checklist_id: item.id,
      description: item.description,
      category: item.category,
      weight: Number(item.weight || 1),
      status,
      expected: item.expected,
      observed,
      first_failing_action: firstFailure ? firstFailure.index : null,
      evidence_references: firstFailure
        ? Object.values(firstFailure.evidence || {})
        : assertions.flatMap((result) => Object.values(result.evidence || {})),
      confidence: status === "blocked" ? 0 : 1,
      residual: status === "pass"
        ? ""
        : status === "blocked"
          ? `No executable assertion certified: ${item.expected}`
          : `Expected ${item.expected}; observed ${observed}`,
    });
  }
  const totalWeight = checklistResults.reduce((sum, item) => sum + item.weight, 0);
  const passedWeight = checklistResults
    .filter((item) => item.status === "pass")
    .reduce((sum, item) => sum + item.weight, 0);
  const result = {
    format_version: 1,
    status: fatalError ? "error" : "ok",
    error: fatalError,
    case_id: spec.caseId,
    execution_id: spec.executionId,
    code_version: spec.codeVersion,
    purpose: spec.purpose,
    plan_version: spec.plan.plan_version,
    planner: spec.plan.planner,
    initial_state: initialState || null,
    actions: actionResults,
    checklist: checklistResults,
    score: totalWeight ? passedWeight / totalWeight : 0,
    console_path: consolePath,
    loaded_resources: loadedResources,
    video_path: videoPath,
    result_path: path.resolve(resultFile),
  };
  writeJson(resultFile, result);
  if (fatalError) process.exitCode = 2;
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
