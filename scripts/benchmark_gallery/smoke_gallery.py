#!/usr/bin/env python3
"""Small browser smoke test for the generated benchmark gallery."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT = Path(__file__).resolve().parents[2]


def local_chromium() -> str | None:
    candidates = [
        PROJECT / ".local/runtime/research/playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell",
        PROJECT / ".local/runtime/research/playwright/chromium-1223/chrome-linux64/chrome",
    ]
    return str(next((path for path in candidates if path.exists()), "")) or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8892/")
    parser.add_argument("--screenshot", default="/tmp/benchmark-gallery.png")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=local_chromium(),
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        assert "Benchmark Atlas" in page.title()
        assert page.locator(".benchmark-tab").count() == 4
        assert page.locator(".category-card").count() == 3

        page.get_by_role("tab", name="FronTalk").click()
        assert page.locator(".category-card").count() == 4
        page.locator("[data-sample='1']").click()
        page.locator("[data-turn='3']").click()
        assert "Turn 4" in page.locator(".input-toolbar strong").first.text_content()

        page.get_by_role("tab", name="WebCompass").click()
        assert page.locator(".category-card").count() == 5
        page.locator("[data-tab='boundary']").click()
        assert page.locator(".context-card").count() == 3

        page.get_by_role("tab", name="SWE-MM").click()
        assert page.locator(".sample-button").count() == 5
        page.locator("[data-tab='schema']").click()
        assert "agent_visible row" in page.locator(".schema-code").text_content()

        page.goto(args.url, wait_until="networkidle")
        page.screenshot(path=args.screenshot, full_page=True)
        browser.close()

    if errors:
        raise RuntimeError(f"Browser page errors: {errors}")
    print(f"smoke ok; screenshot={Path(args.screenshot)}")


if __name__ == "__main__":
    main()
