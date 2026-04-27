from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.sms.base import SmsService
from regstack.sms.null import NullSmsService

if TYPE_CHECKING:
    from regstack.config.schema import SmsConfig


def build_sms_service(config: SmsConfig) -> SmsService:
    if config.backend == "null":
        return NullSmsService()
    if config.backend == "sns":
        from regstack.sms.sns import SnsSmsService

        return SnsSmsService(config)
    if config.backend == "twilio":
        from regstack.sms.twilio import TwilioSmsService

        return TwilioSmsService(config)
    raise ValueError(f"Unknown SMS backend: {config.backend!r}")
