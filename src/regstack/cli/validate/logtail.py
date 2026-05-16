"""Stream log lines from a remote regstack deployment.

The validator never reads the host's process directly — instead the
operator points ``--log-source`` at a tail-able window onto the host's
stdout (file, ssh, docker, or arbitrary command). We spawn the
appropriate subprocess, copy each stdout line into an asyncio queue,
and let phases ``await`` until a line matching a predicate appears.

Why subprocesses and not native libraries: this keeps the SSH and
Docker integrations dependency-free — the operator already has the
clients installed, we just shell out. We never need to interact with
the remote tooling; we just have to consume its stdout. The trade-off
is that errors surface as "process exited" rather than rich error
types, which is fine for an operator tool — the message we print
("ssh exited with code N before any line was seen") points the
operator at their own command to debug.

Threading model: each tailer owns an :class:`asyncio.subprocess.Process`
plus a single reader task. Both are torn down by :meth:`close`. The
tailer is single-consumer — only the runner pulls lines off the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger("regstack.cli.validate.logtail")


class LogTailError(RuntimeError):
    """The log-source subprocess could not be started, or died early."""


class TokenNotSeenError(RuntimeError):
    """A predicated wait timed out before a matching line appeared."""


@dataclass(slots=True)
class LogSourceSpec:
    """Parsed ``--log-source`` value.

    The ``kind`` is one of ``file``, ``ssh``, ``docker``, ``cmd``;
    ``argv`` is the resolved command we will exec.
    """

    kind: str
    target: str
    argv: list[str]


_KNOWN_KINDS = {"file", "ssh", "docker", "cmd"}


def parse_log_source(spec: str) -> LogSourceSpec:
    """Parse a ``--log-source`` value into a runnable argv.

    Forms accepted:

    - ``file:/var/log/regstack.log``
    - ``ssh:user@host:/var/log/regstack.log``
    - ``docker:<container-name>``
    - ``cmd:<arbitrary shell command>``

    The SSH form intentionally uses ``BatchMode=yes`` so we never block
    on an interactive prompt — fail loudly instead and let the operator
    fix their key setup.
    """
    if ":" not in spec:
        raise ValueError(
            f"--log-source must be 'file:PATH', 'ssh:user@host:PATH', "
            f"'docker:CONTAINER', or 'cmd:<shell>'. Got: {spec!r}"
        )
    kind, _, target = spec.partition(":")
    kind = kind.strip()
    if kind not in _KNOWN_KINDS:
        raise ValueError(f"--log-source kind {kind!r} not one of {sorted(_KNOWN_KINDS)}")
    if not target:
        raise ValueError(f"--log-source {kind!r} requires a target after the colon")

    if kind == "file":
        argv = ["tail", "-n", "0", "-F", target]
    elif kind == "ssh":
        # ssh target form is "user@host:/path"; split off the path so
        # we can shell-escape it for the remote `tail` invocation.
        if ":" not in target:
            raise ValueError(f"--log-source ssh requires 'user@host:/path' (got {target!r})")
        host, _, remote_path = target.partition(":")
        argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            host,
            f"tail -n 0 -F {shlex.quote(remote_path)}",
        ]
    elif kind == "docker":
        argv = ["docker", "logs", "-f", "--since", "1s", target]
    else:  # cmd
        argv = ["sh", "-c", target]
    return LogSourceSpec(kind=kind, target=target, argv=argv)


class LogTailer:
    """Stream lines from a log source into an asyncio.Queue.

    Lifecycle:

    1. ``await tailer.start()`` spawns the subprocess and the reader task.
    2. Phases call :meth:`expect_url` or :meth:`expect_code` to wait for
       a matching line.
    3. ``await tailer.close()`` terminates the subprocess and drains the
       reader. Safe to call multiple times.

    Lines pulled while no phase is waiting accumulate in the queue, so
    a token logged before the validator opened its window is still
    visible — there's no race against the remote process.
    """

    def __init__(self, spec: LogSourceSpec, *, verbose: bool = False) -> None:
        self._spec = spec
        self._verbose = verbose
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def spec(self) -> LogSourceSpec:
        return self._spec

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._spec.argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LogTailError(
                f"--log-source command not found: {self._spec.argv[0]!r} "
                f"(is it installed and on PATH?)"
            ) from exc
        self._reader = asyncio.create_task(self._pump(), name="logtail-reader")

    async def _pump(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if self._verbose:
                    log.info("logtail<< %s", line)
                await self._queue.put(line)
        finally:
            await self._queue.put(None)  # sentinel: stream ended

    async def expect_line(
        self,
        predicate: Callable[[str], bool],
        *,
        timeout: float,
        description: str,
    ) -> str:
        """Wait for a line where ``predicate(line)`` is true.

        Times out with :class:`TokenNotSeenError` after ``timeout`` seconds.
        Raises :class:`LogTailError` if the subprocess died before a
        match was seen — that almost always means the operator's
        log-source command itself failed (wrong path, ssh denied,
        docker container gone) and the message should point at that.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TokenNotSeenError(
                    f"timed out after {timeout:.1f}s waiting for {description} "
                    f"on log-source {self._spec.kind}:{self._spec.target}"
                )
            try:
                line = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError as exc:
                raise TokenNotSeenError(
                    f"timed out after {timeout:.1f}s waiting for {description}"
                ) from exc
            if line is None:
                # Subprocess ended. Read stderr for the operator-facing hint.
                stderr_text = ""
                if self._proc is not None and self._proc.stderr is not None:
                    with contextlib.suppress(Exception):
                        stderr_text = (
                            (await self._proc.stderr.read())
                            .decode("utf-8", errors="replace")
                            .strip()
                        )
                exit_code = self._proc.returncode if self._proc is not None else None
                raise LogTailError(
                    f"log-source {self._spec.argv[0]!r} exited (code={exit_code}) "
                    f"before {description} was seen. stderr: {stderr_text or '<empty>'}"
                )
            if predicate(line):
                return line

    async def expect_url(
        self,
        pattern: re.Pattern[str],
        *,
        must_contain: Iterable[str] = (),
        timeout: float = 30.0,
        description: str | None = None,
    ) -> str:
        """Wait for a line whose URL matches ``pattern`` and contains
        every substring in ``must_contain``. Returns the matched URL.
        """
        desc = description or f"URL matching /{pattern.pattern}/"
        substrs = tuple(must_contain)

        def matches(line: str) -> bool:
            m = pattern.search(line)
            if m is None:
                return False
            url = m.group("url") if "url" in m.groupdict() else m.group(0)
            return all(s in url for s in substrs)

        line = await self.expect_line(matches, timeout=timeout, description=desc)
        m = pattern.search(line)
        assert m is not None
        return m.group("url") if "url" in m.groupdict() else m.group(0)

    async def expect_code(
        self,
        pattern: re.Pattern[str],
        *,
        timeout: float = 30.0,
        description: str | None = None,
    ) -> str:
        """Wait for a line matching ``pattern`` and return the ``code``
        named group.
        """
        desc = description or f"code matching /{pattern.pattern}/"

        def matches(line: str) -> bool:
            return pattern.search(line) is not None

        line = await self.expect_line(matches, timeout=timeout, description=desc)
        m = pattern.search(line)
        assert m is not None
        return m.group("code")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, timeout=5.0)
            except TimeoutError:
                self._reader.cancel()
