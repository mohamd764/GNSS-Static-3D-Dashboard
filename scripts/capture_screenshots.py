"""Regenerate the README screenshots from a running app (``python app.py``).

Requires Google Chrome and ``pip install selenium``. Loads the synthetic demo
session, runs both checks, exports the PDF, and demonstrates the wrong-zone warning.

    python scripts/capture_screenshots.py [--url http://127.0.0.1:8050] [--out docs/screenshots]
"""

from __future__ import annotations

import argparse
import base64
import glob
import os
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8050/")
    parser.add_argument("--out", default="docs/screenshots")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    downloads = tempfile.mkdtemp()

    options = webdriver.ChromeOptions()
    for arg in ("--headless=new", "--no-sandbox", "--window-size=1280,900", "--hide-scrollbars"):
        options.add_argument(arg)
    options.add_experimental_option("prefs", {"download.default_directory": downloads})
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    def by_id(element_id):
        return driver.find_element(By.ID, element_id)

    def click(element_id):
        driver.execute_script("arguments[0].click()", by_id(element_id))

    def capture(element, name, max_height=None):
        rect = driver.execute_script(
            "const b = arguments[0].getBoundingClientRect();"
            "return {x: b.left + window.scrollX, y: b.top + window.scrollY, width: b.width, height: b.height};",
            element,
        )
        pad = 8
        clip = {"x": max(rect["x"] - pad, 0), "y": max(rect["y"] - pad, 0), "width": rect["width"] + 2 * pad,
                "height": min(rect["height"], max_height or rect["height"]) + 2 * pad, "scale": 1}
        shot = driver.execute_cdp_cmd("Page.captureScreenshot",
                                      {"format": "png", "clip": clip, "captureBeyondViewport": True})
        with open(os.path.join(args.out, f"{name}.png"), "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))

    def card_of(element_id):
        return by_id(element_id).find_element(By.XPATH, "./ancestor::div[contains(concat(' ', @class, ' '), ' card ')][1]")

    driver.get(args.url)
    wait.until(lambda d: by_id("load-sample-button").is_displayed())
    click("load-sample-button")
    wait.until(lambda d: "EPSG:32615" in by_id("crs-feedback").text)
    time.sleep(0.5)

    click("apply-scale-factor")
    click("submit-button")
    wait.until(lambda d: "Linear misclose" in by_id("statistical-analysis").text)
    click("vertical-submit-button")
    wait.until(lambda d: "dH (calc - GNSS)" in by_id("vertical-results").text)
    time.sleep(0.5)

    container = driver.find_element(By.CSS_SELECTOR, ".container-lg")
    capture(container, "overview", max_height=1250)
    capture(card_of("crs-mode"), "coordinate-reference-system")
    capture(card_of("geometry-output"), "grid-geometry")
    capture(card_of("results-table"), "horizontal-results")
    capture(card_of("vertical-results"), "vertical-results")

    click("export-pdf-button")
    for _ in range(40):
        if glob.glob(os.path.join(downloads, "*.pdf")) and not glob.glob(os.path.join(downloads, "*.crdownload")):
            break
        time.sleep(0.25)
    pdfs = glob.glob(os.path.join(downloads, "*.pdf"))
    print("PDF export:", [f"{os.path.basename(p)} ({os.path.getsize(p)} bytes)" for p in pdfs] or "FAILED")

    driver.execute_script("arguments[0].click()", driver.find_element(By.CSS_SELECTOR, "#crs-mode input[value='epsg']"))
    by_id("epsg-code").send_keys("32614")
    wait.until(lambda d: "km from the approximate site position" in by_id("crs-feedback").text)
    capture(card_of("crs-mode"), "wrong-zone-warning")
    print("Wrong-zone warning:", by_id("crs-feedback").text.splitlines()[-1])

    severe = [entry for entry in driver.get_log("browser") if entry["level"] == "SEVERE"]
    print("Browser console errors:", severe or "none")
    driver.quit()


if __name__ == "__main__":
    main()
