# Lessons

Patterns worth not repeating. Each entry: what happened, why it slipped
through, and the rule that prevents it.

## Editing docs without rebuilding them

**2026-07-29.** Appended a `#### Added` block straight under
`## Unreleased` in `docs/changelog.md`. That's an H2 → H4 jump, which
MyST reports as `Non-consecutive header level increase`, and the docs
build runs with `-W`, so it failed CI after the push.

**Why it slipped through:** `inv docs` had been run earlier in the same
session — *before* the changelog edit — and everything after it was
`inv lint` plus the test matrix. Neither touches Sphinx. The local gates
were all green and none of them could have caught it.

**Rule:** if a change touches anything under `docs/`, run `inv docs`
before committing. Lint and pytest do not cover the docs build. The
changelog is in the toctree, so a changelog edit *is* a docs edit.

**Guard added:** `tests/unit/test_changelog_structure.py` fails on a
skipped heading level in milliseconds, so this specific defect no longer
needs a CI cycle to surface. It doesn't cover the rest of the docs
build — the rule above still stands.

## A guard that has never failed isn't a guard

**2026-07-29.** Two packaging/structure checks were written this session.
Both were verified by deliberately reintroducing the failure and watching
the test fire — an untracked `.someeditor/config.json` at the repo root
for the sdist allowlist, and the real H2 → H4 structure for the changelog
check — then restoring.

**Why it matters:** an assertion written against already-correct state
passes whether or not its logic is right. The sdist *denylist* test had
passed for months while the tarball shipped a file it was meant to stop.

**Rule:** after writing a regression test, make it fail on purpose once.
If that isn't practical, say so explicitly rather than implying the guard
is proven.

## Differential-test before reporting a divergence

**2026-07-29.** A SecantusDB compatibility spike failed on
`find_one_and_replace` with an immutable-`_id` error, which looked like a
divergence from mongod. Running the identical operation against a real
mongod showed the same rejection: the fault was the spike re-upserting a
model whose `id` the repo had written back as a *string*, so the second
call sent a string `_id` over a stored ObjectId. Both servers were right.

**Rule:** when an alternative implementation appears to diverge from the
reference, run the same operation against the reference before reporting
it. "My test failed against X" is not "X is broken."
