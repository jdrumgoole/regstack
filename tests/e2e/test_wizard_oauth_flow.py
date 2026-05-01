"""End-to-end Playwright tests for the OAuth setup SPA.

Drives the wizard from outside the browser process. Covers the SPA
state machine, validation surfacing, sessionStorage persistence,
and the write-success screen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from regstack.wizard.oauth_google.writer import (  # noqa: E402
    CONFIG_FILE,
    SECRETS_ENV_KEY,
    SECRETS_FILE,
)

pytestmark = pytest.mark.skipif(
    not Path(
        os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(Path.home() / "Library" / "Caches" / "ms-playwright"),
        )
    ).exists(),
    reason="Playwright browser cache missing — run `uv run playwright install chromium`.",
)


@pytest.fixture
def page(wizard_server: dict) -> Page:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(wizard_server["url"])
        # Wait for the JS to populate the progress bar.
        page.wait_for_selector(".wiz-progress-cell")
        yield page
        context.close()
        browser.close()


def _fill_text(page: Page, name: str, value: str) -> None:
    page.locator(f'input[name="{name}"]').fill(value)


def _check(page: Page, name: str, on: bool = True) -> None:
    el = page.locator(f'input[name="{name}"]')
    if on and not el.is_checked():
        el.check()
    elif not on and el.is_checked():
        el.uncheck()


def _next(page: Page) -> None:
    page.locator("#wiz-next").click()


def _wait_step(page: Page, n: int) -> None:
    page.wait_for_function(
        f"() => document.querySelector('.wiz-step.is-active').dataset.step === \"{n}\""
    )


def _walk_to_review(page: Page) -> None:
    _wait_step(page, 0)
    _next(page)  # → 1 detect existing (no existing → just next)
    _wait_step(page, 1)
    _next(page)
    _wait_step(page, 2)  # base url
    _fill_text(page, "base_url", "http://localhost:8000")
    _next(page)
    _wait_step(page, 3)  # GCP wait points
    _next(page)
    _wait_step(page, 4)
    _next(page)
    _wait_step(page, 5)
    _next(page)
    _wait_step(page, 6)
    _next(page)
    _wait_step(page, 7)  # credentials
    _fill_text(page, "client_id", "12345-abc.apps.googleusercontent.com")
    _fill_text(page, "client_secret", "GOCSPX-secretvalue1234")
    _next(page)
    _wait_step(page, 8)
    _next(page)
    _wait_step(page, 9)
    _next(page)
    _wait_step(page, 10)


def test_happy_path_writes_files(page: Page, wizard_server: dict) -> None:
    _walk_to_review(page)
    # Step 10 = review. Advance to write.
    _next(page)
    _wait_step(page, 11)
    page.locator("#wiz-write").click()
    page.wait_for_selector('[data-step="11"] [data-when="post-write"]:not([hidden])')
    expect(page.locator(".wiz-success")).to_be_visible()

    target_dir: Path = wizard_server["target_dir"]
    cfg = (target_dir / CONFIG_FILE).read_text()
    assert "enable_oauth = true" in cfg
    assert "12345-abc.apps.googleusercontent.com" in cfg
    secrets = (target_dir / SECRETS_FILE).read_text()
    assert f"{SECRETS_ENV_KEY}=GOCSPX-secretvalue1234" in secrets


def test_step_7_inline_validation_keeps_user_on_step(page: Page) -> None:
    _wait_step(page, 0)
    for _ in range(7):
        _next(page)
    _wait_step(page, 7)
    _fill_text(page, "client_id", "not-a-google-id")
    _fill_text(page, "client_secret", "GOCSPX-secretvalue1234")
    _next(page)
    # Inline error rendered, hash hasn't moved on.
    page.wait_for_selector('.wiz-step[data-step="7"].is-active .wiz-field-error')
    assert page.evaluate("location.hash") in {"", "#/step/7"}


def test_back_button_returns_to_previous_step(page: Page) -> None:
    _wait_step(page, 0)
    _next(page)
    _wait_step(page, 1)
    page.locator("#wiz-back").click()
    _wait_step(page, 0)


def test_review_edit_link_jumps_back(page: Page) -> None:
    _walk_to_review(page)
    page.locator('[data-step="10"] [data-goto="7"]').first.click()
    _wait_step(page, 7)
    # Previously entered values still there.
    expect(page.locator('input[name="client_id"]')).to_have_value(
        "12345-abc.apps.googleusercontent.com"
    )
