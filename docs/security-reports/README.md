# Security reports

Daily output from the scheduled security-review agent (see
[`scripts/security-review-prompt.md`](../../scripts/security-review-prompt.md)).

Each file is named `YYYY-MM-DD.md` and follows the structure declared
in the prompt: 🔴 CRITICAL / 🟠 WARNING / 🟡 INFO / 🟢 CLEAN findings,
plus a summary block.

Each report lands on `main` via a short-lived PR that the agent
**squash-merges and deletes in the same run** — the repo has
"Automatically delete head branches" enabled, so no `security-review/*`
branch or open PR is ever left behind. The PR title carries a severity
prefix while it's open:

- `[security-critical]` — at least one CRITICAL finding.
- `[security-warning]` — at least one WARNING finding (no CRITICALs).
- `[security-clean]` — clean bill of health.

CRITICAL / WARNING findings that need a code change are tracked as
GitHub Issues (label `security`), not as lingering PRs — merging a
report documents a finding, it doesn't fix it. See
[`scripts/security-review-prompt.md`](../../scripts/security-review-prompt.md)
("Land the report") for the exact steps.
