#!/usr/bin/env node
"use strict";

const fs = require("fs");
const { pathToFileURL } = require("url");

async function main() {
  const [driverPackage, taskFile, workerValue] = process.argv.slice(2);
  if (!driverPackage || !taskFile) {
    throw new Error("Usage: render_playwright.js DRIVER_PACKAGE TASK_FILE [WORKERS]");
  }
  const { chromium } = require(driverPackage);
  const tasks = JSON.parse(fs.readFileSync(taskFile, "utf8"));
  const workers = Math.max(1, Number.parseInt(workerValue || "1", 10));
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  let nextIndex = 0;

  async function renderOne(task) {
    const result = { ...task.result };
    let page;
    try {
      page = await browser.newPage({
        viewport: { width: task.width, height: task.height },
      });
      if (task.sitePath) {
        await page.goto(pathToFileURL(task.htmlPath).href, {
          waitUntil: "domcontentloaded",
          timeout: task.timeoutMs,
        });
      } else {
        const html = fs.readFileSync(task.htmlPath, "utf8");
        await page.setContent(html, {
          waitUntil: "domcontentloaded",
          timeout: task.timeoutMs,
        });
      }
      await page.waitForTimeout(task.waitMs);
      await page.screenshot({
        path: task.outputPath,
        fullPage: task.fullPage,
        animations: "disabled",
        timeout: task.timeoutMs,
      });
      result.status = "ok";
    } catch (error) {
      result.status = "error";
      result.error = `${error.name || "Error"}: ${error.message || String(error)}`;
    } finally {
      if (page) {
        await page.close().catch(() => {});
      }
    }
    process.stdout.write(`${JSON.stringify(result)}\n`);
  }

  async function worker() {
    while (true) {
      const index = nextIndex++;
      if (index >= tasks.length) return;
      await renderOne(tasks[index]);
    }
  }

  try {
    await Promise.all(
      Array.from({ length: Math.min(workers, tasks.length || 1) }, () => worker())
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
