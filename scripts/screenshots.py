"""Regenerate the README's screenshots (docs/screenshots/) from a real run of the local stack.

It builds the README's demo state: a Purchase metric, a dark-mode flag, and the "Bigger
checkout button" experiment created in the dashboard. It then feeds the experiment the
checkout_button scenario, clones it, and feeds the clone the srm_bug scenario, photographing
each step with Playwright. Everything goes through the real dashboard, API, and worker code.

Run it with `make screenshots` against an empty stack (scripts/README.md): the experiment
keys must be unused, because a stopped or finished experiment can't be reset.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx2
from playwright.sync_api import FloatRect, Locator, Page, sync_playwright

DASHBOARD = "http://localhost:3000"
API = "http://localhost:8000"
DEMO = "http://localhost:8080"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

EXPERIMENT = "checkout-button"
CLONE = "checkout-button-srm"
HYPOTHESIS = (
    "If we make the Buy button bigger, purchases will increase because the button is easier "
    "to find."
)  # the checkout_button scenario's own hypothesis (scenarios/checkout_button.yaml)
LOOK_EVERY_SECONDS = 2.0  # results looks while users arrive, so the lift chart has a history


def main() -> None:
    password, server_key = os.environ.get("ADMIN_PASSWORD"), os.environ.get("ABTEST_SERVER_KEY")
    if not password or not server_key:
        raise SystemExit("set ADMIN_PASSWORD and ABTEST_SERVER_KEY (they are in .env)")
    admin = httpx2.Client(base_url=API, headers={"Authorization": f"Bearer {server_key}"})
    if admin.get(f"/admin/experiments/{EXPERIMENT}").status_code != 404:
        raise SystemExit(
            f"experiment {EXPERIMENT!r} already exists: run this against an empty stack "
            "(see scripts/README.md)"
        )
    # The metric and flag aren't photographed, so they're created through the API.
    _check(admin.post("/admin/metrics", json={
        "key": "purchase", "name": "Purchase", "kind": "conversion",
        "event_name": "purchase", "direction": "increase",
    }))  # fmt: skip
    _check(admin.post("/admin/flags", json={
        "key": "dark-mode", "description": "Dark theme", "enabled": True, "rollout_bp": 5000,
    }))  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            color_scheme="light",
            timezone_id="UTC",  # the dashboard shows UTC; the demo page uses the browser's zone
        )
        page = context.new_page()
        sign_in(page, password)
        create_experiment(page)
        page.get_by_role("button", name="Start experiment").click()
        page.get_by_role("button", name="Stop").wait_for()

        traffic("checkout_button", EXPERIMENT, looks=admin)
        page.reload()
        page.get_by_text("lift over time").wait_for()
        main_column = page.locator("main")
        shoot(page, "results-verdict", main_column.locator("header"), main_column.locator("table"))
        chart = page.get_by_role("heading", name="Big button: lift over time").locator("..")
        notes = page.locator("ul", has_text="saw more than one variant")
        shoot(page, "lift-over-time", chart, notes)

        page.get_by_role("button", name="Clone").click()
        page.get_by_label("Key for the copy").fill(CLONE)
        page.get_by_role("button", name="Create copy").click()
        page.wait_for_url(f"**/experiments/{CLONE}")
        page.get_by_role("button", name="Start experiment").click()
        page.get_by_role("button", name="Stop").wait_for()
        traffic("srm_bug", CLONE)
        page.reload()
        banner = page.get_by_role("alert").filter(has_text="sample ratio mismatch")
        banner.wait_for()
        shoot(page, "srm-banner", main_column.locator("header"), banner)

        # Last, so the demo page's visitor doesn't change the results photographed above.
        page.goto(DEMO)
        page.get_by_role("button", name="Buy now").click()
        page.get_by_role("button", name="Send queued events now").click()
        page.get_by_text("POST /v1/events -> 202").first.wait_for()
        shoot(page, "sdk-demo", page.locator("main"))
        browser.close()


def sign_in(page: Page, password: str) -> None:
    page.goto(f"{DASHBOARD}/login")
    page.get_by_label("Admin password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{DASHBOARD}/experiments")


def create_experiment(page: Page) -> None:
    """Fill in the new-experiment form as the README describes, photographing it twice."""
    page.goto(f"{DASHBOARD}/experiments/new")
    page.get_by_label("Name", exact=True).fill("Bigger checkout button")
    page.get_by_label("Key (used by the SDK)").fill(EXPERIMENT)
    page.get_by_label("Hypothesis").fill(HYPOTHESIS)
    page.get_by_label("Variant 2 name").fill("Big button")
    page.get_by_label("Metric 1 expected baseline").fill("10")
    page.get_by_label("Smallest lift worth detecting (%)").fill("8")
    page.get_by_role("radio", name="Any time (sequential)").check()
    page.get_by_text("users per variant for an 80% chance").wait_for()
    page.evaluate("document.activeElement.blur()")  # no focus ring in the photo

    form = page.locator("main form")
    shoot(page, "new-experiment-form", page.locator("main h1"), form.locator("section").nth(2))
    shoot(page, "sample-size", form.locator("section").nth(3))
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_url(f"**/experiments/{EXPERIMENT}")


def traffic(scenario: str, key: str, looks: httpx2.Client | None = None) -> None:
    """Send a scenario's simulated users to a running experiment with `make traffic`.

    With `looks`, also ask for a results look every few seconds while they arrive, as the
    "Update results now" button does. Extra looks are safe under the sequential test.
    """
    done = threading.Event()

    def look_repeatedly(admin: httpx2.Client) -> None:
        while not done.wait(LOOK_EVERY_SECONDS):
            _check(admin.post(f"/admin/experiments/{key}/recompute"))

    looker = threading.Thread(target=look_repeatedly, args=(looks,)) if looks else None
    if looker:
        looker.start()
    try:
        command = [
            "make",
            "traffic",
            f"SCENARIO={scenario}",
            f"ARGS=--use-running --experiment-key {key}",
        ]
        subprocess.run(command, check=True)
    finally:
        done.set()
        if looker:
            looker.join()


def shoot(page: Page, name: str, first: Locator, last: Locator | None = None) -> None:
    """Save the part of the page from `first` down to `last`, full width of the wider one."""
    boxes = [box for box in (first.bounding_box(), (last or first).bounding_box()) if box]
    if len(boxes) != 2:
        raise SystemExit(f"{name}: an element to photograph isn't visible")
    scroll_y: float = page.evaluate("window.scrollY")
    pad = 24
    left = min(box["x"] for box in boxes) - pad
    top = min(box["y"] for box in boxes) + scroll_y - pad
    right = max(box["x"] + box["width"] for box in boxes) + pad
    bottom = max(box["y"] + box["height"] for box in boxes) + scroll_y + pad
    clip: FloatRect = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    page.screenshot(path=OUT / f"{name}.png", clip=clip, full_page=True)
    print(f"wrote docs/screenshots/{name}.png", file=sys.stderr)


def _check(response: httpx2.Response) -> httpx2.Response:
    if response.is_error:
        raise SystemExit(f"{response.request.method} {response.request.url.path}: "
                         f"{response.status_code} {response.text}")  # fmt: skip
    return response


if __name__ == "__main__":
    main()
