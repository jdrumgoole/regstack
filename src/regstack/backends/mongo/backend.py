from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.backends.base import Backend, BackendKind
from regstack.backends.mongo.client import make_client, parse_database_name
from regstack.backends.mongo.indexes import install_indexes
from regstack.backends.mongo.repositories.blacklist_repo import BlacklistRepo
from regstack.backends.mongo.repositories.login_attempt_repo import LoginAttemptRepo
from regstack.backends.mongo.repositories.mfa_code_repo import MfaCodeRepo
from regstack.backends.mongo.repositories.oauth_identity_repo import (
    MongoOAuthIdentityRepo,
)
from regstack.backends.mongo.repositories.oauth_state_repo import MongoOAuthStateRepo
from regstack.backends.mongo.repositories.pending_repo import PendingRepo
from regstack.backends.mongo.repositories.user_repo import UserRepo

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.database import AsyncDatabase

    from regstack.auth.clock import Clock
    from regstack.config.schema import RegStackConfig


class MongoBackend(Backend):
    """MongoDB-backed regstack storage. Uses PyMongo's async client and
    delegates TTL handling to MongoDB's TTL indexes.
    """

    kind = BackendKind.MONGO

    def __init__(self, *, config: RegStackConfig, clock: Clock) -> None:
        super().__init__(config=config, clock=clock)
        self._client: AsyncMongoClient = make_client(config)
        self._db: AsyncDatabase = self._client[parse_database_name(config)]

        self.users = UserRepo(self._db, config.user_collection, clock=clock)
        self.pending = PendingRepo(self._db, config.pending_collection)
        self.blacklist = BlacklistRepo(self._db, config.blacklist_collection)
        self.attempts = LoginAttemptRepo(self._db, config.login_attempt_collection)
        self.mfa_codes = MfaCodeRepo(self._db, config.mfa_code_collection, clock=clock)
        self.oauth_identities = MongoOAuthIdentityRepo(self._db, config.oauth_identity_collection)
        self.oauth_states = MongoOAuthStateRepo(self._db, config.oauth_state_collection)

    async def install_schema(self) -> None:
        await install_indexes(self._db, self.config)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> None:
        await self._client.admin.command("ping")

    # --- Mongo-specific helpers (used by tests + doctor) -----------------

    @property
    def database(self) -> AsyncDatabase:
        return self._db

    @property
    def client(self) -> AsyncMongoClient:
        return self._client
