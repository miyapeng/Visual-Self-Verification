#!/usr/bin/env node
"use strict";

const fs = require("fs");
const { pathToFileURL } = require("url");

async function main() {
  const [driverPackage, specPath] = process.argv.slice(2);
  if (!driverPackage || !specPath) {
    throw new Error("Usage: render_one_playwright.js DRIVER_PACKAGE SPEC_JSON");
  }
  const { chromium } = require(driverPackage);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const errors = [];
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: spec.width, height: spec.height },
      deviceScaleFactor: 1,
    });
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console[error]: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message || error}`));
    await page.goto(pathToFileURL(spec.source).href, {
      waitUntil: "networkidle",
      timeout: 30000,
    });
    await page.screenshot({ path: spec.target, fullPage: true, animations: "disabled" });
    await page.close();
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({ status: "ok", errors }));
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
