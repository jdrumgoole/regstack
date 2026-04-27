from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape

from regstack.email.base import EmailMessage

if TYPE_CHECKING:
    from regstack.config.schema import EmailConfig
    from regstack.models.user import BaseUser

_DEFAULT_PACKAGE = "regstack.email"
_DEFAULT_TEMPLATE_DIR = "templates"


class MailComposer:
    """Builds rendered ``EmailMessage`` instances from Jinja2 templates.

    Hosts override the default templates by registering their own template
    directory via ``RegStack.add_template_dir``; the underlying
    ``ChoiceLoader`` resolves the host directory first.

    Each email kind has three template files:
        ``<name>.subject.txt``  — single line, whitespace-stripped
        ``<name>.html``         — rich body
        ``<name>.txt``          — plain-text fallback
    """

    def __init__(
        self,
        *,
        email_config: EmailConfig,
        app_name: str,
        host_template_dirs: list[Path] | None = None,
    ) -> None:
        self._email_config = email_config
        self._app_name = app_name
        self._host_dirs: list[Path] = list(host_template_dirs or [])
        self._env = self._build_env()

    def add_template_dir(self, path: Path) -> None:
        self._host_dirs.insert(0, path)
        self._env = self._build_env()

    def _build_env(self) -> Environment:
        loaders = [FileSystemLoader(str(p)) for p in self._host_dirs]
        loaders.append(PackageLoader(_DEFAULT_PACKAGE, _DEFAULT_TEMPLATE_DIR))
        return Environment(
            loader=ChoiceLoader(loaders),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def _render(self, template_name: str, context: dict[str, object]) -> str:
        return self._env.get_template(template_name).render(context)

    def _compose(
        self,
        *,
        kind: str,
        to: str,
        context: dict[str, object],
    ) -> EmailMessage:
        subject = self._render(f"{kind}.subject.txt", context).strip()
        html = self._render(f"{kind}.html", context)
        text = self._render(f"{kind}.txt", context)
        return EmailMessage(
            to=to,
            subject=subject,
            html=html,
            text=text,
            from_address=self._email_config.from_address,
            from_name=self._email_config.from_name,
        )

    # --- Public renderers -------------------------------------------------

    def verification(self, *, to: str, full_name: str | None, url: str) -> EmailMessage:
        return self._compose(
            kind="verification",
            to=to,
            context={
                "app_name": self._app_name,
                "full_name": full_name or "",
                "url": url,
            },
        )

    def password_reset(
        self, *, to: str, full_name: str | None, url: str, ttl_minutes: int
    ) -> EmailMessage:
        return self._compose(
            kind="password_reset",
            to=to,
            context={
                "app_name": self._app_name,
                "full_name": full_name or "",
                "url": url,
                "ttl_minutes": ttl_minutes,
            },
        )

    def email_change(
        self, *, to: str, full_name: str | None, url: str, ttl_minutes: int
    ) -> EmailMessage:
        return self._compose(
            kind="email_change",
            to=to,
            context={
                "app_name": self._app_name,
                "full_name": full_name or "",
                "url": url,
                "ttl_minutes": ttl_minutes,
            },
        )

    # SMS bodies live here too — same Jinja loader stack so hosts can
    # override the wording by dropping ``sms_<kind>.txt`` into their
    # template directory.
    def sms_body(self, *, kind: str, **context: object) -> str:
        full = {"app_name": self._app_name, **context}
        return self._render(f"sms_{kind}.txt", full).strip()


def default_template_dir() -> Path:
    """Filesystem path of the bundled defaults — useful for tooling that wants
    to copy and customise rather than override per-template.
    """
    return Path(str(resources.files(_DEFAULT_PACKAGE).joinpath(_DEFAULT_TEMPLATE_DIR)))


__all__ = ["MailComposer", "default_template_dir"]


def for_user(user: BaseUser) -> dict[str, object]:
    """Tiny convenience used by routers building template contexts."""
    return {"email": user.email, "full_name": user.full_name}
