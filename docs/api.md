# API reference

Auto-generated from docstrings. The most useful entry point is
`regstack.RegStack`; everything else hangs off it.

## Top-level

```{eval-rst}
.. autosummary::
   :toctree: _autosummary
   :recursive:

   regstack.RegStack
   regstack.RegStackConfig
   regstack.EmailConfig
   regstack.SmsConfig
```

## Configuration

```{eval-rst}
.. autoclass:: regstack.config.schema.RegStackConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.config.schema.EmailConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.config.schema.SmsConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autofunction:: regstack.config.loader.load_config
```

## Façade

```{eval-rst}
.. autoclass:: regstack.app.RegStack
   :members:
   :show-inheritance:
```

## Auth primitives

```{eval-rst}
.. autoclass:: regstack.auth.password.PasswordHasher
   :members:

.. autoclass:: regstack.auth.jwt.JwtCodec
   :members:

.. autoclass:: regstack.auth.jwt.TokenPayload
   :members:

.. autoexception:: regstack.auth.jwt.TokenError

.. autoclass:: regstack.auth.clock.SystemClock
   :members:

.. autoclass:: regstack.auth.clock.FrozenClock
   :members:

.. autoclass:: regstack.auth.lockout.LockoutService
   :members:

.. autoclass:: regstack.auth.lockout.LockoutDecision
   :members:

.. autoclass:: regstack.auth.dependencies.AuthDependencies
   :members:
```

## Models

```{eval-rst}
.. autoclass:: regstack.models.user.BaseUser
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.user.UserCreate
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.user.UserPublic
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.pending_registration.PendingRegistration
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.login_attempt.LoginAttempt
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.mfa_code.MfaCode
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

## Repositories

```{eval-rst}
.. autoclass:: regstack.db.repositories.user_repo.UserRepo
   :members:

.. autoexception:: regstack.db.repositories.user_repo.UserAlreadyExistsError

.. autoclass:: regstack.db.repositories.pending_repo.PendingRepo
   :members:

.. autoclass:: regstack.db.repositories.blacklist_repo.BlacklistRepo
   :members:

.. autoclass:: regstack.db.repositories.login_attempt_repo.LoginAttemptRepo
   :members:

.. autoclass:: regstack.db.repositories.mfa_code_repo.MfaCodeRepo
   :members:

.. autoclass:: regstack.db.repositories.mfa_code_repo.MfaVerifyOutcome
   :members:

.. autofunction:: regstack.db.indexes.install_indexes

.. autofunction:: regstack.db.client.make_client
```

## Email + SMS

```{eval-rst}
.. autoclass:: regstack.email.base.EmailMessage
   :members:

.. autoclass:: regstack.email.base.EmailService
   :members:

.. autoclass:: regstack.email.console.ConsoleEmailService
   :members:

.. autoclass:: regstack.email.composer.MailComposer
   :members:

.. autofunction:: regstack.email.factory.build_email_service

.. autoclass:: regstack.sms.base.SmsMessage
   :members:

.. autoclass:: regstack.sms.base.SmsService
   :members:

.. autofunction:: regstack.sms.base.is_valid_e164

.. autoclass:: regstack.sms.null.NullSmsService
   :members:

.. autofunction:: regstack.sms.factory.build_sms_service
```

## Hooks

```{eval-rst}
.. autoclass:: regstack.hooks.events.HookRegistry
   :members:

.. autodata:: regstack.hooks.events.KNOWN_EVENTS
```

## Routers

```{eval-rst}
.. autofunction:: regstack.routers.build_router

.. autofunction:: regstack.ui.pages.build_ui_router

.. autofunction:: regstack.ui.pages.build_ui_environment

.. autofunction:: regstack.ui.pages.default_static_dir
```
