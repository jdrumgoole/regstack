from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import click

from regstack.config.secrets import generate_secret

CONFIG_FILE = "regstack.toml"
SECRETS_FILE = "regstack.secrets.env"


@click.command(help="Interactive wizard that writes regstack.toml + regstack.secrets.env.")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Directory to write config files into.",
)
@click.option("--force", is_flag=True, help="Overwrite existing config files without prompting.")
def init(target_dir: Path, *, force: bool) -> None:
    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    config_path = target_dir / CONFIG_FILE
    secrets_path = target_dir / SECRETS_FILE

    if (config_path.exists() or secrets_path.exists()) and not force:
        click.confirm(
            f"Config already exists at {config_path} or {secrets_path}. Overwrite?",
            abort=True,
        )

    click.echo(click.style("regstack init — app configuration only.\n", bold=True))
    click.echo("This wizard never provisions infrastructure. It only writes config files.\n")

    # --- App identity ---
    app_name = click.prompt("App name", default="MyApp")
    base_url = click.prompt("Public base URL", default="http://localhost:8000")
    parsed = urlsplit(base_url)
    cookie_default = parsed.hostname or ""
    cookie_domain = click.prompt(
        "Cookie domain (blank for none)", default=cookie_default, show_default=True
    )
    behind_proxy = click.confirm("Behind a reverse proxy (X-Forwarded-* headers)?", default=False)

    # --- MongoDB ---
    mongodb_url = click.prompt("MongoDB connection URL", default="mongodb://localhost:27017")
    mongodb_database = click.prompt("MongoDB database", default=app_name.lower().replace(" ", "_"))

    # --- JWT ---
    if click.confirm("Auto-generate a 64-byte JWT secret?", default=True):
        jwt_secret = generate_secret(64)
    else:
        jwt_secret = click.prompt("JWT secret", hide_input=True, confirmation_prompt=True)
    jwt_ttl_seconds = int(click.prompt("JWT lifetime in seconds", default=7200, type=int))
    transport = click.prompt(
        "Token transport", type=click.Choice(["bearer", "cookie"]), default="bearer"
    )

    # --- Email ---
    email_backend = click.prompt(
        "Email backend",
        type=click.Choice(["console", "smtp", "ses"]),
        default="console",
    )
    if email_backend != "console":
        click.echo(
            click.style(
                f"Note: {email_backend!r} backend lands in M2; the wizard will write your "
                "config, but the running app will refuse to send mail until then.",
                fg="yellow",
            )
        )
    sender_default = cookie_default if cookie_default and "." in cookie_default else "example.com"
    from_address = click.prompt("Sender email address", default=f"noreply@{sender_default}")
    from_name = click.prompt("Sender display name", default=app_name)
    smtp_host = smtp_port = smtp_user = smtp_pass = ses_region = None
    smtp_starttls = True
    if email_backend == "smtp":
        smtp_host = click.prompt("SMTP host")
        smtp_port = int(click.prompt("SMTP port", default=587, type=int))
        smtp_starttls = click.confirm("Use STARTTLS?", default=True)
        smtp_user = click.prompt("SMTP username", default="")
        smtp_pass = click.prompt("SMTP password", default="", hide_input=True) or None
    elif email_backend == "ses":
        ses_region = click.prompt("AWS region", default="eu-west-1")

    # --- SMS (skip unless 2FA wanted) ---
    enable_sms_2fa = click.confirm("Enable SMS-based 2FA?", default=False)
    sms_backend = "null"
    if enable_sms_2fa:
        sms_backend = click.prompt(
            "SMS backend", type=click.Choice(["sns", "twilio"]), default="sns"
        )

    # --- Features ---
    enable_admin_router = click.confirm("Enable JSON admin router?", default=False)
    enable_ui_router = click.confirm("Enable server-rendered UI pages?", default=False)
    allow_registration = click.confirm("Allow self-service registration?", default=True)
    require_verification = click.confirm("Require email verification before login?", default=False)

    config_text = _render_toml(
        app_name=app_name,
        base_url=base_url,
        cookie_domain=cookie_domain or None,
        behind_proxy=behind_proxy,
        mongodb_database=mongodb_database,
        jwt_ttl_seconds=jwt_ttl_seconds,
        transport=transport,
        require_verification=require_verification,
        allow_registration=allow_registration,
        enable_admin_router=enable_admin_router,
        enable_ui_router=enable_ui_router,
        enable_sms_2fa=enable_sms_2fa,
        email_backend=email_backend,
        from_address=from_address,
        from_name=from_name,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_starttls=smtp_starttls,
        smtp_username=smtp_user,
        ses_region=ses_region,
        sms_backend=sms_backend,
    )
    secrets_text = _render_secrets(
        jwt_secret=jwt_secret,
        mongodb_url=mongodb_url,
        smtp_password=smtp_pass,
    )

    config_path.write_text(config_text, encoding="utf-8")
    secrets_path.write_text(secrets_text, encoding="utf-8")
    secrets_path.chmod(0o600)

    click.echo()
    click.echo(click.style("Wrote ", fg="green") + str(config_path))
    click.echo(click.style("Wrote ", fg="green") + str(secrets_path) + "  (chmod 600)")
    click.echo()
    click.echo(
        "Next: load the config in your app with `RegStackConfig.load()` and "
        "include `regstack.router` on a FastAPI app."
    )


def _render_toml(
    *,
    app_name: str,
    base_url: str,
    cookie_domain: str | None,
    behind_proxy: bool,
    mongodb_database: str,
    jwt_ttl_seconds: int,
    transport: str,
    require_verification: bool,
    allow_registration: bool,
    enable_admin_router: bool,
    enable_ui_router: bool,
    enable_sms_2fa: bool,
    email_backend: str,
    from_address: str,
    from_name: str,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_starttls: bool,
    smtp_username: str | None,
    ses_region: str | None,
    sms_backend: str,
) -> str:
    lines = [
        "# regstack.toml — generated by `regstack init`. Re-run to regenerate.",
        f'app_name = "{app_name}"',
        f'base_url = "{base_url}"',
    ]
    if cookie_domain:
        lines.append(f'cookie_domain = "{cookie_domain}"')
    lines.extend(
        [
            f"behind_proxy = {str(behind_proxy).lower()}",
            "",
            f'mongodb_database = "{mongodb_database}"',
            "",
            f"jwt_ttl_seconds = {jwt_ttl_seconds}",
            f'transport = "{transport}"',
            "",
            f"require_verification = {str(require_verification).lower()}",
            f"allow_registration = {str(allow_registration).lower()}",
            f"enable_admin_router = {str(enable_admin_router).lower()}",
            f"enable_ui_router = {str(enable_ui_router).lower()}",
            f"enable_sms_2fa = {str(enable_sms_2fa).lower()}",
            "",
            "[email]",
            f'backend = "{email_backend}"',
            f'from_address = "{from_address}"',
            f'from_name = "{from_name}"',
        ]
    )
    if email_backend == "smtp":
        lines.extend(
            [
                f'smtp_host = "{smtp_host}"',
                f"smtp_port = {smtp_port}",
                f"smtp_starttls = {str(smtp_starttls).lower()}",
            ]
        )
        if smtp_username:
            lines.append(f'smtp_username = "{smtp_username}"')
    elif email_backend == "ses":
        lines.append(f'ses_region = "{ses_region}"')
    lines.extend(["", "[sms]", f'backend = "{sms_backend}"', ""])
    return "\n".join(lines)


def _render_secrets(
    *,
    jwt_secret: str,
    mongodb_url: str,
    smtp_password: str | None,
) -> str:
    lines = [
        "# regstack.secrets.env — keep out of version control.",
        f"REGSTACK_JWT_SECRET={jwt_secret}",
        f"REGSTACK_MONGODB_URL={mongodb_url}",
    ]
    if smtp_password:
        lines.append(f"REGSTACK_EMAIL__SMTP_PASSWORD={smtp_password}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    init(standalone_mode=True)
    sys.exit(0)
