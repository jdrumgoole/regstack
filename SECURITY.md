# Security policy

## Supported versions

regstack is in alpha. Until a `1.x` release, only the latest tagged
version on [PyPI](https://pypi.org/project/regstack/) receives security
fixes. Hosts that have already adopted regstack should expect to take
patch upgrades within the same minor line; backports across minor
versions are not promised.

## Reporting a vulnerability

Please **do not** open a public issue or pull request for a suspected
vulnerability.

Instead, email **joe@joedrumgoole.com** with:

- A short description of the issue and the affected component(s).
- The minimum-viable reproduction (a failing test case, a curl
  invocation, or a sequence of API calls).
- Your assessment of impact (e.g. "remote code execution", "auth
  bypass on `/login`", "account enumeration on `/forgot-password`").
- Any suggested mitigation.

You should receive an acknowledgement within **5 business days**. If
you don't, please follow up — your report may have been mis-routed.

We aim to publish a fix within **30 days** of confirmation, faster for
high-severity issues. We will credit reporters in the changelog and
the release notes unless you prefer to remain anonymous.

## What's in scope

- Issues in the regstack package (this repository) that affect a
  default or recommended deployment.
- Issues in the bundled CSS / JS that materially weaken the auth
  boundary.
- Issues in the example app under `examples/minimal/` that would also
  apply to a production embed.

## What's out of scope

- Vulnerabilities in third-party dependencies — please report those
  upstream first; we'll pin / replace as needed.
- Misconfiguration in your own deployment (missing TLS, weak JWT
  secret, etc.). The `regstack doctor` command exists to surface these
  before they become incidents.
- Issues that require an attacker who is already authenticated as a
  superuser.
