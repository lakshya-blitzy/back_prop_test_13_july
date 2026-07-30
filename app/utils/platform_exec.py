"""Platform dispatch -- the Python port of the CI pipeline's ``isUnix()`` branch.

This module realises ONE source construct and nothing else -- AAP Rule T2 requires
each source construct to map onto exactly one module, and the construct here is
the platform branch inside the pipeline's ``stage('Run tests')``. That source is
reproduced verbatim from ``[Jenkins:L7-L11]`` below -- eight spaces of
indentation on the ``if`` / ``} else {`` / ``}`` lines, twelve on the two
bodies::

        if(isUnix()){
            sh "mvn clean test"
        } else {
            bat "mvn clean test"
        }

Both branches ran the *identical* command; only the executor differed -- the
pipeline's ``sh`` step versus its ``bat`` step. The dispatch is therefore about
*which executor*, not *which command*, and this module preserves exactly that
shape: one command, two platform-specific invocations of it.

The mapping is the one the plan prescribes (AAP 0.3.1, semantic transformation
table)::

    if(isUnix()){ sh } else { bat }   ->   platform.system() != "Windows"

Groovy's ``isUnix()`` is a *binary* predicate, not an operating-system matrix:
every non-Windows platform took the ``sh`` branch, so here every non-Windows
value takes the POSIX branch. A cross-platform matrix is explicitly out of scope
(AAP 0.2.2 -- the helper "preserves the ``isUnix()`` branch only, not a
matrix"), which is why exactly two code paths exist and why no third one may
ever be added.

Design contract
---------------
Builds commands, never runs them
    This module has no execution API. The service layer owns process execution
    and the explicit timeout that goes with it, and it is the only layer that
    imports anything capable of starting a process. Nothing here can.
Argument lists only, never a shell string
    Every command is returned as a fresh ``list[str]`` of separate argv tokens,
    so the caller executes it directly, with no shell and no quoting -- the
    ported replacement for the implicit shell semantics of the pipeline's
    ``sh`` and ``bat`` steps. A joined command line is never produced, and a
    bare ``str`` handed in where a token sequence is expected is rejected with
    ``TypeError`` rather than silently treated as a sequence of characters.
    Tokens are passed through verbatim: pre-quoting them would corrupt the
    arguments the callee finally receives.
Injectable decision
    ``platform.system()`` is consulted at call time and is never cached at
    import time, and both a ``system`` name and an ``is_unix`` boolean may be
    injected. That seam is a requirement, not a convenience: continuous
    integration for this project runs on Linux only, so without it the Windows
    branch would be unreachable and half of the ported behaviour would be
    impossible to assert.
Terminal node of the dependency chain
    ``app/utils`` sits at the end of ``api -> services -> reporting -> utils``
    (AAP Rule T7), so only the Python standard library is imported here: no
    first-party module, no third-party distribution, and nothing from the test
    harness.

Public API
----------
:func:`is_unix`
    The predicate itself -- the direct port of Groovy's ``isUnix()``.
:func:`select_command`
    The Strategy: given a POSIX command and a Windows command, return the one
    the resolved platform calls for.
:func:`build_test_command`
    :func:`select_command` applied to the two ported payloads below, ready for
    the service layer to execute.
:data:`POSIX_TEST_COMMAND`, :data:`WINDOWS_TEST_COMMAND`
    The immutable canonical payloads, so callers and tests share one
    definition of each branch instead of restating it.
"""

import logging
import platform
from collections.abc import Sequence
from typing import Final

__all__ = [
    "POSIX_TEST_COMMAND",
    "WINDOWS_TEST_COMMAND",
    "build_test_command",
    "is_unix",
    "select_command",
]

# A module-level logger and nothing more. Handlers, levels and formatters are
# owned exclusively by ``app/logging_config.py``; a utility module must never
# reconfigure logging on behalf of the process that imports it.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The two ported branch payloads.
#
# The pipeline's command strings become the Python entry point assigned to each
# branch (AAP 0.4.1); the work underneath -- the port of ``mvn clean test`` --
# is ``make test``, which both scripts invoke:
#
#     [Jenkins:L8]    sh  "mvn clean test"   ->   ./scripts/run_tests.sh
#     [Jenkins:L10]   bat "mvn clean test"   ->   scripts\run_tests.bat
#
# Each is a ``tuple`` so the canonical definition cannot be mutated through a
# returned value; every public function hands back a fresh ``list`` instead.
#
# The backslash in the Windows payload is escaped deliberately: written as a
# single backslash, ``\r`` would be a carriage return and the payload would
# silently name a different script.
#
# No shell wrapper is prepended to either payload. Rule T6 forbids inventing a
# shell name the source never named, and none is needed: this project pins
# CPython 3.14.6, and CPython dispatches a ``.bat`` payload through the Windows
# command interpreter itself, with correct argument escaping, so a batch script
# runs from a plain argument list.
# ---------------------------------------------------------------------------
POSIX_TEST_COMMAND: Final[tuple[str, ...]] = ("./scripts/run_tests.sh",)
WINDOWS_TEST_COMMAND: Final[tuple[str, ...]] = ("scripts\\run_tests.bat",)


def _resolve_is_unix(system: str | None, is_unix: bool | None) -> bool:
    """Resolve the single boolean the whole dispatch turns on.

    Args:
        system: Platform name to judge instead of consulting the host, or
            ``None`` to consult the host.
        is_unix: Explicit branch override. When not ``None`` it wins over both
            ``system`` and the host, which is what makes either branch
            assertable from a single machine.

    Returns:
        ``True`` when the POSIX branch applies, ``False`` for the Windows
        branch.

    Raises:
        TypeError: If ``is_unix`` is neither ``bool`` nor ``None``, or
            ``system`` is neither ``str`` nor ``None``. Both are rejected
            rather than coerced, so a mistyped override cannot quietly select
            the wrong branch.
    """
    if is_unix is not None:
        if not isinstance(is_unix, bool):
            raise TypeError(f"is_unix must be bool or None, received {type(is_unix).__name__}")
        return is_unix
    if system is not None and not isinstance(system, str):
        raise TypeError(f"system must be str or None, received {type(system).__name__}")
    # THE ported decision, expressed once and only here:
    #   ``if(isUnix())``  [Jenkins:L7]  ->  ``platform.system() != "Windows"``
    # The host is read at call time, never into a module-level constant at
    # import time, so an injected or patched platform name takes effect
    # immediately and no stale value can survive between calls.
    return (platform.system() if system is None else system) != "Windows"


def _validated_tokens(tokens: Sequence[str], *, argument: str) -> list[str]:
    """Copy ``tokens`` into a fresh list, rejecting anything that is not argv.

    An empty token is accepted here on purpose: an empty argv *value* is
    legitimate and is genuinely used by this project -- clearing the preserved
    Gherkin tag filter is spelled ``-m`` followed by an empty string.

    Args:
        tokens: Candidate argv tokens, each already a separate element.
        argument: Name of the caller's parameter, quoted in error messages so a
            failure points at the offending call site.

    Returns:
        A new ``list[str]``. Copying is deliberate: no caller can reach shared
        state through a returned value, and no returned value can be mutated
        into a different command later.

    Raises:
        TypeError: If ``tokens`` is itself a string or bytes-like object -- the
            classic "one joined command line" mistake, which must never be
            mistaken for a token sequence -- or if any element is not a ``str``.
    """
    if isinstance(tokens, str | bytes | bytearray):
        raise TypeError(
            f"{argument} must be a sequence of separate argv tokens, not a joined "
            f"command line (received {type(tokens).__name__}); a command is never "
            "assembled as a single string here"
        )
    validated: list[str] = list(tokens)
    for position, token in enumerate(validated):
        if not isinstance(token, str):
            raise TypeError(f"{argument}[{position}] must be str, received {type(token).__name__}")
    return validated


def _validated_command(command: Sequence[str], *, argument: str) -> list[str]:
    """Validate ``command`` as an executable invocation and return a fresh copy.

    Adds the two requirements that :func:`_validated_tokens` deliberately does
    not impose on plain arguments: a command must have a first token, and that
    token must name something runnable.

    Args:
        command: Candidate argv tokens, the first naming the executable.
        argument: Name of the caller's parameter, quoted in error messages.

    Returns:
        A new ``list[str]`` holding the validated tokens.

    Raises:
        TypeError: Propagated from :func:`_validated_tokens`.
        ValueError: If ``command`` holds no tokens at all, or its first token
            is empty and therefore names no executable.
    """
    tokens: list[str] = _validated_tokens(command, argument=argument)
    if not tokens:
        raise ValueError(f"{argument} must contain at least one token naming the executable")
    if not tokens[0]:
        raise ValueError(f"{argument}[0] must name the executable, received an empty token")
    return tokens


def is_unix(system: str | None = None) -> bool:
    """Report whether the POSIX branch applies -- the port of Groovy ``isUnix()``.

    Args:
        system: Platform name to judge instead of consulting the host. Pass a
            value such as ``"Windows"`` or ``"Linux"`` to evaluate a branch the
            current host cannot reach. When omitted, ``platform.system()`` is
            consulted at call time.

    Returns:
        ``True`` for every platform except Windows, reproducing the pipeline's
        binary predicate exactly: there is no third answer, just as there was
        no third branch.

    Raises:
        TypeError: If ``system`` is neither ``str`` nor ``None``.

    Examples:
        >>> is_unix(system="Linux")
        True
        >>> is_unix(system="Darwin")
        True
        >>> is_unix(system="Windows")
        False
    """
    return _resolve_is_unix(system, None)


def select_command(
    posix_command: Sequence[str],
    windows_command: Sequence[str],
    *,
    system: str | None = None,
    is_unix: bool | None = None,
) -> list[str]:
    r"""Select the POSIX or the Windows command list -- the Strategy of this module.

    Both candidates are validated on every call, whichever one is selected. That
    is intentional: the Windows branch is unreachable on a Linux host, and
    continuous integration here is Linux-only, so eager validation is the only
    way a defect in the unselected branch can ever surface.

    Args:
        posix_command: Argv tokens for the branch the pipeline's ``sh`` step
            covered -- that is, every non-Windows platform.
        windows_command: Argv tokens for the branch the pipeline's ``bat`` step
            covered.
        system: Platform name to judge instead of consulting the host.
        is_unix: Explicit branch override, winning over ``system`` and over the
            host.

    Returns:
        A fresh ``list[str]`` of argv tokens for the resolved branch, ready to
        be executed with no shell involved. A new list is returned on every
        call, so mutating one result cannot affect any other.

    Raises:
        TypeError: If either candidate is a joined command string instead of a
            token sequence, if any token is not a ``str``, or if an override has
            the wrong type.
        ValueError: If either candidate holds no tokens, or its first token is
            empty.

    Examples:
        >>> select_command(["./run.sh"], ["run.bat"], is_unix=True)
        ['./run.sh']
        >>> select_command(["./run.sh"], ["run.bat"], system="Windows")
        ['run.bat']
        >>> select_command(["./run.sh"], ["run.bat"], system="FreeBSD")
        ['./run.sh']
    """
    posix_tokens: list[str] = _validated_command(posix_command, argument="posix_command")
    windows_tokens: list[str] = _validated_command(windows_command, argument="windows_command")
    unix: bool = _resolve_is_unix(system, is_unix)
    selected: list[str] = posix_tokens if unix else windows_tokens
    _LOGGER.debug(
        "platform dispatch: is_unix=%s, selected the %s branch of [Jenkins:L7-L11]: %r",
        unix,
        "sh" if unix else "bat",
        selected,
    )
    return selected


def build_test_command(
    is_unix: bool | None = None,
    *,
    system: str | None = None,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    r"""Build the ported ``stage('Run tests')`` command for the resolved platform.

    This is :func:`select_command` applied to :data:`POSIX_TEST_COMMAND` and
    :data:`WINDOWS_TEST_COMMAND`, and it is the function the service layer calls
    to obtain the command it then executes with its own explicit timeout. No
    timeout, retry or exit-status policy is decided here.

    Args:
        is_unix: Explicit branch override. ``None`` (the default) resolves the
            branch from ``system``, or from the host when that is ``None`` too.
        system: Platform name to judge instead of consulting the host.
        extra_args: Additional arguments to append. Each element is appended as
            its own argv token, never spliced into an existing one and never
            quoted, so no value can extend or alter the command being built. An
            empty string is a valid argument here.

    Returns:
        A fresh ``list[str]``: the branch payload followed by ``extra_args`` in
        order.

    Raises:
        TypeError: If ``extra_args`` is a joined string rather than a sequence
            of tokens, if any element is not a ``str``, or if an override has
            the wrong type.
        ValueError: Never raised for the canonical payloads, which are valid by
            construction; it can only surface if a payload constant is
            overwritten with an invalid value at runtime.

    Examples:
        >>> build_test_command(is_unix=True)
        ['./scripts/run_tests.sh']
        >>> build_test_command(system="Windows")
        ['scripts\\run_tests.bat']
        >>> build_test_command(True, extra_args=["-k", "login"])
        ['./scripts/run_tests.sh', '-k', 'login']
    """
    command: list[str] = select_command(
        POSIX_TEST_COMMAND,
        WINDOWS_TEST_COMMAND,
        system=system,
        is_unix=is_unix,
    )
    if extra_args is not None:
        command.extend(_validated_tokens(extra_args, argument="extra_args"))
    return command
