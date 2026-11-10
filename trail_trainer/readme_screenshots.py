"""Render the site's pages in a headless browser and save the screenshots the README shows.

Run with `uv run python -m trail_trainer.readme_screenshots`. It serves the app from this checkout's
own data, so the screenshots show whatever is in the training log, trails and saved plans now.
Needs Chromium for Playwright, installed once with `uv run playwright install chromium`.
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright
from werkzeug.serving import make_server

from trail_trainer.app import app
from trail_trainer.trails import PROJECT_ROOT

SCREENSHOTS_DIR = PROJECT_ROOT / "docs" / "screenshots"
# Wide enough for the widest page (the Training Log's); each shot is cropped to its own page's width.
VIEWPORT_WIDTH_PX = 1400
VIEWPORT_HEIGHT_PX = 900
# The Fitness tab analyses every new recording on its first load, which can take a minute or more.
PAGE_TIMEOUT_MS = 5 * 60 * 1000


@contextmanager
def serving_app() -> Iterator[str]:
    """Serve the app on a free local port for the duration; yields its base URL."""
    # Keep the request log out of the output, which a commit hook prints.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def screenshot_page_down_to(page: Page, last: Locator, path: Path) -> None:
    """Save the page from its top, tabs included, down to the bottom of `last`, as wide as the page's content."""
    content = page.locator("main").bounding_box()
    bottom = last.bounding_box()
    assert content is not None and bottom is not None, "the page's content and the last element must be shown"
    page.screenshot(
        path=path,
        full_page=True,
        clip={"x": content["x"], "y": 0, "width": content["width"], "height": bottom["y"] + bottom["height"]},
    )


def panel(page: Page, title: str) -> Locator:
    """The figure panel titled `title`, once its plot is drawn."""
    section = page.locator("section.panel").filter(has=page.get_by_role("heading", name=title, exact=True))
    section.locator(".main-svg").first.wait_for()
    return section


def shoot_trail_planning(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/trail-planning")
    # The catalog loads after the week's saved plan, so once it's drawn the page has settled. It's
    # drawn inside the collapsed Included Trails, so it's never visible.
    page.locator("#included table, #included p").first.wait_for(state="attached")
    if page.locator(".plan-table").count() == 0:
        # No plan saved for this week: show a freshly shuffled one instead.
        page.locator("#plan-button").click()
        page.locator("#result > *").first.wait_for()
    screenshot_page_down_to(page, page.locator("#included-details"), SCREENSHOTS_DIR / "trail_planning.png")


def shoot_training_log(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/training-log")
    weeks = page.locator("#grid .grid-row[data-first-day]")
    weeks.first.wait_for()
    shown_weeks = weeks.nth(1) if weeks.count() > 1 else weeks.first
    screenshot_page_down_to(page, shown_weeks, SCREENSHOTS_DIR / "training_log.png")


def shoot_fitness(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/fitness")
    over_time = panel(page, "Over time")
    screenshot_page_down_to(page, over_time, SCREENSHOTS_DIR / "fitness.png")


def latest_effort_in_model_id(page: Page, base_url: str) -> str:
    response = page.request.get(f"{base_url}/api/fitness", timeout=PAGE_TIMEOUT_MS)
    assert response.ok, f"/api/fitness failed: {response.status} {response.text()}"
    in_model = [e for e in response.json()["efforts"] if e["membership"]]
    assert in_model, "there's no fitness model, so no effort to explore against it"
    return max(in_model, key=lambda e: e["start_local"])["id"]


def opened_group_member(page: Page, group_title: str, title: str) -> Locator:
    """The figure titled `title` in the collapsed group of panels titled `group_title`, opening the
    group if it isn't, once the figure's plot is drawn."""
    group = page.locator("details.panel").filter(has=page.get_by_role("heading", name=group_title, exact=True))
    if not group.evaluate("details => details.open"):
        group.locator("summary").click()
    member = group.locator("section.subpanel").filter(has=page.get_by_role("heading", name=title, exact=True))
    member.locator(".main-svg").first.wait_for()
    return member


def shoot_effort_panels(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/fitness/activities/{latest_effort_in_model_id(page, base_url)}")
    panel(page, "Climbing").screenshot(path=SCREENSHOTS_DIR / "effort_climbing.png")
    for title, filename in [
        ("Vertical rate vs heart rate", "effort_rate_vs_heart_rate.png"),
        ("Vertical rate vs elevation", "effort_rate_vs_elevation.png"),
    ]:
        opened_group_member(page, "Model details", title).screenshot(path=SCREENSHOTS_DIR / filename)


def main() -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    with serving_app() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": VIEWPORT_WIDTH_PX, "height": VIEWPORT_HEIGHT_PX},
            color_scheme="light",
        )
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        for shoot in [shoot_trail_planning, shoot_training_log, shoot_fitness, shoot_effort_panels]:
            shoot(page, base_url)
        browser.close()
    print(f"Saved screenshots to {SCREENSHOTS_DIR.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
