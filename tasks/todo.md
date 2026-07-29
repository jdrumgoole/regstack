# SecantusDB support

**Decisions taken** (2026-07-28): SecantusDB is *not* a new `BackendKind` —
it is the existing Mongo backend, because the repositories are identical
over the wire. It *is* a production-supported target, so it gets a runtime
extra, deployment docs, and a place in the test matrix where a
compatibility regression fails the build.

## Why this is small

regstack's Mongo layer needs five things, and SecantusDB has all of them:

| regstack depends on | SecantusDB |
| --- | --- |
| `unique` indexes (`users.email`, two on `oauth_identities`, `mfa_codes`) | Honoured |
| `E11000` → `DuplicateKeyError` | mongod-verbatim message, real `DuplicateKeyError` |
| `find_one_and_replace` (pending + mfa repos) | Supported, explicitly hardened |
| `expireAfterSeconds` TTL (5 collections) | Honoured; 60s sweeper, `ttl_sweep_seconds` configurable |
| async pymongo client | `pymongo_async_validation` gauge runs upstream's async suite |

regstack uses **no** transactions or sessions, which is where most of
SecantusDB's documented divergences live. Geo operators, capped
collections and profiling are also unused.

## Work

### 1. Doctor: stop reporting SecantusDB as CVE-vulnerable

- [ ] `_check_mongo_server_version` reads `secantusVersion` from `buildInfo`
- [ ] When present, report `SecantusDB <ver> (MongoDB 7.0 compatibility)` and
      skip `_assess_mongo_server_version`
- [ ] Regression test: a `buildInfo` carrying `secantusVersion` must not
      produce a MongoBleed warning

**This is a live bug today.** SecantusDB deliberately reports
`buildInfo.version = "7.0.0"`; `_MONGO_PATCHED_BASELINE[(7,0)]` is
`(7,0,28)`, so `regstack doctor` currently tells anyone pointing at
SecantusDB that their server is vulnerable to CVE-2025-14847. It isn't —
that's a bug in mongod's zlib path, and SecantusDB does not share that code.

### 2. Test matrix: `secantus` as a fourth backend

- [ ] Module-level lazy singleton in `tests/conftest.py` starting one
      `SecantusDBServer(port=0, storage_path=":memory:")` per xdist worker,
      shut down in `pytest_sessionfinish`
- [ ] `_make_database_url("secantus", …)` returns `f"{server.uri}/{db_name}"`
      with the same per-worker-per-token db naming the mongo path uses
- [ ] `_resolve_backends` accepts `secantus`; skip cleanly when the package
      isn't installed, the way the mongo-only tests already skip
- [ ] `inv test-secantus`, and add it to `inv test-all`
- [ ] CI matrix entry — needs **no service container**, unlike mongo/postgres

Port 0 gives a kernel-assigned port, so parallel workers cannot collide.
`:memory:` storage means no cleanup sweep and no leftover databases.

### 3. Production support

- [ ] Optional extra `secantus = ["SecantusDB>=0.6.0b2"]` (embedding only —
      a daemon deployment needs nothing beyond pymongo)
- [ ] Docs: deployment section covering the daemon (`secantusd-py` / Rust
      binary), the single-node constraints, and TTL sweep cadence
- [ ] `CLAUDE.md`: add `secantus` to the every-backend-or-it's-red rule
- [ ] `docs/changelog.md` under `## Unreleased`

## Risks

- **0.6.0b2 is a beta.** Promising production support means its stability
  becomes regstack's problem, and a compatibility regression becomes a
  release blocker. Floor pinned at `>=0.6.0b2`.
- **No macOS x86_64 wheel** (arm64 only). Intel Mac contributors would
  build from source or skip the backend.
- **TTL timing.** regstack's `FrozenClock` sits at 2125 to dodge mongod's
  reaper; SecantusDB's sweeper behaves the same way, so no change expected —
  but this is the most likely source of a surprise, and worth watching in
  the first full run.

## Review

All three work items landed as specced. Two things worth recording.

### The spike nearly produced a false bug report

The first compatibility spike failed on `find_one_and_replace` with
*"Performing an update on the path '_id' would modify the immutable
field '_id'"*, which looked like a SecantusDB divergence. It wasn't.
Running the same operation against real mongod and against SecantusDB
side by side showed identical behaviour in both directions:

| replacement doc | mongod | SecantusDB |
| --- | --- | --- |
| same `_id` as stored | accepted | accepted |
| different `_id` | rejected | rejected |
| stringified `_id` over a stored ObjectId | rejected | rejected |

The spike had re-upserted a model object whose `id` the repo had already
written back **as a string**, so the second `to_mongo()` sent a string
`_id` against a stored ObjectId — a genuine `_id` change, correctly
rejected by both. No real callsite does this; all three build a fresh
`PendingRegistration` with `id=None`. Lesson: differential-test against
the reference implementation before reporting a divergence.

### Un-skipping was the substantive part

The first green `secantus` run was 655 passed / **24 skipped**, and the
skips were the Mongo-specific tests — TTL and unique index behaviour,
ObjectId guards, the `$jsonSchema` validator, the lockout and MFA repos.
Those are precisely the tests a compatibility regression would trip, so
reporting that run as "SecantusDB support works" would have been
misleading. Two changes fixed it:

- `mongo_client` now falls back to the embedded server instead of
  skipping, since those are wire-protocol tests satisfied by either.
- `wire_protocol_backend()` replaces literal `"mongo"` in `backend_kind`
  overrides, so pinned tests follow whichever server the run has rather
  than demanding mongod.

Final: **678 passed, 1 skipped** under `secantus`, identical to the
`mongo` count. The one remaining skip is unrelated (a shape-only regex
test). SecantusDB also runs the suite in ~22s against mongod's ~51s.

The `test_indexes.py` un-pinning surfaced a real assertion that only
held for mongod (`"server" in result.detail`), which is exactly the
end-to-end confirmation that the doctor fix works against a live
SecantusDB: the detail read `SecantusDB 0.6.0b3 (MongoDB 7.0.0
compatibility)`.

### Deviations from the plan

- Installed version is **0.6.0b3**, not the 0.6.0b2 the plan floored at.
  The floor stays `>=0.6.0b2` — it's a minimum, and b3 satisfies it.
- The dev-extra dependency carries a marker excluding macOS x86_64,
  since no wheel exists there and `uv sync --extra dev` would otherwise
  try to build WiredTiger from source on an Intel Mac. PEP 508 has no
  boolean NOT, so it's written in De Morgan form.
- CI needed **no matrix change** — `_resolve_backends` adds `secantus`
  whenever the package imports, and the dev extra installs it on Linux.
  Only comments and a step name were touched.
- `inv coverage`'s missing-backend banner would have raised `KeyError`
  on a port-less backend; `_describe_missing_backend` now reports the
  uninstalled package instead of inventing a port.
