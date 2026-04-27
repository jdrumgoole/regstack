from __future__ import annotations

import builtins

import pytest

from regstack.config.schema import EmailConfig


def test_ses_requires_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without aioboto3 installed, instantiating SesEmailService must fail
    with a friendly error, not an obscure ImportError at first send().
    """
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "aioboto3":
            raise ImportError("simulated missing aioboto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from regstack.email.ses import SesEmailService

    with pytest.raises(RuntimeError, match="ses' extra"):
        SesEmailService(EmailConfig(backend="ses"))
