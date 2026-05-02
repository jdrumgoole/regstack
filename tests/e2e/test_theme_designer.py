"""End-to-end Playwright tests for the theme designer SPA."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from regstack.wizard.theme_designer.writer import THEME_FILE  # noqa: E402

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
def page(designer_server: dict) -> Page:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(designer_server["url"])
        # Wait for the JS to populate the controls.
        page.wait_for_selector('.d-row[data-var="--rs-accent"]')
        yield page
        context.close()
        browser.close()


def test_loads_with_defaults(page: Page, designer_server: dict) -> None:
    accent_input = page.locator('.d-row[data-var="--rs-accent"] input[type="text"]')
    expect(accent_input).to_have_value("#2563eb")  # default accent


def test_save_writes_theme_css(page: Page, designer_server: dict) -> None:
    """Type a new accent value, click Save, confirm the file appears
    on disk with the right content."""
    accent_input = page.locator('.d-row[data-var="--rs-accent"] input[type="text"]')
    accent_input.fill("#0d9488")
    page.locator("#d-save").click()
    # Status flips to ok.
    page.wait_for_function('() => document.getElementById("d-status").dataset.tone === "ok"')
    target_dir: Path = designer_server["target_dir"]
    text = (target_dir / THEME_FILE).read_text()
    assert "--rs-accent: #0d9488;" in text


def test_validation_error_shows_inline(page: Page, designer_server: dict) -> None:
    accent_input = page.locator('.d-row[data-var="--rs-accent"] input[type="text"]')
    accent_input.fill("not-a-colour")
    page.locator("#d-save").click()
    page.wait_for_function('() => document.getElementById("d-status").dataset.tone === "error"')
    expect(accent_input).to_have_class("has-error")
    target_dir: Path = designer_server["target_dir"]
    assert not (target_dir / THEME_FILE).exists()


def test_dark_tab_switches_scope(page: Page, designer_server: dict) -> None:
    """Clicking the Dark tab should rebuild controls with dark-scope
    values (which differ from light defaults for --rs-bg)."""
    light_bg = page.locator('.d-row[data-var="--rs-bg"] input[type="text"]')
    expect(light_bg).to_have_value("#ffffff")

    page.locator('.d-tab[data-scope="dark"]').click()
    page.wait_for_function(
        '() => document.querySelector(\'.d-row[data-var="--rs-bg"] input[type="text"]\').value === "#0b1220"'
    )


def test_reset_restores_defaults(page: Page, designer_server: dict) -> None:
    accent_input = page.locator('.d-row[data-var="--rs-accent"] input[type="text"]')
    accent_input.fill("#ff00ff")
    page.locator("#d-reset").click()
    page.wait_for_function(
        '() => document.querySelector(\'.d-row[data-var="--rs-accent"] input[type="text"]\').value === "#2563eb"'
    )
