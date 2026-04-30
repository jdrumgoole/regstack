# Security reports

Daily output from the scheduled security-review agent (see
[`scripts/security-review-prompt.md`](../../scripts/security-review-prompt.md)).

Each file is named `YYYY-MM-DD.md` and follows the structure declared
in the prompt: 🔴 CRITICAL / 🟠 WARNING / 🟡 INFO / 🟢 CLEAN findings,
plus a summary block.

The agent files a PR per report so each review is reviewable as a
diff against `main`. PR title prefix:

- `[security-critical]` — at least one CRITICAL finding.
- `[security-warning]` — at least one WARNING finding (no CRITICALs).
- `[security-clean]` — clean bill of health.
