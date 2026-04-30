"""In-process Google OAuth provider stub for integration tests.

Used by ``tests/integration/test_oauth_google_router.py``. Exposes a
single class — :class:`FakeGoogleProvider` — that satisfies
:class:`regstack.oauth.base.OAuthProvider` without touching the
network. Tests configure what the provider should "see" on the next
``exchange_code`` call (the same nonce/audience the production
provider would have minted) and the assertions read out the same
shapes the real provider would deliver.
"""

from tests._fake_google.provider import FakeGoogleProvider, FakeGoogleScript

__all__ = ["FakeGoogleProvider", "FakeGoogleScript"]
