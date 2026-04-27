from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.sms.base import SmsMessage, SmsService

if TYPE_CHECKING:
    from regstack.config.schema import SmsConfig


class SnsSmsService(SmsService):
    """AWS SNS Publish-to-PhoneNumber backend. Requires the optional
    ``sns`` extra (``pip install regstack[sns]`` → aioboto3).
    """

    def __init__(self, config: SmsConfig) -> None:
        try:
            import aioboto3  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "The SNS SMS backend requires the 'sns' extra. "
                "Install with `pip install regstack[sns]` or `uv sync --extra sns`."
            ) from exc
        self._config = config

    async def send(self, message: SmsMessage) -> None:
        import aioboto3

        session = aioboto3.Session()
        async with session.client("sns", region_name=self._config.sns_region) as client:
            kwargs: dict[str, object] = {
                "PhoneNumber": message.to,
                "Message": message.body,
            }
            if message.from_number:
                kwargs["MessageAttributes"] = {
                    "AWS.SNS.SMS.SenderID": {
                        "DataType": "String",
                        "StringValue": message.from_number,
                    }
                }
            await client.publish(**kwargs)
