from __future__ import annotations

from pathlib import Path

from regstack.config.loader import load_config


def _write(p: Path, body: str) -> None:
    p.write_text(body)


def test_toml_then_env_then_kwargs(tmp_path: Path, monkeypatch) -> None:
    toml = tmp_path / "regstack.toml"
    _write(
        toml,
        """\
app_name = "FromToml"
mongodb_database = "from_toml"
jwt_ttl_seconds = 600
[email]
backend = "console"
from_address = "toml@example.com"
""",
    )
    secrets_env = tmp_path / "secrets.env"
    _write(secrets_env, "REGSTACK_JWT_SECRET=secret-from-env-file\n")

    monkeypatch.setenv("REGSTACK_APP_NAME", "FromEnv")
    monkeypatch.setenv("REGSTACK_EMAIL__FROM_ADDRESS", "env@example.com")

    cfg = load_config(
        toml_path=toml,
        secrets_env_path=secrets_env,
        mongodb_database="from_kwarg",
    )

    assert cfg.app_name == "FromEnv"  # env beats toml
    assert cfg.mongodb_database == "from_kwarg"  # kwargs beat env
    assert cfg.jwt_ttl_seconds == 600  # toml inherited
    assert cfg.email.from_address == "env@example.com"  # nested env override
    assert cfg.jwt_secret.get_secret_value() == "secret-from-env-file"
