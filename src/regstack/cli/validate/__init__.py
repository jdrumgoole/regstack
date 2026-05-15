"""End-to-end probe of a deployed regstack installation.

See ``regstack validate --help`` for the operator runbook. The Click
command lives in :mod:`regstack.cli.validate.cli`; everything else in
this package is implementation detail (HTTP probe client, log tailer,
phase orchestration, renderers).
"""

from __future__ import annotations
