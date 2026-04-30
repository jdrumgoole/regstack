from __future__ import annotations

from pathlib import Path

from regstack.ui.pages import PAGE_NAMES, build_ui_environment, default_static_dir


def test_default_environment_finds_all_pages() -> None:
    env = build_ui_environment([])
    assert env.get_template("base.html") is not None
    aliases = {
        "confirm-email-change": "email_change_confirm",
        "mfa-confirm": "mfa_confirm",
        "oauth-complete": "oauth_complete",
    }
    for page in PAGE_NAMES:
        suffix = aliases.get(page, page)
        assert env.get_template(f"auth/{suffix}.html") is not None


def test_host_template_overrides_take_precedence(tmp_path: Path) -> None:
    host_dir = tmp_path / "host_templates"
    (host_dir / "auth").mkdir(parents=True)
    (host_dir / "auth" / "login.html").write_text("HOST LOGIN PAGE")

    env = build_ui_environment([host_dir])
    rendered = env.get_template("auth/login.html").render()
    assert rendered == "HOST LOGIN PAGE"

    # Non-overridden pages still come from regstack defaults.
    rendered = env.get_template("auth/register.html").render(
        app_name="X",
        api_prefix="/api/auth",
        ui_prefix="/account",
        static_prefix="/regstack-static",
        theme_css_url=None,
        brand_logo_url=None,
        brand_tagline=None,
        allow_registration=True,
        enable_password_reset=True,
        enable_account_deletion=True,
        page="register",
    )
    assert "Create your account" in rendered


def test_default_static_dir_contains_core_and_theme() -> None:
    base = default_static_dir()
    assert (base / "css" / "core.css").is_file()
    assert (base / "css" / "theme.css").is_file()
    assert (base / "js" / "regstack.js").is_file()
