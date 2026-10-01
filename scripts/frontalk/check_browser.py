#!/usr/bin/env python3
"""Launch FronTalk's released Selenium driver against an offline page."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = PROJECT_ROOT / "evaluate" / "frontalk" / "upstream"
sys.path.insert(0, str(UPSTREAM))

from webvoyager.run import get_default_driver  # noqa: E402


def main() -> None:
    driver = get_default_driver()
    try:
        driver.get(
            "data:text/html,<html><title>frontalk-browser-ok</title>"
            "<body>offline browser smoke test</body></html>"
        )
        chrome = driver.capabilities.get("chrome", {})
        print("title:", driver.title)
        print("browser:", driver.capabilities.get("browserName"))
        print("browser_version:", driver.capabilities.get("browserVersion"))
        print("chromedriver_version:", chrome.get("chromedriverVersion"))
        if driver.title != "frontalk-browser-ok":
            raise RuntimeError("FronTalk browser smoke test returned the wrong title")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
