#!/usr/bin/env python3
"""Launch Playwright Chromium with the same flags used by InteractWeb-Bench."""

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.goto(
                "data:text/html,<html><title>interactweb-browser-ok</title>"
                "<body>offline browser smoke test</body></html>"
            )
            print("title:", page.title())
            print("chromium_version:", browser.version)
            if page.title() != "interactweb-browser-ok":
                raise RuntimeError("InteractWeb browser smoke test returned the wrong title")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
