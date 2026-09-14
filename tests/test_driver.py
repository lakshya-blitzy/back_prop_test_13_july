r"""Tests for the worker-local WebDriver lifecycle - ``app/automation/driver.py``.

The module under test is the Python port of ``Driver.java``, which AAP 0.2.1
holds as REFERENCE, and this suite is the third statement of the lifecycle AAP
0.3.3 requires ``features/environment.py``, ``app/automation/driver.py`` and
this module to state identically:

    Each worker process holds one slot for a driver.  ``before_scenario``
    creates a driver into that slot if it is empty; ``after_scenario`` captures
    failure evidence, calls ``quit()``, and clears the slot.  Exactly one live
    session exists per worker at any moment, every scenario gets a fresh
    session, and no code ever touches a driver after ``quit()``.

What is asserted, and where it comes from
-----------------------------------------
The module under test enumerates the obligations in its own docstring ("What
``tests/test_driver.py`` asserts"), and each is anchored in a Java line by its
parity map.  Every one of them has an asserting test below:

* **One slot per worker** (``Driver.java:17``) - the session lives in a
  ``threading.local()``, so a second thread starts from an empty slot.
* **Create on demand and reuse** (``:22``) - one construction, and a second
  call returns the identical object having provisioned nothing further.
* **Provision, then construct** (``:31-32``, ``:38``) - the manager installs
  the binary, its path reaches the ``Service``, and the ``Service`` reaches the
  browser constructor, in that order.
* **Set-up order** (``:32-34``, ``:39-40``) - the slot is populated first, then
  ``maximize_window()``, then ``implicitly_wait(10)`` - seconds, never
  milliseconds.
* **Firefox provisions with Gecko** (AAP deviation 5 / Conflict 6) -
  ``Driver.java:37`` provisions the *Chrome* binary in the Firefox branch, and
  the port corrects it; the Chrome manager's call count is asserted to be zero
  so that a regression to the source's defect fails here.
* **No default branch** (``:29-42``) - ``"Chrome"``, ``"CHROME"``,
  ``" chrome"``, ``"safari"``, ``"edge"``, ``""`` and an unset key each yield
  ``None``, construct nothing, provision nothing and raise nothing, per AAP
  0.4.1: *"Any other value fails at first driver use, as today."*  There is no
  "already failed" state, so a later call tries again.
* **Guarded teardown, quit once, clear the slot** (``:51-53``), and a failing
  ``quit()`` **propagates** while the slot is cleared in a ``finally``.
* **The provisioning policy** that module's docstring states: one verification
  gate over every path that reaches a ``Service``; a pre-provisioned system
  binary preferred over the managers; the managers called with the ambient
  ``webdriver-manager`` environment neutralized - its trust settings, and the
  proxy and certificate-authority variables a hidden ``.env`` can restore -
  and with a no-shell, absolute-path browser-version probe; a private,
  verified, ``HOME``-independent cache root; every archive member validated
  before it is unpacked; and an ambient ``SE_CHROMEDRIVER`` or
  ``SE_GECKODRIVER`` unable to change the path Selenium receives.
* **The containment of the browser process tree**: the driver process is asked
  for its own process group inside this session, the handle is captured while
  the service starts it and adopted by the session, and teardown tells a
  verified release from "no local process" and from "never contained" -
  raising :exc:`app.automation.driver.DriverTeardownError` for the last.

Nothing here starts a browser, downloads a driver binary or touches the
network.  Every seam the module under test reaches outside itself - the two
managers, the two ``Service`` factories, the ``webdriver`` namespace, the
system directories it searches and its private cache root - is replaced by a
recording double or a temporary directory installed **on the module under
test**, which is the only patch point that works: ``get_browser`` is imported
by name, so patching ``app.config.get_browser`` would not be seen, and the same
reasoning applies to every other name the module binds at import time.

Three platforms, one suite
--------------------------
AAP 0.8 requires Chrome and Firefox on Windows, Linux and macOS, and both
runner scripts run this suite as a mandatory gate before any browser starts -
so it has to pass on all three.  Two rules keep it honest about that:

* a test that needs a facility only one platform has - ``process_group``,
  ``os.getpgid``, ``os.killpg``, ``/bin/ps``, ``os.mkfifo``, a symbolic link,
  POSIX mode bits, ``os.chown`` or the real Win32 API - carries
  :data:`POSIX_ONLY` or :data:`WINDOWS_ONLY` and *skips* where that facility is
  absent, rather than failing there;
* a test that *fakes* a platform - through ``_IS_WINDOWS``, ``_IS_MACOS``,
  ``_HAS_POSIX_PERMISSIONS``, ``_HAS_PROCESS_GROUPS`` or ``_kernel32`` - runs
  everywhere and is what gives the other platforms' branches their coverage on
  whichever platform the suite happens to run.

Three contracts are asserted over the source as well as over behaviour
----------------------------------------------------------------------
Each has a part that no single call can show, and that part is asserted over
the *syntax tree* of the module under test - see "Reading the source itself"
below.

**No default branch** substantively forbids a **fallback**, not a log line, so
the source-level tests pin the absence of every fallback: only ``"chrome"`` and
``"firefox"`` are ever compared against, only ``webdriver.Chrome`` and
``webdriver.Firefox`` are ever constructed, a session is adopted only from
inside those two branches, and the unmatched path constructs nothing,
provisions nothing, adopts nothing, raises nothing and rebinds nothing.  The
behavioural half adds that the unmatched path emits no record at WARNING or
above, so it can never masquerade as a warning that something was substituted
or retried.

**Teardown reports its failures.**  The clear happens inside a ``finally``, the
function catches neither everything nor ``BaseException`` - a
``KeyboardInterrupt`` or a worker shutdown has to stay able to stop the run -
and any handler it does have reports what it caught.  ``quit_driver`` has no
handler at all, so the last two hold with nothing to inspect, which is the
strongest form of them: the failure propagates.

**A driver path is verified before it is executed.**  Behaviourally, each
refusal class is exercised through :func:`app.automation.driver.get_driver` and
raises rather than reaching a browser constructor.

The private seam
----------------
``driver.py`` documents ``_holder`` and ``_session()`` as the intended test
seam, private so production code cannot orphan a live browser process with
them.  ``tests/conftest.py``'s autouse isolation fixture replaces ``_holder``
around every test, so each begins with an empty slot; this module reads
``_session()`` and never writes to it.
"""

from __future__ import annotations

import ast
import ctypes
import io
import logging
import os
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, NamedTuple

import pytest
from conftest import StubDriver
from webdriver_manager.core.driver_cache import DriverCacheManager
from webdriver_manager.core.os_manager import ChromeType, OperationSystemManager

from app import automation, config
from app.automation import driver as driver_module

# --------------------------------------------------------------------------
# Fixed names and values
#
# The driver executables the harness materializes are empty files with the
# execute bit set, created inside the test's own temporary directory.  They
# have to be real files owned by this user in a directory nobody else can
# write, because the module under test verifies exactly that before handing a
# path to Selenium - and they are never executed: the browser constructors are
# doubles, and the only subprocess the module ever starts is a browser
# ``--version`` probe, which no test lets reach a real binary.
# --------------------------------------------------------------------------

#: The suffix an executable carries on this platform, so that the driver files
#: these tests materialize are the ones the module under test searches for.
#: Spelled here rather than imported from the module under test, which is what
#: keeps this suite an independent statement of the contract.
EXECUTABLE_SUFFIX: Final[str] = ".exe" if os.name == "nt" else ""

#: File name of the driver the Chrome branch provisions.
CHROME_DRIVER_NAME: Final[str] = f"chromedriver{EXECUTABLE_SUFFIX}"

#: File name of the driver the Firefox branch provisions.
GECKO_DRIVER_NAME: Final[str] = f"geckodriver{EXECUTABLE_SUFFIX}"

#: Mode a materialized driver executable is created with: executable by its
#: owner, writable by nobody else.  Anything wider is what the refusal tests
#: install deliberately.
TRUSTED_EXECUTABLE_MODE: Final[int] = 0o755

#: The implicit wait ``Driver.java:34`` sets, in **seconds**.  The Python
#: binding takes seconds, so a millisecond literal would fail the assertion in
#: :func:`test_new_session_is_maximized_then_given_a_ten_second_implicit_wait`.
IMPLICIT_WAIT_SECONDS: Final[int] = 10

# Recorded event names.  One constant per observable interaction, so that a
# sequence assertion reads as the Java branch it ports.
CHROME_MANAGER_CONSTRUCT: Final[str] = "ChromeDriverManager()"
CHROME_MANAGER_INSTALL: Final[str] = "ChromeDriverManager.install"
GECKO_MANAGER_CONSTRUCT: Final[str] = "GeckoDriverManager()"
GECKO_MANAGER_INSTALL: Final[str] = "GeckoDriverManager.install"
CHROME_SERVICE: Final[str] = "ChromeService"
FIREFOX_SERVICE: Final[str] = "FirefoxService"
WEBDRIVER_CHROME: Final[str] = "webdriver.Chrome"
WEBDRIVER_FIREFOX: Final[str] = "webdriver.Firefox"

#: The driver-path environment variables selenium's own ``Service`` classes
#: default to - ``chromium/service.py:50`` and ``firefox/service.py:49``, each
#: written ``key = key or "SE_..."`` so that passing ``None`` does not disable
#: the lookup.  An ambient value in either is what
#: ``common/service.py:79``'s ``self.env_path() or executable_path`` would
#: execute in place of a provisioned path.
CHROME_DRIVER_PATH_ENV_KEY: Final[str] = "SE_CHROMEDRIVER"
GECKO_DRIVER_PATH_ENV_KEY: Final[str] = "SE_GECKODRIVER"

#: The environment variables the module under test forces around a manager
#: call, and the value each must hold while ``install()`` runs: certificate
#: verification on, and the relocatable cache off.
FORCED_WDM_SETTINGS: Final[tuple[tuple[str, str], ...]] = (
    ("WDM_SSL_VERIFY", "true"),
    ("WDM_LOCAL", "false"),
)

#: The provisioning-library variables that must be absent while ``install()``
#: runs, whatever the ambient environment or a hidden ``.env`` file set them to.
REMOVED_WDM_SETTINGS: Final[tuple[str, ...]] = (
    "WDM_LOG",
    "WDM_LOG_LEVEL",
    "WDM_PROGRESS_BAR",
    "PYTEST_XDIST_WORKER",
)

#: The transport variables that must also be absent while ``install()`` runs,
#: in both spellings.  The HTTP stack beneath the provisioning library reads
#: these to decide where a driver binary is fetched from and what may sign for
#: it, and a hidden ``.env`` file can restore them after a worker launcher has
#: filtered them out - so neutralizing them is part of the provisioning call
#: rather than only part of launching a worker.
TRANSPORT_SETTINGS: Final[tuple[str, ...]] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "requests_ca_bundle",
    "curl_ca_bundle",
    "ssl_cert_file",
    "ssl_cert_dir",
)

#: The fixed name of the private provisioning cache root, under the system
#: temporary directory and suffixed with this account's user id on POSIX.
CACHE_DIRECTORY_NAME: Final[str] = "testinium-qa-drivers"

#: The mode bits that would let somebody other than the owner write a
#: directory this module creates and later executes the contents of.
PERMISSIVE_MODE_BITS: Final[int] = stat.S_IWGRP | stat.S_IWOTH

#: The whole of the Chrome branch, in order - ``Driver.java:31-32``.
CHROME_BRANCH_SEQUENCE: Final[tuple[str, ...]] = (
    CHROME_MANAGER_CONSTRUCT,
    CHROME_MANAGER_INSTALL,
    CHROME_SERVICE,
    WEBDRIVER_CHROME,
)

#: The whole of the Firefox branch, in order - ``Driver.java:37-38`` with the
#: manager corrected to Gecko (AAP deviation 5).
FIREFOX_BRANCH_SEQUENCE: Final[tuple[str, ...]] = (
    GECKO_MANAGER_CONSTRUCT,
    GECKO_MANAGER_INSTALL,
    FIREFOX_SERVICE,
    WEBDRIVER_FIREFOX,
)

#: The two settings ``_adopt_session`` applies, in the Java order ``:33`` then
#: ``:34``, as :meth:`conftest.StubDriver.operations` reports them.
SETUP_OPERATIONS: Final[tuple[str, ...]] = ("maximize_window", "implicitly_wait")

#: Browser values that match neither branch.  The first three are the Java
#: ``switch``'s case sensitivity and lack of trimming - ``equals`` comparison,
#: so normalizing them would accept input the source rejects; the next three
#: are plain unrecognised values; ``None`` is the key being unset, which
#: ``app/config.py`` returns unchanged.
UNRECOGNISED_BROWSERS: Final[tuple[str | None, ...]] = (
    "Chrome",
    "CHROME",
    " chrome",
    "safari",
    "edge",
    "",
    None,
)

#: Logger under which a record from the module under test would arrive.  The
#: negative assertions below are scoped to this tree, so an unrelated record
#: emitted by another part of the application cannot satisfy or break them.
DRIVER_LOGGER_PREFIX: Final[str] = "app.automation"

#: The package logger records propagate to.  Its level is raised around the
#: teardown-failure capture so that a WARNING the module might emit cannot be
#: filtered out before ``caplog`` sees it - which is what lets that test assert
#: the failure was **not** turned into a warning-and-return.
APP_LOGGER_NAME: Final[str] = "app"

#: Guard for a test that exercises a facility only one platform has.  AAP 0.8
#: requires this suite to pass on Windows, Linux and macOS, and both runner
#: scripts run it as a mandatory gate before any browser starts - so a test
#: that needs ``process_group``, ``os.getpgid``, ``os.killpg``, ``/bin/ps``,
#: POSIX mode bits or the Win32 API has to *skip* where that facility does not
#: exist rather than fail there.  ``skipif`` is built into pytest, so no marker
#: registration is needed and ``--strict-markers`` is satisfied.
#:
#: Every test that fakes a platform - through ``_HAS_PROCESS_GROUPS``,
#: ``_kernel32``, ``_HAS_POSIX_PERMISSIONS`` or ``sys.platform`` - is
#: deliberately left unguarded: those are what give the branches of the other
#: platform their coverage, on whichever platform the suite happens to run.
POSIX_ONLY: Final[pytest.MarkDecorator] = pytest.mark.skipif(
    os.name != "posix",
    reason="exercises a POSIX process-group, signal or permission facility",
)

#: Guard for the mirror image: a test whose subject is the real Win32 API.
WINDOWS_ONLY: Final[pytest.MarkDecorator] = pytest.mark.skipif(
    os.name != "nt",
    reason="exercises the real Win32 kernel32 binding",
)


# --------------------------------------------------------------------------
# The recording doubles
#
# Deliberately not ``unittest.mock``: the assertions below are about the
# *order* of events across four collaborators, which one shared ordered log
# expresses directly and four independent mock call lists do not.
# --------------------------------------------------------------------------


class RecordedCall:
    """One entry of :class:`CallRecorder`'s ordered log.

    :param target: The event name, one of the module constants above.
    :param args: Positional arguments exactly as they were passed.
    :param kwargs: Keyword arguments exactly as they were passed.
    """

    __slots__ = ("args", "kwargs", "target")

    def __init__(self, target: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.target = target
        self.args = args
        self.kwargs = kwargs

    def values(self) -> tuple[Any, ...]:
        """Every argument value, positional first.

        Used where the contract is *what* was handed over rather than how: the
        provisioned path has to reach the ``Service`` factory, and whether the
        port passes it positionally or by keyword is not part of the contract.

        :returns: Positional arguments followed by keyword argument values.
        """
        return self.args + tuple(self.kwargs.values())

    def __repr__(self) -> str:
        """Render the call as it was made, which is what a failure needs.

        :returns: A short representation.
        """
        return f"<RecordedCall {self.target} args={self.args!r} kwargs={self.kwargs!r}>"


class CallRecorder:
    """One ordered log shared by every double in a :class:`DriverHarness`.

    A single log is what makes "provision before construct" assertable: the
    events of both collaborators appear in one sequence, so their relative
    order is a property of the log rather than something a test has to infer.
    """

    __slots__ = ("calls",)

    def __init__(self) -> None:
        #: Every recorded call, in the order it happened.
        self.calls: list[RecordedCall] = []

    def record(self, target: str, *args: Any, **kwargs: Any) -> None:
        """Append one event to the log.

        :param target: The event name.
        :param args: Positional arguments, stored unmodified.
        :param kwargs: Keyword arguments, stored unmodified.
        :returns: ``None``.
        """
        self.calls.append(RecordedCall(target, args, dict(kwargs)))

    def targets(self) -> tuple[str, ...]:
        """The event names in order, with arguments dropped.

        :returns: The event names in call order.
        """
        return tuple(call.target for call in self.calls)

    def calls_to(self, target: str) -> tuple[RecordedCall, ...]:
        """Every recorded call to one event name, in order.

        :param target: The event name.
        :returns: The matching calls, in call order.
        """
        return tuple(call for call in self.calls if call.target == target)

    def count_of(self, target: str) -> int:
        """How many times one event was recorded.

        The direct expression of the two "never touched" obligations: the
        Chrome manager's count in the Firefox branch, and every count in the
        unrecognised-browser case.

        :param target: The event name.
        :returns: The number of occurrences.
        """
        return sum(1 for call in self.calls if call.target == target)

    def __repr__(self) -> str:
        """Summarise the log as its event sequence.

        :returns: A short representation.
        """
        return f"<CallRecorder {self.targets()!r}>"


class FakeDriverManager:
    """Stand-in for ``ChromeDriverManager`` / ``GeckoDriverManager``.

    Callable, because the module under test calls the *class* -
    ``ChromeDriverManager(os_system_manager=...)`` - and then ``.install()`` on
    the result.  The call returns ``self``, so one object records both events
    and a test can read the construction and the installation as two entries of
    one log.

    Nothing is downloaded and no network call is made: :meth:`install` reports
    :attr:`binary_path`, which the harness materializes as a real, verifiable
    file.  Both :attr:`binary_path` and :attr:`install_error` are writable, so
    a test can make provisioning return an untrustworthy path or fail outright
    without replacing the double.

    :param recorder: The shared log.
    :param construct_target: Event name recorded when the class is called.
    :param install_target: Event name recorded by :meth:`install`.
    :param binary_path: The path :meth:`install` reports.
    """

    __slots__ = (
        "_construct_target",
        "_install_target",
        "_recorder",
        "binary_path",
        "install_error",
        "install_hook",
    )

    def __init__(
        self,
        recorder: CallRecorder,
        construct_target: str,
        install_target: str,
        binary_path: str,
    ) -> None:
        self._recorder = recorder
        self._construct_target = construct_target
        self._install_target = install_target
        #: The path :meth:`install` reports having provisioned.
        self.binary_path = binary_path
        #: Set to an exception to make :meth:`install` fail with it.
        self.install_error: BaseException | None = None
        #: Set to a callable to observe the process state during the call -
        #: the only moment at which the provisioning library reads its
        #: settings from the environment.
        self.install_hook: Callable[[], None] | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> FakeDriverManager:
        """Record the manager's construction and return the recorder itself.

        :param args: Constructor positional arguments, recorded as passed.
        :param kwargs: Constructor keyword arguments, recorded as passed -
            which is how the ``os_system_manager`` the module under test
            supplies becomes assertable.
        :returns: ``self``, so that ``.install()`` lands in the same log.
        """
        self._recorder.record(self._construct_target, *args, **kwargs)
        return self

    def install(self) -> str:
        """Record the provisioning and report the binary path, or fail.

        :returns: :attr:`binary_path`.
        :raises BaseException: :attr:`install_error`, when a test set one.
        """
        self._recorder.record(self._install_target)

        if self.install_hook is not None:
            self.install_hook()

        if self.install_error is not None:
            raise self.install_error

        return self.binary_path


class ServiceDouble:
    """Stand-in for a selenium ``Service`` object, with its path semantics.

    Three jobs.  It is identifiable, so a test can assert that the object the
    ``Service`` factory produced is the object the browser constructor
    received; it carries what it was built from; and it reproduces the one
    behaviour of the real class that decides which binary would be executed -
    ``selenium/webdriver/common/service.py:79``'s
    ``self._path = self.env_path() or executable_path``, where ``env_path()``
    reads the driver-path environment variable named by
    :attr:`DRIVER_PATH_ENV_KEY`.  Reproducing it here is what makes the ambient
    ``SE_CHROMEDRIVER`` / ``SE_GECKODRIVER`` test meaningful: without the
    module under test overriding both attributes, this double would carry the
    ambient value exactly as selenium would.

    It also reproduces the one *lifecycle* behaviour the module under test
    reaches into: ``start()``, which is where selenium spawns the driver
    executable and which both browser constructors call unconditionally
    (``chromium/webdriver.py:55``, ``firefox/webdriver.py:60``).  Before that
    call there is no ``process`` attribute worth reading, and after it there is
    - which is exactly the transition the containment capture is installed
    around, so the double has to have it for that wrapper to be observable at
    all.  ``start`` is bound as an *instance* attribute because that is the
    attribute the module under test replaces.

    :param kind: Which factory produced it, for readable failures.
    :param carried: Every argument value the factory was given.
    :param executable_path: The path the factory was asked to run.
    :param env_key: The driver-path environment variable selenium defaults to
        for this browser.
    :param spawned: The process object ``start()`` publishes as
        :attr:`process`, or ``None`` for a service that starts no local
        process.
    """

    __slots__ = (
        "DRIVER_PATH_ENV_KEY",
        "_spawned",
        "carried",
        "kind",
        "path",
        "process",
        "start",
        "start_count",
    )

    def __init__(
        self,
        kind: str,
        carried: tuple[Any, ...],
        executable_path: str | None,
        env_key: str,
        spawned: Any = None,
    ) -> None:
        self.kind = kind
        self.carried = carried
        #: The variable selenium would consult; ``None`` once cleared.
        self.DRIVER_PATH_ENV_KEY: str | None = env_key
        #: The path that would be executed.
        self.path = os.environ.get(env_key) or executable_path
        self._spawned = spawned
        #: The driver process, absent until :meth:`begin` publishes it - the
        #: same ordering the real class has.
        self.process: Any = None
        #: How many times the service was started.
        self.start_count = 0
        #: Bound here rather than declared as a method, because the module
        #: under test wraps this attribute on the instance.
        self.start = self.begin

    def begin(self) -> None:
        """Start the service: publish the process the driver would have spawned.

        :returns: ``None``.
        """
        self.start_count += 1
        self.process = self._spawned

    def __repr__(self) -> str:
        """Render the kind, the carried path and the effective path.

        :returns: A short representation.
        """
        return f"<ServiceDouble {self.kind} carrying {self.carried!r} path={self.path!r}>"


class FakeServiceFactory:
    """Stand-in for ``ChromeService`` / ``FirefoxService``.

    :param recorder: The shared log.
    :param target: Event name recorded on each call.
    :param kind: Passed through to the :class:`ServiceDouble` it produces.
    :param env_key: The driver-path environment variable selenium's own class
        for this browser defaults to - ``"SE_CHROMEDRIVER"`` at
        ``chromium/service.py:50``, ``"SE_GECKODRIVER"`` at
        ``firefox/service.py:49``.
    """

    __slots__ = ("_env_key", "_kind", "_recorder", "_target", "process", "produced")

    def __init__(self, recorder: CallRecorder, target: str, kind: str, env_key: str) -> None:
        self._recorder = recorder
        self._target = target
        self._kind = kind
        self._env_key = env_key
        #: Every service object handed out, in order.
        self.produced: list[ServiceDouble] = []
        #: The process the next service will publish when it is started - what
        #: a test programs to give the containment capture something real to
        #: take ownership of.  ``None`` is the ordinary case: a service that
        #: starts no local driver process, and therefore a session with no
        #: process tree behind it.
        self.process: Any = None

    def __call__(self, *args: Any, **kwargs: Any) -> ServiceDouble:
        """Record the call and produce a fresh, identifiable service object.

        :param args: Positional arguments, recorded as passed.
        :param kwargs: Keyword arguments, recorded as passed.
        :returns: A new :class:`ServiceDouble` carrying those values.
        """
        self._recorder.record(self._target, *args, **kwargs)
        values = args + tuple(kwargs.values())
        service = ServiceDouble(
            self._kind,
            values,
            values[0] if values else None,
            self._env_key,
            self.process,
        )
        self.produced.append(service)
        return service


class FakeBrowserConstructor:
    """Stand-in for ``webdriver.Chrome`` / ``webdriver.Firefox``.

    No browser process is started: the call is recorded and a stubbed session
    is handed back, which is what makes the maximize, the implicit wait and the
    ``quit()`` observable.

    :param recorder: The shared log.
    :param target: Event name recorded on each call.
    :param session_source: Callable producing the session to return.
    """

    __slots__ = ("_recorder", "_session_source", "_target")

    def __init__(
        self,
        recorder: CallRecorder,
        target: str,
        session_source: Callable[[], Any],
    ) -> None:
        self._recorder = recorder
        self._target = target
        self._session_source = session_source

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Record the construction, start the service, return a stubbed session.

        Starting the service is not incidental: both selenium constructors call
        ``self.service.start()`` unconditionally before the WebDriver handshake
        (``chromium/webdriver.py:55``, ``firefox/webdriver.py:60``), and that
        call is what the module under test wraps to contain the driver process.
        A double that skipped it would leave every containment assertion in
        this module testing a code path production never takes.

        :param args: Positional arguments, recorded as passed - the "bare
            construction" assertions read them to prove there were none.
        :param kwargs: Keyword arguments, recorded as passed.
        :returns: The next session from :attr:`_session_source`.
        """
        self._recorder.record(self._target, *args, **kwargs)

        service = kwargs.get("service")

        if service is not None:
            service.start()

        return self._session_source()


class FakeWebDriverNamespace:
    """Stand-in for the ``webdriver`` name ``driver.py`` binds at import.

    Patching the whole namespace rather than attributes of the real
    ``selenium.webdriver`` module keeps the replacement local to the module
    under test, so no other importer of selenium can see it.

    :param recorder: The shared log.
    :param session_source: Callable producing the session each constructor
        returns.
    """

    __slots__ = ("Chrome", "Firefox")

    def __init__(self, recorder: CallRecorder, session_source: Callable[[], Any]) -> None:
        # Capitalised to match the selenium names the module under test calls.
        self.Chrome = FakeBrowserConstructor(recorder, WEBDRIVER_CHROME, session_source)
        self.Firefox = FakeBrowserConstructor(recorder, WEBDRIVER_FIREFOX, session_source)


class FailingQuitSession:
    """A session whose ``quit()`` always raises, counting its attempts.

    ``conftest.StubDriver`` declares ``__slots__`` and offers no failing-quit
    seam - deliberately, since it counts ``quit()`` rather than judging it - so
    the teardown-failure test needs this local double.  It implements exactly
    the three methods ``driver.py`` calls on a session, and it counts the
    attempt *before* failing it, which is what lets a test assert that a
    failing ``quit()`` was tried exactly once.

    :param error: The exception ``quit()`` raises.
    """

    __slots__ = ("_error", "implicit_waits", "maximize_count", "quit_count")

    def __init__(self, error: BaseException) -> None:
        self._error = error
        #: How many times ``quit()`` was attempted, failure included.
        self.quit_count = 0
        self.maximize_count = 0
        #: Every implicit wait applied, in order.
        self.implicit_waits: list[float] = []

    def maximize_window(self) -> None:
        """Accept the maximize of ``Driver.java:33``.

        :returns: ``None``.
        """
        self.maximize_count += 1

    def implicitly_wait(self, seconds: float) -> None:
        """Accept the implicit wait of ``Driver.java:34``.

        :param seconds: Timeout in seconds, recorded unconverted.
        :returns: ``None``.
        """
        self.implicit_waits.append(seconds)

    def quit(self) -> None:
        """Count the attempt, then fail it.

        :returns: Never returns.
        :raises BaseException: The error this double was built with.
        """
        self.quit_count += 1
        raise self._error


def materialize_driver(
    directory: Path,
    name: str,
    mode: int = TRUSTED_EXECUTABLE_MODE,
) -> Path:
    """Create an empty stand-in for a driver binary and return its path.

    Real file, real mode bits, inside the test's own temporary directory,
    because the module under test verifies the file on disk rather than the
    string it was handed.  Nothing ever executes it.

    :param directory: The directory to create it in; created if absent.
    :param name: The executable's file name.
    :param mode: The mode to set, defaulting to
        :data:`TRUSTED_EXECUTABLE_MODE`.  A refusal test passes a wider one.
    :returns: The path of the created file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"")
    path.chmod(mode)

    return path


class DriverHarness:
    """Every collaborator of ``driver.py``, replaced by a recording double.

    Installed by the :func:`driver_harness` fixture.  Sessions are handed out
    one per browser construction: a queued session when a test has programmed
    one, otherwise a fresh :class:`conftest.StubDriver`.  A new object per
    construction is what makes "the next ``get_driver()`` builds a *new*
    session" assertable by identity.

    The two driver paths the fake managers report are materialized as real,
    verifiable files under ``provisioned_directory``, so the verification the
    module under test applies to a manager result passes here and a test that
    wants it to fail says so explicitly.

    :param provisioned_directory: Directory the fake managers "install" into.
    """

    __slots__ = (
        "cache_root",
        "chrome_binary_path",
        "chrome_manager",
        "chrome_service",
        "firefox_service",
        "gecko_binary_path",
        "gecko_manager",
        "provisioned_directory",
        "queued",
        "recorder",
        "sessions",
        "webdriver",
    )

    def __init__(self, provisioned_directory: Path) -> None:
        self.recorder = CallRecorder()

        #: Sessions a test has programmed, consumed in order.
        self.queued: list[Any] = []

        #: Every session handed out, in construction order.
        self.sessions: list[Any] = []

        #: Where the fake managers report having installed their drivers.
        self.provisioned_directory = provisioned_directory

        #: Where the private provisioning cache root is redirected to.  Inside
        #: the test's own directory and deliberately absent until route two
        #: creates it, so the creation and the verification are what the tests
        #: observe rather than something the fixture arranged.
        self.cache_root = provisioned_directory.parent / "driver-cache"

        self.chrome_binary_path = materialize_driver(provisioned_directory, CHROME_DRIVER_NAME)
        self.gecko_binary_path = materialize_driver(provisioned_directory, GECKO_DRIVER_NAME)

        self.chrome_manager = FakeDriverManager(
            self.recorder,
            CHROME_MANAGER_CONSTRUCT,
            CHROME_MANAGER_INSTALL,
            str(self.chrome_binary_path),
        )
        self.gecko_manager = FakeDriverManager(
            self.recorder,
            GECKO_MANAGER_CONSTRUCT,
            GECKO_MANAGER_INSTALL,
            str(self.gecko_binary_path),
        )
        self.chrome_service = FakeServiceFactory(
            self.recorder,
            CHROME_SERVICE,
            "chrome",
            CHROME_DRIVER_PATH_ENV_KEY,
        )
        self.firefox_service = FakeServiceFactory(
            self.recorder,
            FIREFOX_SERVICE,
            "firefox",
            GECKO_DRIVER_PATH_ENV_KEY,
        )
        self.webdriver = FakeWebDriverNamespace(self.recorder, self._next_session)

    def expose_driver_process(self, process: Any) -> Any:
        """Program the driver process the next service publishes when started.

        The containment capture reads the process from the *service*, because
        that is where it exists at the moment the capture runs - after the
        driver executable has spawned and before the browser it will start
        exists.  A test that wants a real process tree contained therefore
        programs it here and not on the session.

        :param process: The process object to publish, or ``None`` for a
            service that starts none.
        :returns: ``process``, so a test can program and keep it in one line.
        """
        self.chrome_service.process = process
        self.firefox_service.process = process

        return process

    def queue_session(self, session: Any) -> Any:
        """Program the session the next browser construction returns.

        :param session: Any object implementing the three methods
            ``driver.py`` calls - ``maximize_window``, ``implicitly_wait`` and
            ``quit``.
        :returns: ``session``, so a test can queue and keep it in one line.
        """
        self.queued.append(session)
        return session

    def _next_session(self) -> Any:
        """Hand out the next session and remember it.

        :returns: The oldest queued session, or a fresh
            :class:`conftest.StubDriver` when none is queued.
        """
        session = self.queued.pop(0) if self.queued else StubDriver()
        self.sessions.append(session)
        return session


def one_call(recorder: CallRecorder, target: str) -> RecordedCall:
    """Return the single recorded call to ``target``.

    :param recorder: The log to read.
    :param target: The event name that must have happened exactly once.
    :returns: That one call.
    """
    calls = recorder.calls_to(target)
    assert len(calls) == 1, f"expected exactly one {target}, recorded {calls!r}"
    return calls[0]


def one_service(harness: DriverHarness, target: str) -> ServiceDouble:
    """Return the single service object the named factory produced.

    :param harness: The installed harness.
    :param target: :data:`CHROME_SERVICE` or :data:`FIREFOX_SERVICE`.
    :returns: That one service object, so a test can compare by identity.
    """
    factory = harness.chrome_service if target == CHROME_SERVICE else harness.firefox_service

    assert len(factory.produced) == 1, f"expected one {target}, produced {factory.produced!r}"

    return factory.produced[0]


# --------------------------------------------------------------------------
# Reading the source itself
#
# Three obligations of the module under test are properties of its *code*
# rather than of any one call, and no stubbed call can demonstrate them: that
# no third browser name can be compared against, that no third browser can be
# constructed, and that a session which could not be closed is forgotten while
# its failure travels on.  Each is asserted over the syntax tree of
# ``app/automation/driver.py``.
#
# Parsed with :mod:`ast` and never matched with a regular expression.  The
# module under test discusses its absent default branch, both browser names
# and its teardown contract at length in prose, so a text search over the file
# is answered by the documentation and says nothing whatever about the code.
# Parsing also classifies a comparison buried in a nested expression, an
# aliased call and a name that appears only inside a docstring correctly, with
# no pattern to maintain against them.
# --------------------------------------------------------------------------

#: The two browser names the port matches - ``Driver.java:30`` and ``:36``.
#: These are the *only* string literals ``get_driver`` may compare the
#: configured value against; a third member here would be a fallback.
BROWSER_NAMES_IN_SOURCE: Final[frozenset[str]] = frozenset({"chrome", "firefox"})

#: The two browser constructors the module may reference, as attributes of the
#: ``webdriver`` namespace it binds at import - ``Driver.java:32`` and ``:38``.
BROWSER_CONSTRUCTORS_IN_SOURCE: Final[frozenset[str]] = frozenset({"Chrome", "Firefox"})

#: The name the module under test binds the selenium namespace to.
WEBDRIVER_NAMESPACE: Final[str] = "webdriver"

#: The private helper that takes ownership of a freshly constructed session.
#: Every call to it is a session reaching the slot, so where those calls sit is
#: where the slot can be written from.
ADOPT_SESSION: Final[str] = "_adopt_session"

#: The private helper that empties the slot - the port of ``driverPool.remove()``.
CLEAR_SESSION: Final[str] = "_clear_session"

#: The module-private holder itself.  No assignment to an attribute of it may
#: appear in ``get_driver``.
HOLDER_NAME: Final[str] = "_holder"

#: The name whose appearance in an ``except`` clause would let a
#: ``KeyboardInterrupt`` or a worker shutdown be swallowed by teardown.
BASE_EXCEPTION_NAME: Final[str] = "BaseException"

#: The suffixes that identify a driver-binary manager and a selenium service
#: factory by name, used by :func:`is_fallback_call` so that the check does not
#: depend on a hand-maintained list of vendor class names.
MANAGER_SUFFIX: Final[str] = "DriverManager"
SERVICE_SUFFIX: Final[str] = "Service"

#: Logger used once per unrecognised-browser test to prove the log capture is
#: live, so that a negative assertion about records cannot pass vacuously.
PROBE_LOGGER_NAME: Final[str] = f"{DRIVER_LOGGER_PREFIX}.capture_liveness_probe"


class BrowserDispatch(NamedTuple):
    """The three paths of ``get_driver``'s browser dispatch, located in the tree.

    :param chrome_body: The statements of the ``"chrome"`` branch.
    :param firefox_body: The statements of the ``"firefox"`` branch.
    :param unmatched: The statements an unrecognised name falls through to.
        The dispatch has two branches and no ``else:``, so this is empty, and
        the assertions over it are what keep it that way.
    """

    chrome_body: tuple[ast.stmt, ...]
    firefox_body: tuple[ast.stmt, ...]
    unmatched: tuple[ast.stmt, ...]


def function_named(tree: ast.Module, name: str) -> ast.FunctionDef:
    """Return the top-level function definition called ``name``.

    :param tree: The parsed module.
    :param name: The function's name.
    :returns: Its definition node.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node

    pytest.fail(f"{name}() is not defined in the module under test")


def constant_strings(node: ast.AST) -> frozenset[str]:
    """Every string literal appearing anywhere inside one subtree.

    :param node: The subtree to read.
    :returns: The distinct string literal values it contains.
    """
    return frozenset(
        inner.value
        for inner in ast.walk(node)
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
    )


def decision_strings(node: ast.AST) -> frozenset[str]:
    """Every string literal a subtree *compares against*.

    Only literals reached from a comparison count - the operands of a
    ``Compare`` node, and the pattern of a ``match`` case, so a rewrite of the
    dispatch from ``==`` to ``match`` would be read the same way.  A literal in
    a docstring, a log message or a comment is not a decision and is therefore
    not collected, which is the whole reason this is a tree walk rather than a
    text search.

    :param node: The subtree to read.
    :returns: The distinct literals it decides on.
    """
    found: set[str] = set()

    for inner in ast.walk(node):
        if isinstance(inner, ast.Compare):
            for side in (inner.left, *inner.comparators):
                found |= constant_strings(side)
        elif isinstance(inner, ast.MatchValue):
            found |= constant_strings(inner.value)

    return frozenset(found)


def subtree_ids(*nodes: ast.AST) -> frozenset[int]:
    """Identities of every node inside the given subtrees, roots included.

    Membership of this set is what "lexically inside" means below: a node is
    inside a branch, or inside a ``finally``, exactly when its identity appears
    in that region's set.  Identity rather than line number, so a reformatting
    of the module under test cannot change the answer.

    :param nodes: The subtree roots.
    :returns: ``id()`` of every node they contain.
    """
    return frozenset(id(inner) for node in nodes for inner in ast.walk(node))


def annotation_ids(tree: ast.AST) -> frozenset[int]:
    """Identities of every node that appears inside a type annotation.

    The module under test annotates with ``webdriver.Remote | None``, so the
    ``webdriver`` namespace is legitimately named outside a call.  Exempting
    annotations *by position* rather than exempting ``Remote`` by name is what
    keeps :func:`test_only_chrome_and_firefox_are_ever_constructed` honest: a
    constructor call can never be in annotation position, so nothing a
    re-annotation does can create a hole in that test.

    :param tree: The subtree to read.
    :returns: ``id()`` of every node reachable from an annotation.
    """
    annotations: list[ast.AST] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)

    return subtree_ids(*annotations)


def callee_name(call: ast.Call) -> str | None:
    """The simple name being called, for a direct or an attribute call.

    :param call: The call node.
    :returns: ``f`` for ``f(...)``, ``attr`` for ``obj.attr(...)``, and
        ``None`` for anything else - a call of a subscript or of another call.
    """
    func = call.func

    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr

    return None


def calls_named(name: str, *nodes: ast.AST) -> tuple[ast.Call, ...]:
    """Every call to ``name`` anywhere inside the given subtrees.

    :param name: The callee name to look for.
    :param nodes: The subtrees to search.
    :returns: The matching call nodes, in tree order.
    """
    return tuple(
        inner
        for node in nodes
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and callee_name(inner) == name
    )


def is_fallback_call(call: ast.Call) -> bool:
    """Whether one call would turn the unmatched browser path into a fallback.

    Four kinds of call are disqualifying, and between them they cover every
    fallback AAP 0.4.1 forbids: constructing any browser through the
    ``webdriver`` namespace, constructing one through a directly imported
    constructor, provisioning a driver binary through any ``*DriverManager``,
    building any ``*Service`` for one, and adopting a session.  Named by shape
    rather than by a list of vendor class names, so a manager or a constructor
    this suite has never heard of is caught too.

    :param call: The call node.
    :returns: ``True`` when the call constructs, provisions or adopts.
    """
    func = call.func

    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == WEBDRIVER_NAMESPACE
    ):
        return True

    name = callee_name(call)

    if name is None:
        return False

    return (
        name == ADOPT_SESSION
        or name in BROWSER_CONSTRUCTORS_IN_SOURCE
        or name.endswith(MANAGER_SUFFIX)
        or name.endswith(SERVICE_SUFFIX)
    )


def browser_dispatch(function: ast.FunctionDef) -> BrowserDispatch:
    """Locate the two browser branches and the path an unmatched name takes.

    A branch is identified by what it *decides on* rather than by its position,
    so the chain may be written as ``if``/``elif`` or as two separate ``if``
    statements and this still finds it.  The unmatched path is what the chain
    falls through to: the ``else`` of its last link, plus anything the first
    link's ``else`` holds besides the link itself.  The dispatch carries no
    ``else``, so that path comes back empty; collecting it anyway is what lets
    the assertions over it fail the moment one appears.

    :param function: The ``get_driver`` definition.
    :returns: The three paths.
    """
    chrome_if: ast.If | None = None
    firefox_if: ast.If | None = None

    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue

        decided = decision_strings(node.test)

        if decided == frozenset({"chrome"}):
            chrome_if = node
        elif decided == frozenset({"firefox"}):
            firefox_if = node

    if chrome_if is None or firefox_if is None:
        pytest.fail(
            "get_driver() must dispatch on 'chrome' and on 'firefox', one "
            "branch each; no such pair of branches was found in the tree"
        )

    unmatched = [statement for statement in chrome_if.orelse if statement is not firefox_if]
    unmatched.extend(firefox_if.orelse)

    return BrowserDispatch(
        chrome_body=tuple(chrome_if.body),
        firefox_body=tuple(firefox_if.body),
        unmatched=tuple(unmatched),
    )


def except_handlers(function: ast.FunctionDef) -> tuple[ast.ExceptHandler, ...]:
    """Every ``except`` clause inside one function.

    :param function: The definition to read.
    :returns: The handler nodes, in tree order.
    """
    return tuple(node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler))


def caught_exception_names(handler: ast.ExceptHandler) -> frozenset[str]:
    """The exception names one handler catches.

    Tuples are flattened and dotted names are reduced to their final
    attribute, so ``except (ValueError, builtins.BaseException)`` reports both.

    :param handler: The handler node.
    :returns: The names it catches; empty for a bare ``except:``.
    """
    if handler.type is None:
        return frozenset()

    names: set[str] = set()

    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    return frozenset(names)


def carries_exception_information(call: ast.Call) -> bool:
    """Whether one logging call reports the exception rather than only a message.

    Two shapes carry it: ``logger.exception(...)``, which attaches the active
    exception by definition, and any call passing ``exc_info``.  An explicit
    ``exc_info=False`` or ``exc_info=None`` carries nothing and is rejected, so
    the disabling form cannot satisfy the check the enabling form exists for.

    :param call: The call node.
    :returns: ``True`` when the call reports the exception.
    """
    if callee_name(call) == "exception":
        return True

    for keyword in call.keywords:
        if keyword.arg != "exc_info":
            continue

        disabled = isinstance(keyword.value, ast.Constant) and not keyword.value.value

        if not disabled:
            return True

    return False


def handler_reports_failure(handler: ast.ExceptHandler) -> bool:
    """Whether a handler's own body reports what it caught.

    Read over the handler's body and nothing else, because the question is what
    *this* handler does: a ``raise`` elsewhere in the file, or a logging call in
    a neighbouring function, says nothing about it.  Three shapes report - a
    re-raise, a log call carrying the exception, or a return value describing
    the failure - and a handler that only ``pass``es, or that logs a message
    without the exception, reports nothing and fails this.

    ``quit_driver`` catches nothing at all, which is the contract: a failing
    ``quit()`` propagates.  This helper exists so that the assertion stays
    meaningful if a handler is ever added - it would have to report - rather
    than being satisfied by the absence of one alone.

    :param handler: The handler node.
    :returns: ``True`` when the failure leaves the handler visible.
    """
    for statement in handler.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Raise):
                return True

            if isinstance(node, ast.Return) and not (
                node.value is None
                or (isinstance(node.value, ast.Constant) and node.value.value is None)
            ):
                return True

            if isinstance(node, ast.Call) and carries_exception_information(node):
                return True

    return False


class AutomationLogCapture(logging.Handler):
    """Every record emitted anywhere under the ``app.automation`` logger.

    ``caplog`` observes records that reach the root logger, and
    ``app/logging_config.py``'s ``configure_logging()`` sets
    ``propagate = False`` on the ``app`` logger - so in a full-suite run, where
    another module may have configured logging, a root-level capture can miss a
    record that was genuinely emitted.  A handler installed directly on
    ``app.automation`` cannot: records from ``app.automation.driver`` reach it
    before any ancestor's propagation flag is consulted.  That matters most for
    the negative assertion, where a capture that silently sees nothing would
    look exactly like a module that emitted nothing.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)

        #: Every record seen, in emission order.
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record.

        :param record: The record being emitted.
        :returns: ``None``.
        """
        self.records.append(record)

    def at_or_above(self, level: int) -> tuple[logging.LogRecord, ...]:
        """Every captured record at or above one level.

        :param level: The threshold, as a :mod:`logging` level number.
        :returns: The matching records, in emission order.
        """
        return tuple(record for record in self.records if record.levelno >= level)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def driver_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DriverHarness:
    """Install a :class:`DriverHarness` over every seam of ``driver.py``.

    Patched **on the module under test**, which is the only patch point that
    works for names bound at import time, and the only one that leaves other
    importers of selenium untouched.  ``monkeypatch.setattr`` is used in its
    default raising mode on purpose: if the module under test stops binding one
    of these six names, this fixture fails loudly instead of quietly leaving a
    real manager, a real browser constructor or the real system search in
    place.

    The system driver directories are emptied, so the tests that exercise the
    dispatch reach the manager route deterministically on any host - a host
    that happens to have a real ``chromedriver`` installed would otherwise take
    the pre-provisioned route and construct no manager at all.  The tests that
    own the pre-provisioned route install a directory of their own through
    :func:`trusted_driver_directory`.

    The private provisioning cache root is redirected into this test's own
    directory for the same reason: route two creates and verifies that root,
    and a suite that used the real one would write into the host's temporary
    directory - a location shared with every other process on the machine -
    and would carry whatever a previous run or a parallel one left there into
    its own result.  The redirected path does not exist when the fixture
    returns: creating it is the behaviour under test.

    After this fixture runs, nothing reachable from ``get_driver()`` can start
    a browser, download a binary, open a socket or write outside ``tmp_path``.

    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :param tmp_path: The test's own directory, where the driver files the fake
        managers report are materialized.
    :returns: The installed harness.
    """
    harness = DriverHarness(tmp_path / "provisioned")

    monkeypatch.setattr(driver_module, "ChromeDriverManager", harness.chrome_manager)
    monkeypatch.setattr(driver_module, "GeckoDriverManager", harness.gecko_manager)
    monkeypatch.setattr(driver_module, "ChromeService", harness.chrome_service)
    monkeypatch.setattr(driver_module, "FirefoxService", harness.firefox_service)
    monkeypatch.setattr(driver_module, "webdriver", harness.webdriver)
    monkeypatch.setattr(driver_module, "_SYSTEM_DRIVER_DIRECTORIES", ())
    monkeypatch.setattr(
        driver_module,
        "_private_cache_root_path",
        lambda: str(harness.cache_root),
    )

    return harness


@pytest.fixture
def trusted_driver_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[..., Path]:
    """Yield an installer for the pre-provisioned driver search - route one.

    The returned callable materializes one driver executable in a directory of
    this test's own and makes that directory the whole of
    ``_SYSTEM_DRIVER_DIRECTORIES``, so what the module under test searches is
    exactly what the test put there.  Patching that module constant is the seam
    ``driver.py`` documents for this; it is not a configuration point, and no
    production path can choose the directories.

    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :param tmp_path: The test's own directory.
    :returns: A callable taking the executable's file name and its mode, and
        returning the path it created.
    """
    directory = tmp_path / "system"

    def install(name: str, mode: int = TRUSTED_EXECUTABLE_MODE) -> Path:
        path = materialize_driver(directory, name, mode)
        monkeypatch.setattr(driver_module, "_SYSTEM_DRIVER_DIRECTORIES", (str(directory),))

        return path

    return install


@pytest.fixture
def browser_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str | None], None]]:
    """Yield a setter for the ``browser`` configuration key.

    A configured value is installed through the public API
    ``app.config.set_userdata``, the mechanism ``tests/conftest.py`` documents:
    userdata takes precedence over the properties file, and membership decides,
    so ``""`` is installed as an empty value rather than falling through to the
    file.  The cleanup below runs in a ``finally``, so a failing assertion
    cannot leak userdata into a later test - the same guarantee conftest's
    "register the finalizer before installing" note asks for.

    An **unset** key is arranged by patching ``get_browser`` on the module under
    test instead.  That is deliberate: clearing userdata would fall through to
    ``configuration.properties`` in the process working directory, and whether
    that file exists is not something this suite may depend on.

    :param monkeypatch: pytest's patcher, used for the unset case.
    :yields: A callable taking the browser value, or ``None`` for an unset key.
    """

    def install(value: str | None) -> None:
        if value is None:
            monkeypatch.setattr(driver_module, "get_browser", lambda: None)
            return

        config.set_userdata({"browser": value})

    try:
        yield install
    finally:
        # ``None`` clears the slot and restores the file-only path, which is
        # the whole of the cleanup app/config.py requires.
        config.set_userdata(None)


@pytest.fixture(scope="module")
def driver_source() -> ast.Module:
    """The parsed syntax tree of ``app/automation/driver.py``.

    Parsed from ``driver_module.__file__`` rather than from a path spelled out
    here, so the source-level assertions cannot drift onto a stale copy of the
    file: they read the very module the behavioural tests import.

    Module-scoped because a syntax tree is an immutable derived value - no test
    below mutates it, so there is nothing one test could hand to the next, and
    the file is read once instead of once per assertion.

    :returns: The module's AST.
    """
    source_path = Path(driver_module.__file__)

    return ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))


@pytest.fixture
def automation_log() -> Iterator[AutomationLogCapture]:
    """Capture every record emitted under the ``app.automation`` logger.

    The handler is installed on ``app.automation`` itself and the logger's level
    is lowered to DEBUG for the duration, so nothing a record's own level or an
    ancestor's propagation flag does can hide it - see
    :class:`AutomationLogCapture` for why a root-level capture is not enough
    here.  Both the level and the handler are restored in a ``finally``, so a
    failing assertion cannot leave the logger reconfigured for a later test.

    :yields: The installed capture.
    """
    automation_logger = logging.getLogger(DRIVER_LOGGER_PREFIX)
    capture = AutomationLogCapture()
    previous_level = automation_logger.level

    automation_logger.addHandler(capture)
    automation_logger.setLevel(logging.DEBUG)

    try:
        yield capture
    finally:
        automation_logger.setLevel(previous_level)
        automation_logger.removeHandler(capture)


# --------------------------------------------------------------------------
# Creation, reuse and the worker-local slot
# --------------------------------------------------------------------------


def test_session_is_created_on_demand_and_then_reused(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin create-on-demand and reuse - ``Driver.java:22`` and ``:45``.

    The first call constructs exactly one session; the second returns the
    identical object, having provisioned nothing and constructed nothing
    further, which is what the Java comment means by "will return same driver
    instance when we call it".  The log is asserted whole after the second
    call, so any extra event - a second install, a second constructor call -
    fails here.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    assert first is not None
    assert driver_harness.sessions == [first]
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE

    second = driver_module.get_driver()

    assert second is first
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 1
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 1
    assert driver_harness.sessions == [first]


def test_the_session_lives_in_the_workers_own_slot(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin one slot per worker - ``Driver.java:17``'s ``InheritableThreadLocal``.

    ``driver.py`` states that a plain module global is deliberately not used.
    A second thread therefore starts from an empty slot and builds its own
    session, which is exactly the storage semantics of the Java field and the
    reason AAP 0.3.3's "exactly one live session exists per worker" holds
    whatever else shares the process.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    assert isinstance(driver_module._holder, threading.local)
    assert driver_module._session() is first

    from_other_thread: list[Any] = []
    thread = threading.Thread(
        target=lambda: from_other_thread.append(driver_module.get_driver()),
        name="driver-slot-probe",
    )
    thread.start()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert len(from_other_thread) == 1
    assert from_other_thread[0] is not first
    assert driver_module._session() is first
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 2


def test_new_session_is_maximized_then_given_a_ten_second_implicit_wait(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin the set-up order - ``Driver.java:33`` before ``:34``.

    Two things at once: the window is maximized *before* the implicit wait is
    applied, and the wait receives exactly ``10``.  The Python binding takes
    seconds, so a millisecond literal - ``10000``, as a Java-to-Python
    transcription might produce - fails the second assertion.
    """
    browser_key("chrome")
    driver_harness.queue_session(stub_driver)

    session = driver_module.get_driver()

    assert session is stub_driver
    assert stub_driver.operations() == SETUP_OPERATIONS
    assert stub_driver.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)


# --------------------------------------------------------------------------
# The two branches of the switch
# --------------------------------------------------------------------------


def test_chrome_branch_provisions_before_constructing_bare(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin the Chrome branch - ``Driver.java:30-35``.

    The full event sequence establishes the ordering the Selenium 3-to-4 note
    in ``driver.py`` describes: the manager installs the binary, the returned
    path is handed to ``ChromeService``, and that service object - by identity,
    not by shape - reaches ``webdriver.Chrome``.  Provisioning happens strictly
    before construction.

    The browser is constructed **bare**: no positional argument and no keyword
    other than ``service``.  ``Driver.java:32`` passes no capability object,
    and AAP 0.6 reserves the browser-locale question, so a capabilities or
    options argument appearing here would silently settle it.
    """
    browser_key("chrome")

    session = driver_module.get_driver()

    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE

    service_call = one_call(driver_harness.recorder, CHROME_SERVICE)
    assert str(driver_harness.chrome_binary_path) in service_call.values()

    assert len(driver_harness.chrome_service.produced) == 1
    service = driver_harness.chrome_service.produced[0]

    browser_call = one_call(driver_harness.recorder, WEBDRIVER_CHROME)
    assert browser_call.args == ()
    assert set(browser_call.kwargs) == {"service"}
    assert browser_call.kwargs["service"] is service

    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.count_of(GECKO_MANAGER_CONSTRUCT) == 0
    assert driver_harness.recorder.count_of(GECKO_MANAGER_INSTALL) == 0
    assert driver_harness.recorder.count_of(WEBDRIVER_FIREFOX) == 0


def test_firefox_branch_provisions_with_gecko_and_never_with_chrome(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin AAP deviation 5 / Conflict 6 - the corrected defect.

    ``Driver.java:37`` calls ``WebDriverManager.chromedriver().setup()`` inside
    the *Firefox* branch, immediately before constructing a ``FirefoxDriver``.
    AAP Conflict 6 corrects it because reproducing it would provision the wrong
    binary, and AAP 0.8 requires the correction to stay individually
    reversible.  The Chrome manager's call counts are asserted to be **zero**
    explicitly: a test that merely checked that Gecko was used would pass while
    the source's defect was reintroduced alongside it.

    As in the Chrome branch, the browser is constructed bare and the
    provisioned path travels through the ``Service`` object by identity.
    """
    browser_key("firefox")

    session = driver_module.get_driver()

    assert driver_harness.recorder.targets() == FIREFOX_BRANCH_SEQUENCE

    service_call = one_call(driver_harness.recorder, FIREFOX_SERVICE)
    assert str(driver_harness.gecko_binary_path) in service_call.values()

    assert len(driver_harness.firefox_service.produced) == 1
    service = driver_harness.firefox_service.produced[0]

    browser_call = one_call(driver_harness.recorder, WEBDRIVER_FIREFOX)
    assert browser_call.args == ()
    assert set(browser_call.kwargs) == {"service"}
    assert browser_call.kwargs["service"] is service

    assert session is driver_harness.sessions[0]

    # The whole point of deviation 5: the Chrome binary is never provisioned
    # while constructing Firefox.
    assert driver_harness.recorder.count_of(CHROME_MANAGER_CONSTRUCT) == 0
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 0
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 0


def test_firefox_session_is_maximized_then_given_the_same_implicit_wait(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin the set-up order on the second branch - ``Driver.java:39-40``.

    The Java branches apply identical treatment by copy-paste, so the port
    factors it into one function.  Asserting it on Firefox as well as on Chrome
    is what keeps that factoring honest: a branch that skipped the shared tail
    would pass the Chrome test alone.
    """
    browser_key("firefox")
    driver_harness.queue_session(stub_driver)

    session = driver_module.get_driver()

    assert session is stub_driver
    assert stub_driver.operations() == SETUP_OPERATIONS
    assert stub_driver.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)


# --------------------------------------------------------------------------
# No default branch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("browser", UNRECOGNISED_BROWSERS)
def test_unrecognised_browser_yields_no_session_and_no_side_effect(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    browser: str | None,
) -> None:
    """Pin the absent ``default:`` label - ``Driver.java:29-42``.

    ``"Chrome"``, ``"CHROME"`` and ``" chrome"`` are the case sensitivity and
    the absent trimming of a Java ``switch`` over a ``String``, which compares
    with ``equals``; ``"safari"`` and ``"edge"`` are values the source never
    handled; ``""`` is an installed empty value, which userdata membership
    resolves rather than treating as unset; ``None`` is the key being unset,
    which ``app/config.py`` returns unchanged.

    Each yields ``None`` with nothing provisioned, nothing constructed, nothing
    raised and an empty slot - AAP 0.4.1: *"Any other value fails at first
    driver use, as today."*  "Nothing raised" includes
    ``DriverProvisioningError``: verification belongs to a branch that matched,
    and an unrecognised value never reaches one.
    """
    browser_key(browser)

    session = driver_module.get_driver()

    assert session is None
    assert driver_module._session() is None
    assert driver_harness.recorder.calls == []
    assert driver_harness.sessions == []


def test_unrecognised_browser_leaves_a_later_call_free_to_try_again(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin the absence of an "already failed" state - ``Driver.java:22``.

    The create-on-demand guard is only "the slot is empty", and the Java method
    keeps no record of a previous miss, so a call after an unrecognised browser
    name tries again in full.  This is what makes a mid-run correction of the
    ``browser`` value take effect rather than being permanently poisoned by the
    first attempt.
    """
    browser_key("safari")

    assert driver_module.get_driver() is None
    assert driver_harness.recorder.calls == []

    browser_key("chrome")

    session = driver_module.get_driver()

    assert session is not None
    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE


@pytest.mark.parametrize("browser", UNRECOGNISED_BROWSERS)
def test_unrecognised_browser_emits_nothing_at_warning_or_above(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    automation_log: AutomationLogCapture,
    browser: str | None,
) -> None:
    """Pin what the unmatched path may *say* - ``Driver.java:29-42``.

    An unrecognised browser is not a warning: nothing was substituted, nothing
    was retried and nothing is being deprecated, so a record at WARNING or
    above on the ``app.automation`` tree would tell an operator the exact
    opposite of what happened - AAP 0.4.1's ruling is that the value simply
    "fails at first driver use, as today".  This is also what keeps the port
    honest about the branch's purpose: a fallback that announced itself as a
    warning would read as sanctioned behaviour rather than as the defect it is.

    The path emits **no** record at all, at any level, and that is what is
    asserted: the dispatch has two branches and nothing else, so there is no
    statement on the unmatched path to emit one from.  A record naming the
    configured value would be a behaviour this port added on its own, so its
    absence is the contract rather than a detail below the contract.

    The liveness probe at the end is what makes the negative assertion worth
    something: it proves the capture would have seen a WARNING had one been
    emitted, so a broken capture fails this test instead of passing it.
    """
    browser_key(browser)

    assert driver_module.get_driver() is None

    emitted = tuple(automation_log.records)

    logging.getLogger(PROBE_LOGGER_NAME).warning("capture liveness probe")

    probed = automation_log.at_or_above(logging.WARNING)

    assert emitted == ()
    assert len(probed) == 1
    assert driver_harness.recorder.calls == []


# --------------------------------------------------------------------------
# No fallback, asserted over the source
#
# The four tests below are the substance of "no default branch": not the
# absence of a log line, but the absence of any fallback.  Together they forbid
# every third browser name, every third constructor, every session written from
# outside the two branches, and every substitution or retry on the path an
# unmatched name takes - a path which, in the landed dispatch, holds no
# statement at all.
# --------------------------------------------------------------------------


def test_get_driver_compares_the_browser_value_against_only_two_names(
    driver_source: ast.Module,
) -> None:
    """Pin the two case labels of ``Driver.java:29-42`` - and only those two.

    Every string literal ``get_driver`` decides on is collected from the tree,
    and the set has to be exactly ``"chrome"`` and ``"firefox"``.  A third
    browser name, an alias, or the target of a normalisation - a lowercased
    ``"CHROME"``, say - each adds a member here, and each is a fallback: AAP
    0.4.1 fixes the behaviour of every other value as failing at first driver
    use, and ``driver.py`` states that normalizing "would make this port accept
    input the source rejects".

    Literals are counted only where they are compared, so the module's prose
    and its log message - which quotes both names - are correctly ignored.
    """
    get_driver = function_named(driver_source, "get_driver")

    assert decision_strings(get_driver) == BROWSER_NAMES_IN_SOURCE


def test_only_chrome_and_firefox_are_ever_constructed(driver_source: ast.Module) -> None:
    """Pin the two browsers of ``Driver.java:29-41`` at module scope.

    Exactly two constructors are reachable in the whole file, and they are
    ``webdriver.Chrome`` and ``webdriver.Firefox``.  ``driver.py``'s "No third
    browser" note is the rule being enforced: the specification mentions
    Internet Explorer, AAP 0.2.2 puts it out of scope, and "Add no branch for
    it" is only meaningful if no other constructor exists anywhere - including
    in a helper that the dispatch could later call.

    The one legitimate mention of the namespace outside a call is the
    ``webdriver.Remote | None`` annotation, and it is exempted **by position**:
    every non-call reference must sit inside an annotation, where no browser can
    be constructed.  Exempting the name ``Remote`` instead would leave a hole
    the moment the annotations changed.
    """
    call_func_ids = frozenset(
        id(node.func) for node in ast.walk(driver_source) if isinstance(node, ast.Call)
    )
    annotated = annotation_ids(driver_source)
    constructed: set[str] = set()
    outside_annotations: list[tuple[str, int]] = []

    for node in ast.walk(driver_source):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id != WEBDRIVER_NAMESPACE:
            continue

        if id(node) in call_func_ids:
            constructed.add(node.attr)
        elif id(node) not in annotated:
            outside_annotations.append((node.attr, node.lineno))

    assert constructed == BROWSER_CONSTRUCTORS_IN_SOURCE
    assert outside_annotations == []


def test_a_session_is_adopted_only_inside_the_two_branches(driver_source: ast.Module) -> None:
    """Pin where the slot may be written from - ``Driver.java:32`` and ``:38``.

    The Java method stores a session with ``driverPool.set(...)`` from inside
    each of its two cases and nowhere else.  Here, adoption is
    ``_adopt_session`` - the one function that writes the holder - so "the slot
    is written only from the two branches" is exactly: one adoption in the
    ``"chrome"`` branch, one in the ``"firefox"`` branch, and none anywhere
    else in ``get_driver``.

    The second half closes the way around that helper: no ``setattr`` call and
    no assignment to an attribute of the holder may appear in ``get_driver``
    outside those two branches either, so a fallback cannot install a session
    by reaching past the helper that the first half constrains.
    """
    get_driver = function_named(driver_source, "get_driver")
    dispatch = browser_dispatch(get_driver)
    matched = subtree_ids(*dispatch.chrome_body) | subtree_ids(*dispatch.firefox_body)

    adoptions = calls_named(ADOPT_SESSION, get_driver)

    assert len(calls_named(ADOPT_SESSION, *dispatch.chrome_body)) == 1
    assert len(calls_named(ADOPT_SESSION, *dispatch.firefox_body)) == 1
    assert len(adoptions) == 2
    assert [call.lineno for call in adoptions if id(call) not in matched] == []

    stray_setattr = [
        call.lineno for call in calls_named("setattr", get_driver) if id(call) not in matched
    ]
    holder_writes = [
        node.lineno
        for node in ast.walk(get_driver)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == HOLDER_NAME
        and not isinstance(node.ctx, ast.Load)
        and id(node) not in matched
    ]

    assert stray_setattr == []
    assert holder_writes == []


def test_the_unmatched_browser_path_is_not_a_fallback(driver_source: ast.Module) -> None:
    """Pin what the unmatched path may not do - ``Driver.java:29-42``.

    Whatever that path may contain, it constructs no browser, provisions no
    driver binary, adopts no session, raises nothing and substitutes no browser
    name.  ``driver.py`` states the same thing in words - "There is no fallback
    browser and no substitution" - and AAP 0.4.1 requires the value to fail at
    first *use* rather than here, which a ``raise`` from this path would
    change.

    "Substitutes no browser name" is asserted structurally, twice over: the
    path binds no name at all, so the browser value cannot be replaced and no
    computed name can reach a constructor, and no literal on it *equals* a
    browser name.  Equality and never containment, so that a sentence
    mentioning either name would be read as the prose it is rather than as a
    substitution.

    The landed dispatch holds no statement on that path, so every assertion
    here reads over an empty collection - which is the strongest form of the
    contract, not a weaker one: a path that does not exist cannot fall back,
    and the moment a statement appears these assertions apply to it.
    """
    get_driver = function_named(driver_source, "get_driver")
    dispatch = browser_dispatch(get_driver)

    fallback_calls: list[tuple[str | None, int]] = []
    raises: list[int] = []
    bindings: list[int] = []
    literals: set[str] = set()

    for statement in dispatch.unmatched:
        literals |= constant_strings(statement)

        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and is_fallback_call(node):
                fallback_calls.append((callee_name(node), node.lineno))
            elif isinstance(node, ast.Raise):
                raises.append(node.lineno)
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
                bindings.append(node.lineno)

    assert fallback_calls == []
    assert raises == []
    assert bindings == []
    assert literals & BROWSER_NAMES_IN_SOURCE == set()


# --------------------------------------------------------------------------
# Ownership before settings, and teardown
# --------------------------------------------------------------------------


def test_session_is_owned_before_its_settings_are_applied(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin slot-before-settings - ``Driver.java:32`` before ``:33``.

    Java stores the session with ``driverPool.set(...)`` and only then
    dereferences ``driverPool.get().manage()``, and ``driver.py`` states the
    consequence as a guarantee: if ``maximize_window()`` raises, the session is
    already this worker's, so :func:`quit_driver` can still close the browser
    process instead of leaking it.  The failure propagates - nothing here
    swallows it - and the implicit wait is never reached.
    """
    browser_key("chrome")
    stub_driver.maximize_error = RuntimeError("maximize refused by the window manager")
    driver_harness.queue_session(stub_driver)

    with pytest.raises(RuntimeError, match="maximize refused"):
        driver_module.get_driver()

    # Ownership, despite the failure: the slot holds the session, so the
    # browser process is reachable rather than orphaned.
    assert driver_module._session() is stub_driver
    assert stub_driver.operations() == ("maximize_window",)

    driver_module.quit_driver()

    assert stub_driver.quit_count == 1
    assert driver_module._session() is None


def test_quit_driver_on_an_empty_slot_does_nothing(driver_harness: DriverHarness) -> None:
    """Pin the teardown guard - ``Driver.java:51``.

    With an empty slot this is a no-op: nothing is called and nothing is
    raised, so a scenario that never built a session - one whose configured
    browser matched neither branch - tears down as quietly as one that did.
    Called twice, because ``after_scenario`` runs for every scenario and a
    second guarded call must be as quiet as the first.
    """
    assert driver_module._session() is None

    driver_module.quit_driver()
    driver_module.quit_driver()

    assert driver_module._session() is None
    assert driver_harness.recorder.calls == []
    assert driver_harness.sessions == []


def test_quit_driver_quits_once_and_clears_the_slot(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin quit-then-clear - ``Driver.java:52-53``.

    ``quit()`` is called exactly once and the slot is emptied, so the next
    ``get_driver()`` builds a genuinely new session rather than handing back
    the quit one.  That is the half of AAP 0.3.3's contract which guarantees
    "every scenario gets a fresh session, and no code ever touches a driver
    after ``quit()``", and identity is what proves it: the second session is a
    different object, freshly provisioned and freshly configured.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    driver_module.quit_driver()

    assert first.quit_count == 1
    assert driver_module._session() is None

    second = driver_module.get_driver()

    assert second is not first
    assert second is driver_harness.sessions[1]
    assert first.quit_count == 1
    assert second.quit_count == 0
    assert second.operations() == SETUP_OPERATIONS
    assert second.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 2
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 2


def test_a_failing_quit_is_not_converted_into_success(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    automation_log: AutomationLogCapture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the teardown-failure contract: propagate, and clear the slot anyway.

    ``quit_driver`` offers no "never raises" guarantee.
    ``Driver.closeDriver()`` installs no handler, AAP 0.1.3's deviation 19
    sanctions suppression for screenshot capture alone, and a browser that
    could not be closed may still be running with an authenticated session in
    it - so the failure reaches the caller, exactly as it stands, while the
    slot is emptied in a ``finally``.  Four things are pinned here:

    * the very exception the session raised propagates out of
      :func:`app.automation.driver.quit_driver` - by identity, so a
      re-wrapped or substituted error fails this;
    * ``quit()`` was attempted exactly once - not retried, not skipped;
    * the slot is empty afterwards, so a session that could not be closed can
      never be handed to a later scenario;
    * the failure was **not** turned into a warning-and-return.  Log capture is
      live for the ``app`` tree and for ``app.automation`` directly, and no
      record is permitted from the module under test: a teardown that logged
      and returned would report success to ``after_scenario``, which is the
      conversion this test exists to reject.

    In production the caller is ``after_scenario``, where behave 1.3.3's
    ``runner.run_hook`` turns this into ``HOOK-ERROR in after_scenario: ...``
    with the scenario's status set to ``hook_error`` and the run continuing -
    visible, which a suppressed failure would not be.
    """
    browser_key("chrome")
    failure = RuntimeError("session is gone")
    session = driver_harness.queue_session(FailingQuitSession(failure))

    assert driver_module.get_driver() is session

    # The double is a session like any other: it received the same set-up, so
    # what follows is about the teardown alone.
    assert session.maximize_count == 1
    assert session.implicit_waits == [IMPLICIT_WAIT_SECONDS]

    with (
        caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME),
        pytest.raises(RuntimeError) as raised,
    ):
        driver_module.quit_driver()

    # Identity, not type or message: the exception the session raised is the
    # exception the caller receives, unwrapped and unreplaced.
    assert raised.value is failure

    # Both capture sources, so that a record emitted below a logger whose
    # propagation another module switched off is still seen - see
    # :class:`AutomationLogCapture`.  Nothing from the module under test may
    # appear in either: reporting by logging is the suppressed form.
    logged = tuple(
        record
        for record in (*caplog.records, *automation_log.records)
        if record.name.startswith(DRIVER_LOGGER_PREFIX)
    )

    assert logged == ()
    assert session.quit_count == 1
    assert driver_module._session() is None

    # And the slot is genuinely reusable afterwards: the failure emptied it
    # rather than poisoning this worker with a session that would not close.
    assert driver_module.get_driver() is driver_harness.sessions[1]


# --------------------------------------------------------------------------
# No silent swallow, asserted over the source
#
# Propagation itself is pinned behaviourally, by identity, in the test above.
# The three tests below add what a single call cannot show: that the slot is
# forgotten on every path, that teardown cannot swallow a shutdown, and that
# any handler present reports what it caught.  ``quit_driver`` has no handler,
# so the last two read over an empty collection - which is the contract, since
# with nothing catching it the failure propagates, and a handler added later
# immediately has both to satisfy.
# --------------------------------------------------------------------------


def test_quit_driver_clears_the_slot_from_inside_a_finally(driver_source: ast.Module) -> None:
    """Pin the clear as unconditional - ``Driver.java:53`` and AAP 0.3.3.

    ``driver.py`` states the requirement as a consequence of the lifecycle
    contract: "A session that could not be closed must never be handed to a
    later scenario, which is what the contract's 'no code ever touches a driver
    after ``quit()``' forbids, so the clear happens in a ``finally``."  A clear
    placed after the ``try`` instead is skipped on exactly the path that needs
    it most - a ``quit()`` that failed - leaving a dead session in the slot for
    the next scenario to be handed.

    Every call that clears the slot is required to be lexically inside a
    ``finally``, not merely one of them, so a second clear added on the happy
    path cannot satisfy this while the failing path stays uncovered.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    final_statements = [
        statement
        for node in ast.walk(quit_driver)
        if isinstance(node, ast.Try | ast.TryStar)
        for statement in node.finalbody
    ]
    inside_finally = subtree_ids(*final_statements)

    clears = calls_named(CLEAR_SESSION, quit_driver)

    assert len(clears) >= 1
    assert [call.lineno for call in clears if id(call) not in inside_finally] == []


def test_quit_driver_catches_neither_everything_nor_base_exception(
    driver_source: ast.Module,
) -> None:
    """Pin what teardown may not catch - the run has to stay interruptible.

    A bare ``except:``, or one naming ``BaseException``, would let teardown
    absorb the signal that is trying to stop the run - a worker that refuses to
    die, which is a far worse failure than the browser-cleanup nuisance such a
    handler would exist for.

    Asserted over every handler in the function.  ``quit_driver`` states its
    position as "No ``except`` clause of any kind", so there is none to inspect
    and the failure propagates; the assertion is what keeps a later handler
    from being either of the two forbidden shapes.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    handlers = except_handlers(quit_driver)

    bare = [handler.lineno for handler in handlers if handler.type is None]
    too_broad = [
        handler.lineno
        for handler in handlers
        if BASE_EXCEPTION_NAME in caught_exception_names(handler)
    ]

    assert bare == []
    assert too_broad == []


def test_quit_driver_reports_every_failure_it_catches(driver_source: ast.Module) -> None:
    """Pin the absence of a silent swallow in teardown.

    A browser process can remain alive while teardown reports success, so a
    handler that swallows the failure - one whose body only ``pass``es, or that
    logs a message without the exception - is forbidden.  A handler that
    re-raises, logs with the exception attached (``exc_info=...`` or
    ``logger.exception``) or returns a value describing the failure is not.

    Judged from each handler's own body, never from a text search over the
    file: a ``raise`` in a neighbouring function, and the module's prose about
    its teardown contract, would both answer such a search while saying nothing
    about what a handler does with what it caught.  ``quit_driver`` catches
    nothing, which is propagation - the strictest form of this - and the
    assertion holds a later handler to the rule.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    handlers = except_handlers(quit_driver)

    silent = [handler.lineno for handler in handlers if not handler_reports_failure(handler)]

    assert silent == []


# --------------------------------------------------------------------------
# Driver provisioning: one verification gate, route one first, and no
# ambient state deciding what gets executed
#
# Whatever path reaches a ``Service`` is executed by selenium as a subprocess
# with this worker's privileges, so the tests below are about *which* path
# that is.  Each one is reachable with stubs alone: the driver executables are
# empty files this test created, the browser constructors are doubles, and the
# only subprocess the module under test would ever start - a browser
# ``--version`` probe - is replaced by a recorder.
# --------------------------------------------------------------------------


class RefusalCase(NamedTuple):
    """One untrustworthy driver candidate, and the refusal it must produce.

    :param case_id: Short identifier, used as the parametrization id.
    :param build: Callable taking the test's directory and returning the path
        to offer the module under test.
    :param reason: The reason code the refusal message has to name.
    :param posix_only: Whether the case can be *expressed* on Windows at all.
        Five of them cannot: a symbolic link needs a privilege there, a FIFO
        has no equivalent, ``chmod`` cannot make a file group- or
        world-writable, and ``os.access(path, os.X_OK)`` answers ``True`` for
        every existing file - so no regular file is non-executable.  Those five
        are also exactly the requirements the module under test applies on
        POSIX alone, which is the same fact seen from the other side.
    """

    case_id: str
    build: Callable[[Path], str]
    reason: str
    posix_only: bool = False


def _build_symlink(directory: Path) -> str:
    """A symbolic link pointing at a perfectly good executable.

    Refused because the link's target can be replaced after the check, which
    is why the module under test inspects with ``os.lstat`` and never follows.

    :param directory: The test's own directory.
    :returns: The path of the link.
    """
    target = materialize_driver(directory / "target", CHROME_DRIVER_NAME)
    link = directory / "link" / CHROME_DRIVER_NAME
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    return str(link)


def _build_group_writable(directory: Path) -> str:
    """An executable its group may rewrite.

    :param directory: The test's own directory.
    :returns: The path of the file.
    """
    return str(materialize_driver(directory / "group", CHROME_DRIVER_NAME, 0o775))


def _build_world_writable(directory: Path) -> str:
    """An executable anybody may rewrite.

    :param directory: The test's own directory.
    :returns: The path of the file.
    """
    return str(materialize_driver(directory / "world", CHROME_DRIVER_NAME, 0o757))


def _build_in_writable_directory(directory: Path) -> str:
    """A good executable in a directory anybody may write.

    Refused because replacing the file needs write permission on the
    directory, not on the file: an attacker unlinks it and puts their own
    there.

    :param directory: The test's own directory.
    :returns: The path of the file.
    """
    holder = directory / "open-directory"
    path = materialize_driver(holder, CHROME_DRIVER_NAME)
    holder.chmod(0o777)

    return str(path)


def _build_not_executable(directory: Path) -> str:
    """A regular file with no execute bit at all.

    :param directory: The test's own directory.
    :returns: The path of the file.
    """
    return str(materialize_driver(directory / "plain", CHROME_DRIVER_NAME, 0o644))


def _build_directory(directory: Path) -> str:
    """A *directory* carrying the driver's name.

    :param directory: The test's own directory.
    :returns: The path of the directory.
    """
    path = directory / "as-directory" / CHROME_DRIVER_NAME
    path.mkdir(parents=True, exist_ok=True)

    return str(path)


def _build_fifo(directory: Path) -> str:
    """A named pipe carrying the driver's name.

    :param directory: The test's own directory.
    :returns: The path of the FIFO.
    """
    holder = directory / "as-fifo"
    holder.mkdir(parents=True, exist_ok=True)
    path = holder / CHROME_DRIVER_NAME
    os.mkfifo(path, 0o755)

    return str(path)


def _build_missing(directory: Path) -> str:
    """A path where nothing exists.

    :param directory: The test's own directory.
    :returns: The path that is absent.
    """
    return str(directory / "absent" / CHROME_DRIVER_NAME)


def _build_relative(directory: Path) -> str:
    """A relative path - the shape a ``PATH`` lookup would produce.

    :param directory: The test's own directory, which the returned path is
        expressed relative to.
    :returns: A relative path to a real, otherwise trustworthy executable, so
        that only its relativeness is what the refusal can be about.
    """
    candidate = materialize_driver(directory / "relative", CHROME_DRIVER_NAME)

    return os.path.relpath(candidate)


def _build_empty(directory: Path) -> str:
    """No path at all - a provisioning library that returned nothing.

    :param directory: The test's own directory, unused: the case is about the
        absence of a value rather than about anything on disk.
    :returns: The empty string.
    """
    return ""


#: Every class of candidate the verification has to refuse, with the reason it
#: must name.  One per requirement in ``driver.py``'s provisioning policy, so a
#: check silently dropped from that function fails a case here.
REFUSAL_CASES: Final[tuple[RefusalCase, ...]] = (
    RefusalCase("symlink", _build_symlink, "not-a-regular-file", posix_only=True),
    RefusalCase(
        "group-writable",
        _build_group_writable,
        "file-writable-by-others",
        posix_only=True,
    ),
    RefusalCase(
        "world-writable",
        _build_world_writable,
        "file-writable-by-others",
        posix_only=True,
    ),
    RefusalCase(
        "writable-directory",
        _build_in_writable_directory,
        "directory-writable-by-others",
        posix_only=True,
    ),
    RefusalCase("not-executable", _build_not_executable, "not-executable", posix_only=True),
    RefusalCase("directory", _build_directory, "not-a-regular-file"),
    RefusalCase("fifo", _build_fifo, "not-a-regular-file", posix_only=True),
    RefusalCase("missing", _build_missing, "not-inspectable"),
    RefusalCase("relative", _build_relative, "not-absolute"),
    RefusalCase("empty", _build_empty, "not-absolute"),
)

#: The cases above as parameters, each carrying its own platform guard, so the
#: four that hold everywhere keep running on Windows while the rest skip.
REFUSAL_PARAMETERS: Final[tuple[Any, ...]] = tuple(
    pytest.param(case, marks=[POSIX_ONLY] if case.posix_only else [])
    for case in REFUSAL_CASES
)


class RecordingSubprocess:
    """Stand-in for the ``subprocess`` module the probe uses.

    Installed on the module under test, so no process is started anywhere.
    It carries through the three attributes that module reads besides ``run``
    - ``DEVNULL``, ``PIPE`` and ``SubprocessError`` - taken from the real
    module rather than invented, so the values the probe passes are the real
    ones and the ``except`` clause still names a real exception class.

    :param real: The genuine ``subprocess`` module.
    :param stdout: Bytes the fake process writes on standard output.
    :param returncode: Status the fake process exits with.
    :param error: Exception :meth:`run` raises instead of returning.
    """

    __slots__ = (
        "DEVNULL",
        "PIPE",
        "SubprocessError",
        "calls",
        "error",
        "returncode",
        "stdout",
    )

    def __init__(
        self,
        real: Any,
        stdout: bytes = b"",
        returncode: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self.DEVNULL = real.DEVNULL
        self.PIPE = real.PIPE
        self.SubprocessError = real.SubprocessError
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        #: Every invocation, as ``(argv, keyword arguments)``.
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def run(self, argv: Any, **kwargs: Any) -> Any:
        """Record one invocation and return a completed-process stand-in.

        :param argv: The argument vector, recorded exactly as passed.
        :param kwargs: Every keyword argument, recorded exactly as passed.
        :returns: An object with ``returncode`` and ``stdout``.
        :raises BaseException: :attr:`error`, when one was programmed.
        """
        self.calls.append((argv, dict(kwargs)))

        if self.error is not None:
            raise self.error

        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout)


@pytest.mark.parametrize(
    ("browser", "driver_name", "service_target", "constructor_target", "manager_targets"),
    [
        (
            "chrome",
            CHROME_DRIVER_NAME,
            CHROME_SERVICE,
            WEBDRIVER_CHROME,
            (CHROME_MANAGER_CONSTRUCT, CHROME_MANAGER_INSTALL),
        ),
        (
            "firefox",
            GECKO_DRIVER_NAME,
            FIREFOX_SERVICE,
            WEBDRIVER_FIREFOX,
            (GECKO_MANAGER_CONSTRUCT, GECKO_MANAGER_INSTALL),
        ),
    ],
)
def test_a_pre_provisioned_driver_is_used_and_no_manager_is_constructed(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    trusted_driver_directory: Callable[..., Path],
    browser: str,
    driver_name: str,
    service_target: str,
    constructor_target: str,
    manager_targets: tuple[str, ...],
) -> None:
    """Pin route one, for both branches - the pre-provisioned driver wins.

    With a verifying driver in the searched system directories, provisioning
    is a directory lookup: no manager object is constructed, no ``install()``
    happens, and therefore no HTTP request, no archive extraction and no cache
    write can occur.  That is what makes the suite runnable on a host with no
    provisioning network, and it is the route this port prefers.

    The path that reaches the ``Service`` is the file this test created, and
    the browser is still constructed bare with that service object.
    """
    installed = trusted_driver_directory(driver_name)
    browser_key(browser)

    session = driver_module.get_driver()

    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.targets() == (service_target, constructor_target)

    for target in manager_targets:
        assert driver_harness.recorder.count_of(target) == 0

    service_call = one_call(driver_harness.recorder, service_target)
    assert str(installed) in service_call.values()

    browser_call = one_call(driver_harness.recorder, constructor_target)
    assert browser_call.args == ()
    assert set(browser_call.kwargs) == {"service"}
    assert browser_call.kwargs["service"].path == str(installed)


@pytest.mark.parametrize(
    "case",
    REFUSAL_PARAMETERS,
    ids=[case.case_id for case in REFUSAL_CASES],
)
def test_an_untrustworthy_driver_path_never_reaches_selenium(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    tmp_path: Path,
    case: RefusalCase,
) -> None:
    """Pin the verification gate over a path the provisioning library returned.

    ``webdriver-manager`` 4.1.2 validates no checksum and no signature,
    extracts what it downloaded and trusts cache metadata under a writable
    home directory, so what ``install()`` returns is a candidate rather than an
    authority.  Each case here is one requirement of the verification: a
    symbolic link, a group- or world-writable file, a good file in a directory
    anybody may write, a file with no execute bit, a directory, a FIFO, a
    missing path, a relative path and no path at all.

    Every one of them raises ``DriverProvisioningError`` *before* any
    ``Service`` is built, so no browser constructor is reached and the slot
    stays empty - a refusal cannot be mistaken for a session.  The message
    names the browser and the reason class and carries neither the rejected
    path nor anything else that produced it.
    """
    driver_harness.chrome_manager.binary_path = case.build(tmp_path / case.case_id)
    browser_key("chrome")

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module.get_driver()

    message = str(raised.value)

    assert "chrome" in message
    assert f"[{case.reason}]" in message
    assert driver_module._session() is None
    assert driver_harness.recorder.targets() == (
        CHROME_MANAGER_CONSTRUCT,
        CHROME_MANAGER_INSTALL,
    )
    assert driver_harness.sessions == []


@pytest.mark.parametrize(
    ("case_id", "windows", "macos", "expected"),
    [
        ("windows", True, False, "windows"),
        ("macos", False, True, "macos"),
        ("linux", False, False, "elsewhere"),
        # A platform cannot be both; the Windows arm is tested first, which
        # fixes the answer and is the behaviour the tables below rely on.
        ("windows-wins", True, True, "windows"),
    ],
)
def test_each_of_the_three_supported_platforms_selects_its_own_table(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    windows: bool,
    macos: bool,
    expected: str,
) -> None:
    """Pin the three-arm platform selection AAP 0.8's support matrix requires.

    Windows, Linux and macOS each need their own locations, and ``os.name``
    cannot express that - it is ``"posix"`` for Linux and macOS alike.  The
    selection is therefore one function with three arms, asserted here on every
    platform by replacing the two predicates it reads, which is also what gives
    the other platforms' tables coverage on this one.
    """
    monkeypatch.setattr(driver_module, "_IS_WINDOWS", windows)
    monkeypatch.setattr(driver_module, "_IS_MACOS", macos)

    assert driver_module._for_platform("windows", "macos", "elsewhere") == expected


def test_macos_looks_for_browsers_inside_its_application_bundles() -> None:
    """Pin the macOS browser locations, which are bundles and not Linux paths.

    A browser probe that finds nothing is not harmless: the provisioning
    library then asks for the *latest* driver rather than the one matching the
    installed browser, and a driver newer than the browser refuses to drive it,
    so every scenario fails at its first session.  On macOS the browsers live
    inside ``/Applications`` bundles, so the Linux table would find none of
    them - which is why the arm exists.

    Every candidate is absolute, as the no-shell probe requires: the spaces in
    these paths are spaces, not escapes, because nothing here reaches a shell.
    """
    binaries = driver_module._MACOS_BROWSER_BINARIES

    assert (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        in binaries[ChromeType.GOOGLE]
    )
    assert "/Applications/Firefox.app/Contents/MacOS/firefox" in binaries["firefox"]
    assert set(binaries) == {ChromeType.GOOGLE, "firefox"}

    for candidates in binaries.values():
        for candidate in candidates:
            assert os.path.isabs(candidate)
            assert "\\" not in candidate


def test_macos_searches_the_directories_a_macos_driver_is_actually_in() -> None:
    """Pin the macOS driver search, including both Homebrew prefixes.

    Homebrew installs into ``/opt/homebrew/bin`` on Apple silicon and
    ``/usr/local/bin`` on Intel, which between them are where a macOS
    operator's ``chromedriver`` or ``geckodriver`` is; without them route one
    can never hit on that platform and every run depends on the network.
    """
    directories = driver_module._MACOS_DRIVER_DIRECTORIES

    assert directories[:2] == ("/opt/homebrew/bin", "/usr/local/bin")
    assert "/usr/bin" in directories
    assert all(os.path.isabs(directory) for directory in directories)


def test_windows_searches_only_administrator_writable_locations() -> None:
    """Pin the Windows position, which is where the trust actually comes from.

    The module cannot reason about who may write a file on Windows - an ACL is
    not reported by ``os.lstat`` - so it claims no such check there.  What
    stands instead is the set of locations: ``Program Files`` style
    directories, which only an administrator can write.  A user-writable
    location such as ``%LOCALAPPDATA%`` appearing here would quietly remove the
    only protection that platform has.
    """
    directories = driver_module._WINDOWS_DRIVER_DIRECTORIES
    binaries = driver_module._WINDOWS_BROWSER_BINARIES

    assert all(directory.startswith("C:\\") for directory in directories)
    assert all("APPDATA" not in directory.upper() for directory in directories)
    assert all("USERS" not in directory.upper() for directory in directories)

    for candidates in binaries.values():
        for candidate in candidates:
            assert candidate.startswith("C:\\Program Files")
            assert candidate.endswith(".exe")


def test_the_writability_and_owner_checks_are_skipped_where_they_mean_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the platform boundary of the driver verification itself.

    Windows reports no POSIX mode bits and answers ``st_uid`` as ``0`` for
    every file, so the three requirements that reason about *who else* may
    write a driver are skipped there rather than evaluated against values that
    carry no meaning - reporting a pass from such arithmetic would claim a
    check that never ran.

    What still applies on every platform is the rest of the gate, and this
    asserts both halves: a world-writable executable is accepted with the POSIX
    reasoning disabled, while a directory and a relative path are still
    refused.
    """
    permissive = materialize_driver(tmp_path / "windows-style", CHROME_DRIVER_NAME, 0o777)

    monkeypatch.setattr(driver_module, "_HAS_POSIX_PERMISSIONS", False)

    assert driver_module._refusal_reason(str(permissive)) is None
    assert driver_module._refusal_reason(_build_directory(tmp_path)) == "not-a-regular-file"
    assert driver_module._refusal_reason("chromedriver") == "not-absolute"


def test_a_refusal_message_carries_no_path(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    tmp_path: Path,
) -> None:
    """Pin that a refusal discloses the reason class and nothing else.

    A refusal is diagnosed from the browser and the reason, so the rejected
    path - which is attacker-influenced, being whatever the provisioning
    library or a poisoned cache produced - stays out of a message that a
    caller may log or publish into a report artifact.

    The rejected candidate is a directory wearing the driver's name, which is
    refused on every platform: the disclosure rule is not a POSIX rule, so the
    case it is asserted over must not be a POSIX-only one.
    """
    rejected = _build_directory(tmp_path / "disclosure")
    driver_harness.chrome_manager.binary_path = rejected
    browser_key("chrome")

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module.get_driver()

    message = str(raised.value)

    assert rejected not in message
    assert str(tmp_path) not in message
    assert CHROME_DRIVER_NAME not in message


@POSIX_ONLY
def test_a_driver_owned_by_another_account_is_refused(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    tmp_path: Path,
) -> None:
    """Pin the ownership requirement - root or this user, nobody else.

    A driver some other local account owns is a driver that account can
    rewrite between provisioning and execution, so ownership is part of the
    verification on POSIX.  The case needs an actual ``chown``, which only a
    privileged process can perform; where it cannot, the requirement is not
    exercisable and the test says so rather than passing vacuously.
    """
    candidate = materialize_driver(tmp_path / "foreign", CHROME_DRIVER_NAME)
    foreign_uid = 65534

    try:
        os.chown(candidate, foreign_uid, foreign_uid)
    except (OSError, AttributeError) as error:
        pytest.skip(f"this process may not change file ownership: {error}")

    driver_harness.chrome_manager.binary_path = str(candidate)
    browser_key("chrome")

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module.get_driver()

    assert "[foreign-owner]" in str(raised.value)
    assert driver_module._session() is None
    assert driver_harness.sessions == []


@POSIX_ONLY
def test_an_untrustworthy_system_candidate_is_skipped_not_executed(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    trusted_driver_directory: Callable[..., Path],
) -> None:
    """Pin route one's treatment of a candidate that fails verification.

    A world-writable ``chromedriver`` sitting in a searched directory is
    exactly what the verification exists to reject, and rejecting it means the
    search continues: the module falls through to the manager route rather
    than executing it, and rather than failing a run that could still be
    provisioned.  So the file is never the path any ``Service`` receives.
    """
    hostile = trusted_driver_directory(CHROME_DRIVER_NAME, 0o777)
    browser_key("chrome")

    session = driver_module.get_driver()

    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE

    service_call = one_call(driver_harness.recorder, CHROME_SERVICE)
    assert str(hostile) not in service_call.values()
    assert str(driver_harness.chrome_binary_path) in service_call.values()


def test_the_manager_call_forces_verified_transport_and_an_approved_cache(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the environment the provisioning library sees during ``install()``.

    ``webdriver_manager.core.config`` calls ``load_dotenv()`` at import, so a
    hidden ``.env`` file from the working directory upward has already merged
    its values into the process environment, and the library reads its trust
    settings from there at call time.  This test installs the hostile values
    that matter - certificate verification off, the relocatable cache on, plus
    the library's log, progress-bar and xdist-worker variables - and asserts
    what the library actually observes while ``install()`` runs:

    * ``WDM_SSL_VERIFY`` reads as enabled and ``ssl_verify()`` answers ``True``,
      so downloads cannot proceed over an unverified connection;
    * ``WDM_LOCAL`` reads as disabled and ``wdm_local()`` answers ``False``, so
      the cache root cannot be moved - the library applies that variable
      *after* an explicitly passed root, so nothing else can pin it;
    * the removed variables are absent, so neither the log level nor the cache
      path can be steered from the environment.

    Afterwards the ambient values are back exactly as the operator set them:
    the override is scoped to the call and observable nowhere else.
    """
    ambient = {
        "WDM_SSL_VERIFY": "0",
        "WDM_LOCAL": "1",
        "WDM_LOG": "0",
        "WDM_LOG_LEVEL": "0",
        "WDM_PROGRESS_BAR": "0",
        "PYTEST_XDIST_WORKER": "gw7",
    }

    for name, value in ambient.items():
        monkeypatch.setenv(name, value)

    observed: list[dict[str, Any]] = []

    def snapshot() -> None:
        from webdriver_manager.core import config as wdm_config

        observed.append(
            {
                "environment": {
                    name: os.environ.get(name)
                    for name in (*(key for key, _ in FORCED_WDM_SETTINGS), *REMOVED_WDM_SETTINGS)
                },
                "ssl_verify": wdm_config.ssl_verify(),
                "wdm_local": wdm_config.wdm_local(),
            }
        )

    driver_harness.chrome_manager.install_hook = snapshot
    browser_key("chrome")

    assert driver_module.get_driver() is driver_harness.sessions[0]
    assert len(observed) == 1

    during = observed[0]

    for name, value in FORCED_WDM_SETTINGS:
        assert during["environment"][name] == value

    for name in REMOVED_WDM_SETTINGS:
        assert during["environment"][name] is None

    assert during["ssl_verify"] is True
    assert during["wdm_local"] is False

    for name, value in ambient.items():
        assert os.environ[name] == value


def test_the_ambient_environment_is_restored_when_provisioning_fails(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the restore as unconditional - an ``install()`` that raises included.

    The override is held only for the duration of the call, so a failure in
    the middle of it must not leave the process carrying values the operator
    never set.  The provisioning failure itself propagates unchanged: it is not
    converted into a refusal, and nothing is constructed.
    """
    monkeypatch.setenv("WDM_SSL_VERIFY", "0")
    monkeypatch.setenv("WDM_LOCAL", "1")
    monkeypatch.delenv("WDM_LOG", raising=False)

    failure = RuntimeError("the driver host is unreachable")
    driver_harness.chrome_manager.install_error = failure
    browser_key("chrome")

    with pytest.raises(RuntimeError) as raised:
        driver_module.get_driver()

    assert raised.value is failure
    assert os.environ["WDM_SSL_VERIFY"] == "0"
    assert os.environ["WDM_LOCAL"] == "1"
    assert "WDM_LOG" not in os.environ

    assert driver_module._session() is None
    assert driver_harness.recorder.count_of(CHROME_SERVICE) == 0
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 0


@pytest.mark.parametrize(
    ("browser", "construct_target"),
    [("chrome", CHROME_MANAGER_CONSTRUCT), ("firefox", GECKO_MANAGER_CONSTRUCT)],
)
def test_each_manager_is_given_the_no_shell_browser_probe(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    browser: str,
    construct_target: str,
) -> None:
    """Pin the probe override as the only platform helper either manager gets.

    The library's own helper builds its command from bare program names and
    runs it through a shell, so a ``google-chrome`` or ``firefox`` earlier in
    ``PATH`` would be executed during provisioning.  Both managers accept an
    ``os_system_manager``, and this asserts that each is constructed with the
    replacement - by keyword, alongside the cache manager and nothing else.

    The same replacement is asserted on the *cache* manager, and that is not a
    duplicate of the line above it: ``DriverCacheManager.find_driver`` asks its
    own platform helper for the installed browser version on every lookup, so
    a cache manager left to build a default would put the library's shell probe
    back on the most travelled path there is - the cache hit.
    """
    browser_key(browser)

    assert driver_module.get_driver() is driver_harness.sessions[0]

    construction = one_call(driver_harness.recorder, construct_target)

    assert construction.args == ()
    assert set(construction.kwargs) == {"os_system_manager", "cache_manager"}

    supplied = construction.kwargs["os_system_manager"]

    assert isinstance(supplied, driver_module._VerifiedSystemManager)
    assert isinstance(supplied, OperationSystemManager)

    cache_manager = construction.kwargs["cache_manager"]

    assert isinstance(cache_manager, DriverCacheManager)
    assert cache_manager._os_system_manager is supplied
    assert isinstance(cache_manager._file_manager, driver_module._ValidatingFileManager)


@pytest.mark.parametrize(
    ("browser", "construct_target"),
    [("chrome", CHROME_MANAGER_CONSTRUCT), ("firefox", GECKO_MANAGER_CONSTRUCT)],
)
def test_the_provisioning_cache_is_rooted_where_no_ambient_value_can_move_it(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
    browser: str,
    construct_target: str,
) -> None:
    """Pin the cache root as this module's decision and not the environment's.

    With no explicit root the library computes ``DEFAULT_USER_HOME_CACHE_PATH``
    - ``<HOME>/.wdm`` - so an ambient ``HOME`` would relocate every cache read
    and every cache write, and the cache is what gets executed.  A hostile
    ``HOME`` is installed here and the root the cache manager actually carries
    is the private one, which the library derives from the passed ``root_dir``
    by appending its own ``.wdm`` folder name.
    """
    hostile_home = driver_harness.provisioned_directory.parent / "hostile-home"
    hostile_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(hostile_home))

    browser_key(browser)

    assert driver_module.get_driver() is driver_harness.sessions[0]

    cache_manager = one_call(driver_harness.recorder, construct_target).kwargs["cache_manager"]
    root = cache_manager._root_dir

    assert root.startswith(str(driver_harness.cache_root))
    assert str(hostile_home) not in root
    assert driver_harness.cache_root.is_dir()


def test_the_private_cache_root_is_user_scoped_under_the_system_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin where the real cache root is computed from - and from what it is not.

    Three properties, and the third is the finding: the location is under the
    system temporary directory, it carries this account's user id on POSIX so
    two accounts sharing that directory cannot meet in one root, and it does
    not derive from ``HOME`` at all - the ambient value the provisioning
    library would otherwise have used.
    """
    hostile_home = tmp_path / "hostile-home"
    monkeypatch.setenv("HOME", str(hostile_home))

    root = driver_module._private_cache_root_path()

    assert os.path.isabs(root)
    assert root.startswith(tempfile.gettempdir())
    assert str(hostile_home) not in root
    assert os.path.basename(root).startswith(CACHE_DIRECTORY_NAME)

    if hasattr(os, "getuid"):
        assert os.path.basename(root) == f"{CACHE_DIRECTORY_NAME}-{os.getuid()}"
    else:
        assert os.path.basename(root) == CACHE_DIRECTORY_NAME


def test_the_private_cache_root_is_created_unreachable_by_others_and_then_reused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the creation: private, and stable across calls.

    The root has to be private because its contents are executed afterwards,
    and it has to be *stable* because a per-run location would download a
    fresh binary for every scenario shard and leave a directory behind for
    each.  So a second call returns the same path and creates nothing new.
    """
    root = tmp_path / "cache"
    monkeypatch.setattr(driver_module, "_private_cache_root_path", lambda: str(root))

    first = driver_module._private_cache_root("chrome")
    created = root.stat().st_mtime_ns
    second = driver_module._private_cache_root("firefox")

    assert first == second == str(root)
    assert root.is_dir()
    assert root.stat().st_mtime_ns == created

    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) & PERMISSIVE_MODE_BITS == 0


def test_an_untrustworthy_cache_root_is_refused_rather_than_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin every refusal class of the cache root, and that none is repaired.

    A root that already exists and fails verification is refused: widening,
    chmod-ing or replacing it would mean this process accepting a directory it
    did not create under a name it expects to own, which on a shared temporary
    directory is exactly the substitution the check exists to notice.

    Four classes, each reached through the real creation path:

    * a path that cannot be created at all, because a regular file stands
      where a parent directory would be;
    * a regular file standing where the root belongs;
    * a symbolic link standing where the root belongs, which ``os.lstat``
      reports as a link rather than following - so the target is never
      inspected, written into or executed from;
    * on POSIX, an existing directory anybody may write.
    """
    blocked_parent = tmp_path / "file"
    blocked_parent.write_bytes(b"")
    monkeypatch.setattr(
        driver_module,
        "_private_cache_root_path",
        lambda: str(blocked_parent / "cache"),
    )

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module._private_cache_root("chrome")

    assert "[cache-root-unusable]" in str(raised.value)

    as_file = tmp_path / "as-file"
    as_file.write_bytes(b"")
    monkeypatch.setattr(driver_module, "_private_cache_root_path", lambda: str(as_file))

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module._private_cache_root("chrome")

    assert "[cache-root-not-a-directory]" in str(raised.value)

    if os.name == "posix":
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = tmp_path / "as-link"
        link.symlink_to(target)
        monkeypatch.setattr(driver_module, "_private_cache_root_path", lambda: str(link))

        with pytest.raises(driver_module.DriverProvisioningError) as raised:
            driver_module._private_cache_root("chrome")

        assert "[cache-root-not-a-directory]" in str(raised.value)

        open_root = tmp_path / "open"
        open_root.mkdir(mode=0o777)
        open_root.chmod(0o777)
        monkeypatch.setattr(driver_module, "_private_cache_root_path", lambda: str(open_root))

        with pytest.raises(driver_module.DriverProvisioningError) as raised:
            driver_module._private_cache_root("chrome")

        assert "[cache-root-writable-by-others]" in str(raised.value)


def test_a_cache_root_that_cannot_be_inspected_is_refused() -> None:
    """Pin the inspection failure as a refusal rather than an assumption.

    :func:`driver._cache_root_refusal` is the half of the check that runs over
    an existing root, and a path it cannot ``lstat`` is one it knows nothing
    about.  Answering ``None`` there would mean "no reason to refuse" for a
    directory that may not even exist.
    """
    assert (
        driver_module._cache_root_refusal(os.path.join(os.sep, "nonexistent", "cache"))
        == "cache-root-unusable"
    )


@POSIX_ONLY
def test_a_cache_root_owned_by_another_account_is_refused(tmp_path: Path) -> None:
    """Pin the ownership requirement on the cache root - this account only.

    Unlike a driver binary, which an administrator may legitimately have
    installed as root, this root is created by this process: one owned by
    anybody else is one something else created under the name this process
    expects to own, which is how a shared temporary directory without a sticky
    bit gets substituted underneath a run.  The case needs a real ``chown``,
    which only a privileged process can perform.
    """
    root = tmp_path / "foreign"
    root.mkdir(mode=0o700)
    foreign_uid = 65534

    try:
        os.chown(root, foreign_uid, foreign_uid)
    except (OSError, AttributeError) as error:
        pytest.skip(f"this process may not change directory ownership: {error}")

    assert driver_module._cache_root_refusal(str(root)) == "cache-root-foreign-owner"


def test_the_cache_root_mode_checks_are_posix_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the platform boundary of the cache root's permission checks.

    Windows decides who may write a directory with an access-control list that
    ``os.lstat`` does not report, and reports every owner as ``0``, so the two
    POSIX checks are skipped there rather than evaluated against values that
    mean nothing.  What still holds on that platform is that the root exists
    and is a directory - and ``%TEMP%`` is per-account there, which is what the
    user-id scoping provides on POSIX.
    """
    open_root = tmp_path / "windows-style"
    open_root.mkdir()
    open_root.chmod(0o777)

    monkeypatch.setattr(driver_module, "_HAS_POSIX_PERMISSIONS", False)

    assert driver_module._cache_root_refusal(str(open_root)) is None

    as_file = tmp_path / "windows-file"
    as_file.write_bytes(b"")

    assert driver_module._cache_root_refusal(str(as_file)) == "cache-root-not-a-directory"


def test_a_refused_cache_root_stops_provisioning_before_any_download(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the refusal's position: before the manager is even constructed.

    The root is where a downloaded driver would be written and where cache
    metadata would be read from, so a root that cannot be trusted is not a
    degraded provisioning run - it is no provisioning run at all.  No manager
    is built, nothing is downloaded, no service is created and the slot stays
    empty.
    """
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"")
    monkeypatch.setattr(
        driver_module,
        "_private_cache_root_path",
        lambda: str(blocked / "cache"),
    )

    browser_key("chrome")

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module.get_driver()

    assert "[cache-root-unusable]" in str(raised.value)
    assert "chrome" in str(raised.value)
    assert driver_harness.recorder.targets() == ()
    assert driver_module._session() is None


def test_the_cache_root_the_library_resolved_is_read_back_and_required_to_be_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the private cache root as a verified outcome, not a requested one.

    ``DriverCacheManager.__init__`` applies ``WDM_LOCAL`` *after* the
    ``root_dir`` it is handed, and a true value there discards that root
    outright for a working-directory-relative ``.wdm``.  Forcing the variable
    false is what the trusted environment window does - so asking for the
    private root and getting it is a property of *when* the manager is built,
    and a security property that depends on its caller's ordering is one a
    later caller loses in silence.

    Both halves are pinned here.  Inside the window the resolved root sits
    inside the private one, which is the production path.  With the variable
    left ambient - the caller that forgot the window, or a library that starts
    reading one more setting - the resolved root is elsewhere and provisioning
    *refuses* rather than quietly reading and executing from a directory
    anything on this host can write.
    """
    root = tmp_path / "cache"
    monkeypatch.setattr(driver_module, "_private_cache_root_path", lambda: str(root))

    def factory(**keywords: object) -> SimpleNamespace:
        return SimpleNamespace(**keywords)

    monkeypatch.setenv("WDM_LOCAL", "1")

    with driver_module._trusted_provisioning_environment():
        manager = driver_module._provisioning_manager("chrome", factory)

    resolved = manager.cache_manager._root_dir

    assert driver_module._resolves_inside(resolved, str(root))
    assert manager.cache_manager._os_system_manager is manager.os_system_manager

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        driver_module._provisioning_manager("chrome", factory)

    assert "[cache-root-not-private]" in str(raised.value)
    assert "chrome" in str(raised.value)


def test_one_containment_rule_answers_both_paths_that_ask_it(tmp_path: Path) -> None:
    """Pin :func:`driver._resolves_inside` - the rule two checks share.

    The archive unpacker asks it whether a member would be written outside the
    directory being unpacked into, and the provisioning manager asks it whether
    the library's cache resolved outside the private root.  Four answers matter:
    the root itself is inside itself, a path beneath it is inside it, a sibling
    whose name merely starts with the root's is not, and a *relative* candidate
    is resolved against the working directory first - which is the case the
    cache check exists for, since the library's fallback root is a relative
    ``.wdm``.  A non-string, which is what reading an attribute off a foreign
    object can yield, is inside nothing.
    """
    root = tmp_path / "root"
    root.mkdir()

    assert driver_module._resolves_inside(str(root), str(root))
    assert driver_module._resolves_inside(str(root / "a" / "b"), str(root))
    assert not driver_module._resolves_inside(f"{root}-sibling", str(root))
    assert not driver_module._resolves_inside(str(root / ".." / "elsewhere"), str(root))
    assert not driver_module._resolves_inside(".wdm", str(root))
    assert driver_module._resolves_inside(".wdm", os.getcwd())
    assert not driver_module._resolves_inside(None, str(root))
    assert not driver_module._resolves_inside("", str(root))


def unpack(archive: Path, destination: Path) -> list[str]:
    """Unpack one archive through the module's validating file manager.

    The file manager is constructed exactly as
    :func:`driver._provisioning_manager` constructs it - over the verified
    platform helper - so what these tests exercise is the object the
    provisioning library would actually be given.

    :param archive: The archive file to unpack.
    :param destination: The directory to extract into; created if absent.
    :returns: The member names the unpacker reports.
    """
    destination.mkdir(parents=True, exist_ok=True)
    manager = driver_module._ValidatingFileManager(driver_module._VerifiedSystemManager())

    return manager.unpack_archive(SimpleNamespace(file_path=str(archive)), str(destination))


def build_tar(path: Path, members: Iterable[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    """Write a gzip tar archive containing exactly the given members.

    Built member by member rather than from files on disk, because the members
    that matter here - an absolute name, a ``..`` name, a symbolic link, a hard
    link, a FIFO, a device node - are ones a hostile publisher writes into an
    archive and several of which cannot be created on the host at all.

    :param path: Where to write the archive; its parent is created.
    :param members: Pairs of member metadata and payload, the payload being
        ``None`` for a member that carries no data.
    :returns: ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(path, mode="w:gz") as archive:
        for info, payload in members:
            if payload is None:
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    return path


def tar_member(
    name: str,
    kind: bytes = tarfile.REGTYPE,
    mode: int = 0o755,
    link: str = "",
) -> tarfile.TarInfo:
    """Return one tar member's metadata.

    :param name: The member name, exactly as the archive should record it.
    :param kind: The tar type flag - regular, directory, symlink, and so on.
    :param mode: The member's mode bits.
    :param link: The link target, for a symbolic or hard link.
    :returns: The member metadata.
    """
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.linkname = link

    return info


def build_zip(path: Path, members: Iterable[tuple[str, int, bytes]]) -> Path:
    """Write a zip archive containing exactly the given members.

    :param path: Where to write the archive; its parent is created.
    :param members: Triples of member name, POSIX mode - stored in the upper
        half of ``external_attr`` exactly as an archiver would - and payload.
    :returns: ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, mode="w") as archive:
        for name, mode, payload in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, payload)

    return path


def test_an_ordinary_tar_archive_is_unpacked_with_the_hardened_filter(
    tmp_path: Path,
) -> None:
    """Pin the tar happy path - the shape a geckodriver release really has.

    A directory member and the binary inside it, unpacked into the cache
    directory the library asked for, with the member names reported back
    because that list is what the cache manager selects the driver binary from.
    """
    archive = build_tar(
        tmp_path / "geckodriver.tar.gz",
        [
            (tar_member("geckodriver-v0.37.1", kind=tarfile.DIRTYPE), None),
            (tar_member("geckodriver-v0.37.1/geckodriver"), b"ELF"),
        ],
    )
    destination = tmp_path / "cache"

    names = unpack(archive, destination)

    assert names == ["geckodriver-v0.37.1", "geckodriver-v0.37.1/geckodriver"]
    assert (destination / "geckodriver-v0.37.1" / "geckodriver").read_bytes() == b"ELF"


def test_an_ordinary_zip_archive_is_unpacked_through_zipfile(tmp_path: Path) -> None:
    """Pin the zip happy path - the shape a chromedriver release really has.

    The mode bits the archive carries are deliberately *not* restored: both
    managers ``chmod`` the binary they return to ``0o755``, and a mode an
    untrusted archive chose is not something to reproduce on disk.
    """
    archive = build_zip(
        tmp_path / "chromedriver.zip",
        [
            ("chromedriver-linux64/", 0o040755, b""),
            ("chromedriver-linux64/chromedriver", 0o100755, b"ELF"),
            ("chromedriver-linux64/LICENSE.chromedriver", 0o100644, b"license"),
        ],
    )
    destination = tmp_path / "cache"

    names = unpack(archive, destination)

    assert names == [
        "chromedriver-linux64/",
        "chromedriver-linux64/chromedriver",
        "chromedriver-linux64/LICENSE.chromedriver",
    ]
    assert (destination / "chromedriver-linux64" / "chromedriver").read_bytes() == b"ELF"


@pytest.mark.parametrize(
    ("case_id", "member", "reason"),
    [
        ("absolute-posix", tar_member("/etc/cron.d/provisioned"), "archive-member-absolute"),
        ("absolute-windows-separator", tar_member(r"\Windows\System32\evil"), "archive-member-absolute"),
        ("absolute-windows-drive", tar_member(r"C:\Windows\System32\evil"), "archive-member-absolute"),
        ("parent-traversal", tar_member("../../evil"), "archive-member-escapes-cache"),
        ("nested-traversal", tar_member("drivers/../../evil"), "archive-member-escapes-cache"),
    ],
)
def test_a_tar_member_written_outside_the_cache_is_refused(
    tmp_path: Path,
    case_id: str,
    member: tarfile.TarInfo,
    reason: str,
) -> None:
    """Pin the traversal refusals, which are the whole of the library's defect.

    ``file_manager.py:113`` extracts with ``filter="fully_trusted"``, so an
    absolute member name or a ``..`` member of a hostile archive is written
    exactly where it says.  Every spelling of "outside the destination" is
    refused here - including the two Windows spellings, on every platform,
    because the archive's author chose the name and the host reading it decides
    nothing about what it means.

    The refusal names the reason class and not the member, and nothing is left
    on disk: the validation completes before extraction begins, so a refused
    archive is one that was never unpacked at all.
    """
    archive = build_tar(tmp_path / f"{case_id}.tar.gz", [(member, b"payload")])
    destination = tmp_path / "cache"

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        unpack(archive, destination)

    message = str(raised.value)

    assert f"[{reason}]" in message
    assert member.name not in message
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    ("case_id", "kind", "link"),
    [
        ("symlink", tarfile.SYMTYPE, "/etc/passwd"),
        ("hardlink", tarfile.LNKTYPE, "geckodriver"),
        ("fifo", tarfile.FIFOTYPE, ""),
        ("character-device", tarfile.CHRTYPE, ""),
        ("block-device", tarfile.BLKTYPE, ""),
    ],
)
def test_a_tar_member_that_is_not_a_regular_file_is_refused(
    tmp_path: Path,
    case_id: str,
    kind: bytes,
    link: str,
) -> None:
    """Pin the type refusals - the other half of an unfiltered extraction.

    A link, a FIFO or a device node placed in the cache is how an archive makes
    the read-back of its own contents reach somewhere else: the binary path the
    manager returns is executed, and the metadata beside it is read, so a
    member that is not a plain file or a directory has no legitimate purpose
    here and is refused rather than created.
    """
    archive = build_tar(
        tmp_path / f"{case_id}.tar.gz",
        [(tar_member("geckodriver", kind=kind, link=link), None)],
    )
    destination = tmp_path / "cache"

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        unpack(archive, destination)

    assert "[archive-member-not-a-regular-file]" in str(raised.value)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    ("case_id", "name", "mode", "reason"),
    [
        ("absolute", "/etc/cron.d/provisioned", 0o100644, "archive-member-absolute"),
        ("traversal", "../../evil", 0o100644, "archive-member-escapes-cache"),
        ("symlink", "chromedriver", 0o120777, "archive-member-not-a-regular-file"),
        ("fifo", "chromedriver", 0o010644, "archive-member-not-a-regular-file"),
    ],
)
def test_a_zip_member_this_port_will_not_write_is_refused(
    tmp_path: Path,
    case_id: str,
    name: str,
    mode: int,
    reason: str,
) -> None:
    """Pin the same three requirements over the zip path.

    ``zipfile`` sanitizes member names as it extracts, so the traversal cases
    are defence in depth there - but it applies no type check at all, and a zip
    records a POSIX mode in the upper half of ``external_attr``, which is how a
    symbolic link travels inside one.  Chromedriver ships as a zip, so this is
    the path the Chrome branch actually takes.
    """
    archive = build_zip(tmp_path / f"{case_id}.zip", [(name, mode, b"payload")])
    destination = tmp_path / "cache"

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        unpack(archive, destination)

    assert f"[{reason}]" in str(raised.value)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(("case_id", "mode"), [("no-metadata", 0), ("permissions-only", 0o644)])
def test_a_zip_member_recording_no_file_type_is_an_ordinary_file(
    tmp_path: Path,
    case_id: str,
    mode: int,
) -> None:
    """Pin the tolerant reading of a zip written without POSIX type bits.

    An archiver on a platform that has no such bits stores permission bits
    alone, or none at all - ``zipfile`` itself stores ``0o600`` for a member
    given no mode.  Neither is a refusal: with no file type recorded, an
    ordinary file is what the archive is claiming, and reading it as "not a
    regular file" would refuse every legitimate Windows driver archive.
    """
    archive = build_zip(tmp_path / f"{case_id}.zip", [("chromedriver.exe", mode, b"MZ")])
    destination = tmp_path / "cache"

    assert unpack(archive, destination) == ["chromedriver.exe"]
    assert (destination / "chromedriver.exe").read_bytes() == b"MZ"


@pytest.mark.parametrize(
    ("case_id", "name", "payload"),
    [
        ("not-a-tar", "driver.tar.gz", b"this is not a gzip stream"),
        ("not-a-zip", "driver.zip", b"this is not a zip container"),
    ],
)
def test_an_archive_that_cannot_be_read_is_refused(
    tmp_path: Path,
    case_id: str,
    name: str,
    payload: bytes,
) -> None:
    """Pin an unreadable download as a refusal rather than a traceback.

    Whatever arrived is not the archive it claims to be, which is a
    provisioning failure with a reason - and the reason is what a refusal
    exists to carry.
    """
    archive = tmp_path / case_id / name
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(payload)

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        unpack(archive, tmp_path / case_id / "cache")

    assert "[archive-unreadable]" in str(raised.value)


@pytest.mark.parametrize("name", ["chromedriver", "driver.exe", "driver.7z", ""])
def test_an_archive_in_an_unsupported_format_is_refused(tmp_path: Path, name: str) -> None:
    """Pin the format whitelist - two suffixes, and no guessing.

    The library answers ``None`` for a suffix it does not recognise and lets
    the caller fail later while looking for a binary among no files, which
    loses the reason entirely.  Naming it here is what makes an unexpected
    download diagnosable.
    """
    manager = driver_module._ValidatingFileManager(driver_module._VerifiedSystemManager())

    with pytest.raises(driver_module.DriverProvisioningError) as raised:
        manager.unpack_archive(SimpleNamespace(file_path=name), str(tmp_path))

    assert "[archive-format-unsupported]" in str(raised.value)


def test_provisioning_neutralizes_the_transport_a_hidden_dotenv_can_supply(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the proxy and certificate-authority variables as removed for the call.

    ``webdriver_manager.core.config``'s import-time ``load_dotenv()`` can put
    these back *after* a worker launcher filtered them out of the child
    environment, and between them they decide where a driver binary is fetched
    from and what is allowed to sign for it.  Each is therefore removed for the
    duration of the call, in both spellings, and restored exactly afterwards.

    The programmed value is derived from the upper-cased name on purpose:
    Windows environment variables are case-insensitive and ``os.environ``
    upper-cases its keys there, so the two spellings of one variable have to
    agree on a value for this test to mean the same thing on every platform.
    """
    ambient = {name: f"hostile-{name.upper()}" for name in TRANSPORT_SETTINGS}

    for name, value in ambient.items():
        monkeypatch.setenv(name, value)

    observed: list[dict[str, str | None]] = []

    driver_harness.chrome_manager.install_hook = lambda: observed.append(
        {name: os.environ.get(name) for name in TRANSPORT_SETTINGS}
    )
    browser_key("chrome")

    assert driver_module.get_driver() is driver_harness.sessions[0]
    assert len(observed) == 1
    assert set(observed[0].values()) == {None}

    for name, value in ambient.items():
        assert os.environ[name] == value


def test_the_transport_neutralization_is_undone_when_provisioning_fails(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the restore of the transport variables on the exception path.

    A failed provisioning attempt must leave the process exactly as it found
    it: these variables are read by every HTTP client in the worker, not only
    by the provisioning library, so one left removed would silently change
    where everything after it connects.
    """
    for name in TRANSPORT_SETTINGS:
        monkeypatch.setenv(name, f"ambient-{name.upper()}")

    driver_harness.chrome_manager.install_error = RuntimeError("the driver host is unreachable")
    browser_key("chrome")

    with pytest.raises(RuntimeError):
        driver_module.get_driver()

    for name in TRANSPORT_SETTINGS:
        assert os.environ[name] == f"ambient-{name.upper()}"


def test_the_browser_probe_runs_an_absolute_argv_with_no_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin how the probe starts a browser: absolute argv, no shell, no stdin.

    The whole of the replacement's contract, over a browser binary this test
    created: the argument vector is a list whose first element is the absolute
    path of that file, ``shell`` is false, standard input comes from
    ``DEVNULL`` so the probe can neither consume nor inherit the run's input,
    standard output is captured, and an explicit timeout bounds the call.  The
    version returned is what the anchored pattern captured from the output.
    """
    binary = materialize_driver(tmp_path / "browser", "chrome")
    recorder = RecordingSubprocess(
        driver_module.subprocess,
        stdout=b"Google Chrome 153.0.8010.36 \n",
    )

    monkeypatch.setattr(driver_module, "subprocess", recorder)
    monkeypatch.setattr(
        driver_module,
        "_BROWSER_BINARIES",
        {ChromeType.GOOGLE: (str(binary),)},
    )

    probe = driver_module._VerifiedSystemManager()
    version = probe.get_browser_version_from_os(ChromeType.GOOGLE)

    assert version == "153.0.8010"
    assert len(recorder.calls) == 1

    argv, options = recorder.calls[0]

    assert argv == [str(binary), "--version"]
    assert os.path.isabs(argv[0])
    assert options["shell"] is False
    assert options["stdin"] is recorder.DEVNULL
    assert options["stdout"] is recorder.PIPE
    assert options["timeout"] > 0
    assert "env" not in options


def test_the_browser_probe_never_resolves_a_bare_program_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin that a name resolvable through ``PATH`` is never started.

    A bare ``google-chrome`` is exactly what the library's own probe hands to a
    shell, and it is what an attacker-writable directory earlier in ``PATH``
    answers.  Here the configured location is such a name: verification refuses
    it for not being absolute, so no process is started at all and the probe
    reports an unknown version - which the library treats as "resolve it
    yourself" rather than as an error.
    """
    recorder = RecordingSubprocess(driver_module.subprocess, stdout=b"Google Chrome 1.2.3\n")

    monkeypatch.setattr(driver_module, "subprocess", recorder)
    monkeypatch.setattr(
        driver_module,
        "_BROWSER_BINARIES",
        {ChromeType.GOOGLE: ("google-chrome", "chrome")},
    )

    probe = driver_module._VerifiedSystemManager()
    version = probe.get_browser_version_from_os(ChromeType.GOOGLE)

    assert version is None
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("case_id", "stdout", "returncode", "error"),
    [
        ("non-zero-status", b"Google Chrome 153.0.8010.36\n", 1, None),
        ("unanchored-output", b"pwned by Google Chrome 153.0.8010.36\n", 0, None),
        ("no-version-at-all", b"\n", 0, None),
        ("start-failure", b"", 0, OSError("cannot execute")),
    ],
)
def test_the_browser_probe_reports_nothing_it_cannot_parse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_id: str,
    stdout: bytes,
    returncode: int,
    error: BaseException | None,
) -> None:
    """Pin the probe's failure behaviour: ``None``, never a guess.

    Four ways a probe can fail to produce an answer: the process exits
    non-zero, its output does not begin with the product name the pattern
    anchors on, it prints nothing version-shaped, or it cannot be started at
    all.  Each yields ``None``, which is the library's own signal that the
    browser version is unknown - so provisioning degrades to the library's
    default resolution rather than acting on text an untrusted process
    produced.
    """
    binary = materialize_driver(tmp_path / case_id, "chrome")
    recorder = RecordingSubprocess(
        driver_module.subprocess,
        stdout=stdout,
        returncode=returncode,
        error=error,
    )

    monkeypatch.setattr(driver_module, "subprocess", recorder)
    monkeypatch.setattr(
        driver_module,
        "_BROWSER_BINARIES",
        {ChromeType.GOOGLE: (str(binary),)},
    )

    probe = driver_module._VerifiedSystemManager()

    assert probe.get_browser_version_from_os(ChromeType.GOOGLE) is None
    assert len(recorder.calls) == 1


def test_the_browser_probe_reports_nothing_for_an_unknown_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the probe's answer for a browser it has no pattern for.

    The two managers this port uses ask about Chrome and Firefox only.  Any
    other key - a manager this port does not use, asking about a browser it
    has no absolute location or pattern for - is answered with ``None`` and
    starts no process, rather than falling back to a name search.
    """
    recorder = RecordingSubprocess(driver_module.subprocess)

    monkeypatch.setattr(driver_module, "subprocess", recorder)

    probe = driver_module._VerifiedSystemManager()

    assert probe.get_browser_version_from_os("edge") is None
    assert probe.get_browser_version_from_os(None) is None
    assert recorder.calls == []


def test_the_probe_inherits_the_platform_inspection_it_does_not_override() -> None:
    """Pin that only the shelling-out method is replaced.

    The platform helper also answers the operating-system name, the
    architecture and whether the machine is ARM, and both managers use those
    to build a download URL.  None of them starts a process, so all are
    inherited: replacing more of the class than the probe would be this port
    reimplementing the library.
    """
    probe = driver_module._VerifiedSystemManager()
    baseline = OperationSystemManager()

    assert probe.get_os_name() == baseline.get_os_name()
    assert probe.get_os_architecture() == baseline.get_os_architecture()
    assert probe.get_os_type() == baseline.get_os_type()
    assert probe.is_arch() == baseline.is_arch()


@pytest.mark.parametrize(
    ("browser", "driver_name", "service_target", "constructor_target", "env_key"),
    [
        (
            "chrome",
            CHROME_DRIVER_NAME,
            CHROME_SERVICE,
            WEBDRIVER_CHROME,
            CHROME_DRIVER_PATH_ENV_KEY,
        ),
        (
            "firefox",
            GECKO_DRIVER_NAME,
            FIREFOX_SERVICE,
            WEBDRIVER_FIREFOX,
            GECKO_DRIVER_PATH_ENV_KEY,
        ),
    ],
)
def test_an_ambient_driver_path_variable_cannot_redirect_selenium(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    trusted_driver_directory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    browser: str,
    driver_name: str,
    service_target: str,
    constructor_target: str,
    env_key: str,
) -> None:
    """Pin the last ambient input: the driver-path environment variable.

    ``common/service.py:79`` computes ``self.env_path() or executable_path``,
    and ``env_path()`` reads the variable named by ``DRIVER_PATH_ENV_KEY`` -
    defaulted to ``SE_CHROMEDRIVER`` and ``SE_GECKODRIVER`` respectively, with
    ``key = key or "SE_..."`` so that passing ``None`` does not disable it.  An
    operator-invisible variable would therefore be executed instead of the path
    this module verified, and :class:`ServiceDouble` reproduces that resolution
    exactly so this test can see it.

    With such a variable installed, the service the browser constructor
    receives still carries the verified path, and its environment key has been
    cleared, so nothing consults the environment for a path again.
    """
    hostile = materialize_driver(tmp_path / "hostile", driver_name)
    monkeypatch.setenv(env_key, str(hostile))

    installed = trusted_driver_directory(driver_name)
    browser_key(browser)

    assert driver_module.get_driver() is driver_harness.sessions[0]

    browser_call = one_call(driver_harness.recorder, constructor_target)
    service = browser_call.kwargs["service"]

    assert service is one_service(driver_harness, service_target)
    assert service.path == str(installed)
    assert service.path != str(hostile)
    assert service.DRIVER_PATH_ENV_KEY is None
    assert os.environ[env_key] == str(hostile)


# --------------------------------------------------------------------------
# The package barrel
# --------------------------------------------------------------------------


def test_barrel_reexports_the_two_lifecycle_functions() -> None:
    """Pin the re-export ``app/automation/__init__.py`` promises.

    ``driver.py``'s docstring states that the package re-exports both functions
    "under exactly these names", and every step and page reaches the lifecycle
    through the barrel.  Identity rather than mere presence is what matters: a
    wrapper re-exported under the same name would defeat both the single-owner
    rule of AAP 0.3.3 and every patch point this suite relies on.
    """
    assert automation.get_driver is driver_module.get_driver
    assert automation.quit_driver is driver_module.quit_driver
    assert "get_driver" in automation.__all__
    assert "quit_driver" in automation.__all__


# --------------------------------------------------------------------------
# OS-level containment of the browser process tree
#
# ``quit()`` is a request to the driver executable, and a request that failed
# leaves that executable - and the browser it started, holding the system
# under test's authenticated session - still running.  Dropping the only
# reference to a session is not a process lifecycle, so the module owns one:
# containment is captured when a session is adopted, released on every
# teardown path, and the release is *verified* rather than assumed.
#
# Every test below runs against doubles or against processes it starts itself.
# None needs a browser, a driver binary or a network.
# --------------------------------------------------------------------------


#: How long a spawned process tree gets to appear before a test inspects it.
#: Generous enough that the leader has certainly forked its child.
TREE_SETTLE_SECONDS: Final[float] = 1.0

#: How long a test waits for a signalled tree to disappear before it reads the
#: group again.  The module's own wait is bounded and polled; this only covers
#: the moment between its last poll and the kernel reaping the last member.
TREE_REAP_SECONDS: Final[float] = 0.5

#: A pid no process can have, used where a containment must be unreachable.
UNREACHABLE_PID: Final[int] = -1

#: An opaque handle value standing in for a Win32 job handle.
FAKE_JOB_HANDLE: Final[int] = 0x2A


class ProcessTree:
    """A real two-process tree, launched exactly as the driver process is.

    The leader forks one child and both sleep.  That shape is the point: a
    plain ``terminate()`` of the leader leaves the child running, which is
    exactly the failure mode containment exists for, so a test that stopped
    only the leader would pass against the defect.

    The launch reproduces the module's own topology - ``process_group=0``, so
    the leader is in a group of its own and in *this* process's session - and
    :meth:`assert_topology` states both halves.  Building the tree any other
    way would make these tests pass over a topology production does not have.

    :param lifetime: How long each process sleeps, in seconds - long enough
        that nothing exits on its own during a test.
    """

    __slots__ = ("process", "process_group")

    def __init__(self, lifetime: int = 300) -> None:
        script = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"'import time; time.sleep({lifetime})'])\n"
            f"time.sleep({lifetime})\n"
        )
        self.process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **driver_module._containment_keywords(),
        )
        time.sleep(TREE_SETTLE_SECONDS)

        #: The group every process of the tree belongs to.
        self.process_group = os.getpgid(self.process.pid)

    def assert_topology(self) -> None:
        """Assert the tree sits in its own group inside this session.

        Both halves matter and each rules out the other's failure: a group of
        its own is what one signal can reach without reaching this process,
        and the shared session is what keeps a run supervisor able to find the
        tree after the worker it belongs to has been cancelled.

        :returns: ``None``.
        """
        assert self.process_group == self.process.pid
        assert self.process_group != os.getpgid(0)
        assert os.getsid(self.process.pid) == os.getsid(0)

    def member_count(self) -> int:
        """How many processes are still in the tree's group.

        Read from the group rather than from the leader, so a surviving child
        is counted even after the leader has gone.  Every process is listed and
        the group column is compared here, rather than asking ``ps -g`` to
        select: that option selects by *session*, and this group is
        deliberately not a session.

        :returns: The number of live members.
        """
        listing = subprocess.run(
            ["/bin/ps", "-eo", "pid=,pgid="],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = (row.split() for row in listing.stdout.splitlines())

        return len([row for row in rows if len(row) == 2 and row[1] == str(self.process_group)])

    def destroy(self) -> None:
        """Ensure nothing of the tree outlives the test.

        :returns: ``None``.
        """
        # Best effort by design, and the only handler in this file that
        # swallows: the tree may already be gone - the test under way is often
        # the thing that removed it - and a cleanup that raised would replace
        # the test's own result with a teardown error.
        try:
            os.killpg(self.process_group, signal.SIGKILL)
        except OSError:
            pass

        try:
            self.process.wait(timeout=TREE_SETTLE_SECONDS)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass


class FakeKernel32:
    """A recording stand-in for the Win32 ``kernel32`` binding.

    Only the five entry points the module calls are implemented, each
    returning a value the module has to interpret.  Every call is recorded, so
    a test asserts the *sequence* - create, limit, open, assign, close - rather
    than only the outcome.

    :param create: What ``CreateJobObjectW`` returns; ``0`` is failure.
    :param limit: What ``SetInformationJobObject`` returns; ``0`` is failure.
    :param open_process: What ``OpenProcess`` returns; ``0`` is failure.
    :param assign: What ``AssignProcessToJobObject`` returns; ``0`` is failure.
    :param close: What ``CloseHandle`` returns; ``0`` is a handle Win32
        declined to close, which for the containment job means nothing was
        terminated.
    """

    __slots__ = ("_assign", "_close", "_create", "_limit", "_open", "calls")

    def __init__(
        self,
        *,
        create: int = FAKE_JOB_HANDLE,
        limit: int = 1,
        open_process: int = 0x55,
        assign: int = 1,
        close: int = 1,
    ) -> None:
        self._create = create
        self._limit = limit
        self._open = open_process
        self._assign = assign
        self._close = close

        #: Event names in call order, with the handle each acted on.
        self.calls: list[tuple[str, Any]] = []

    def CreateJobObjectW(self, attributes: Any, name: Any) -> int:
        """Record the creation and return the programmed handle."""
        self.calls.append(("CreateJobObjectW", name))
        return self._create

    def SetInformationJobObject(
        self, job: int, info_class: int, info: Any, length: int
    ) -> int:
        """Record the limit call and return the programmed result."""
        self.calls.append(("SetInformationJobObject", (job, info_class, length)))
        return self._limit

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        """Record the open and return the programmed handle."""
        self.calls.append(("OpenProcess", (access, pid)))
        return self._open

    def AssignProcessToJobObject(self, job: int, process: int) -> int:
        """Record the assignment and return the programmed result."""
        self.calls.append(("AssignProcessToJobObject", (job, process)))
        return self._assign

    def CloseHandle(self, handle: int) -> int:
        """Record the close and return the programmed result."""
        self.calls.append(("CloseHandle", handle))
        return self._close

    def targets(self) -> tuple[str, ...]:
        """The recorded call names, in order.

        :returns: The sequence of entry points called.
        """
        return tuple(name for name, _ in self.calls)


class ProcessDouble:
    """A process object exposing only what containment reads from one.

    :param pid: The process id to report.
    :param outcome: What ``wait`` does - an exception class to raise, or
        ``None`` to return cleanly.
    """

    __slots__ = ("_outcome", "pid", "waits")

    def __init__(self, pid: int = 4321, outcome: type[BaseException] | None = None) -> None:
        self.pid = pid
        self._outcome = outcome

        #: Every timeout ``wait`` was called with, in order.
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        """Record the wait and produce the programmed outcome.

        :param timeout: The bound the caller allowed.
        :returns: ``0`` when the programmed outcome is a clean return.
        :raises BaseException: The programmed outcome, when one was given.
        """
        self.waits.append(timeout)

        if self._outcome is subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(cmd="driver", timeout=timeout or 0)

        if self._outcome is not None:
            raise self._outcome("wait failed")

        return 0


@pytest.fixture
def process_tree() -> Iterator[ProcessTree]:
    """Start a real two-process tree and guarantee its removal.

    :yields: The tree, already settled and with its group resolved.
    """
    tree = ProcessTree()

    try:
        yield tree
    finally:
        tree.destroy()


@pytest.fixture
def windows_platform(monkeypatch: pytest.MonkeyPatch) -> FakeKernel32:
    """Present the module with a Windows platform and a fake ``kernel32``.

    Both halves are required and neither is sufficient: the module chooses its
    containment mechanism from :data:`driver._HAS_PROCESS_GROUPS`, and reaches
    Win32 only through :func:`driver._kernel32`.  Faking the pair is what makes
    the Windows branches reachable from a POSIX host, which is the only way
    they are covered at all.

    :param monkeypatch: pytest's patcher.
    :returns: The fake library every Windows branch will call.
    """
    library = FakeKernel32()

    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    return library


@POSIX_ONLY
def test_the_driver_process_is_placed_in_its_own_group_in_this_session() -> None:
    """Pin the containment topology, which is one decision with two halves.

    ``popen_kw`` is Selenium's own pass-through into the ``subprocess.Popen``
    call that starts the driver executable (``common/service.py:230-239``
    expands it and passes no process-placement argument of its own), so it is
    the one place that process's placement can be requested.

    ``process_group=0`` asks for a group of the child's own *inside the
    parent's session*, and both halves are load-bearing:

    * the group is what one ``killpg`` reaches - the driver and every browser
      process it forks - without reaching the worker running the scenarios;
    * the session is what keeps the tree reachable from outside, because a run
      supervisor that cancels a worker reclaims that worker's session.

    ``start_new_session=True`` would give the first and destroy the second: the
    driver would leave the worker's process group *and* its session and would
    survive the supervisor's reclamation of both, which is the one outcome this
    request must not produce.
    """
    keywords = driver_module._containment_keywords()

    assert keywords == {"process_group": 0}
    assert "start_new_session" not in keywords


@POSIX_ONLY
def test_the_requested_placement_is_what_the_kernel_actually_does() -> None:
    """Pin the topology against the kernel rather than against the keyword.

    The keyword above is an intention; this is the measurement.  A real child
    is launched with exactly what :func:`driver._containment_keywords` returns,
    and the kernel is asked what it did with it: the child leads a group of its
    own, that group is not this process's, and the session is still this
    process's.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({TREE_SETTLE_SECONDS * 30})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **driver_module._containment_keywords(),
    )

    try:
        assert os.getpgid(child.pid) == child.pid
        assert os.getpgid(child.pid) != os.getpgid(0)
        assert os.getsid(child.pid) == os.getsid(0)
    finally:
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        child.wait(timeout=TREE_SETTLE_SECONDS)


def test_no_process_group_isolation_is_requested_where_groups_do_not_exist(
    windows_platform: FakeKernel32,
) -> None:
    """Pin the Windows request as empty - the job is the mechanism there.

    Windows process groups do not kill a tree, so nothing is asked of the
    launch and containment is the Job Object assigned once the process exists.
    Passing either POSIX placement argument there would be both meaningless and
    a ``ValueError`` from ``Popen``.
    """
    assert driver_module._containment_keywords() == {}
    assert windows_platform.calls == []


@pytest.mark.parametrize(
    ("browser", "service_label"),
    [("chrome", CHROME_SERVICE), ("firefox", FIREFOX_SERVICE)],
)
def test_the_service_carries_the_containment_request_and_nothing_else(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    browser: str,
    service_label: str,
) -> None:
    """Pin the service's arguments: the verified path and ``popen_kw`` only.

    The second argument is deliberately not a browser setting.  It configures
    the *driver executable's* process, so ``Driver.java:29-41``'s bare browser
    construction is untouched - which the bare-construction assertions
    elsewhere in this module continue to check independently.
    """
    browser_key(browser)

    assert driver_module.get_driver() is not None

    service_calls = [
        call for call in driver_harness.recorder.calls if call.target == service_label
    ]

    assert len(service_calls) == 1
    assert len(service_calls[0].args) == 1
    assert service_calls[0].kwargs == {"popen_kw": driver_module._containment_keywords()}


@POSIX_ONLY
def test_the_driver_process_is_contained_when_the_service_starts_it(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    process_tree: ProcessTree,
) -> None:
    """Pin *when* the containment is taken: as the service starts, not later.

    The wrapper the module installs on the service's ``start`` runs between the
    driver executable spawning and the WebDriver session being created, which
    is the only instant at which the process exists and the browser does not -
    and on Windows it is the only instant at which a job can contain the
    browser at all, since a process inherits its parent's job when it is
    created.

    The handle is then adopted by the session, so the two moments are visible
    separately: the capture happens during construction, and by the time
    ``get_driver`` returns it belongs to the session in the slot.
    """
    browser_key("chrome")
    driver_harness.expose_driver_process(process_tree.process)
    process_tree.assert_topology()

    assert driver_module.get_driver() is not None

    service = one_service(driver_harness, CHROME_SERVICE)
    containment = driver_module._containment()

    assert service.start_count == 1
    assert containment is not None
    assert containment.pid == process_tree.process.pid
    assert containment.process_group == process_tree.process_group
    assert containment.process is process_tree.process
    assert containment.job_handle is None

    # Adopted, and therefore no longer staged: nothing a later session adopts
    # can be this tree.
    assert driver_module._take_staged_containment() is None


def test_a_service_with_no_local_driver_process_captures_the_marker() -> None:
    """Pin the absence of a process as a *state*, distinct from "never looked".

    A service that exposes no started local process - one whose start failed,
    or a double - has no tree to take ownership of, and the capture says so
    with :data:`driver._NO_LOCAL_PROCESS` rather than with ``None``.  The
    distinction is the whole point: this marker means "there is demonstrably
    nothing to reclaim" and releases silently, while ``None`` means "no capture
    ever ran" and releases *unconfirmed*.  Collapsing them would turn a session
    nobody contained into a clean teardown.

    Reading through ``getattr`` rather than by attribute access is what makes
    an absent attribute this marker rather than an ``AttributeError`` in the
    middle of a browser launch.
    """
    for service in (
        StubDriver(),
        SimpleNamespace(),
        SimpleNamespace(process=None),
        SimpleNamespace(process=SimpleNamespace(pid="7")),
        SimpleNamespace(process=SimpleNamespace(pid=0)),
    ):
        assert driver_module._capture_containment(service) is driver_module._NO_LOCAL_PROCESS


def test_a_session_whose_service_started_nothing_carries_the_marker(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin the end-to-end shape of that: the marker reaches the slot.

    Nothing about the launch fails - the session is built, set up and returned
    - and what the slot carries is the marker, so teardown knows the capture
    ran and found nothing rather than having to assume one way or the other.
    """
    browser_key("chrome")
    driver_harness.expose_driver_process(None)

    assert driver_module.get_driver() is driver_harness.sessions[0]
    assert driver_module._containment() is driver_module._NO_LOCAL_PROCESS


@POSIX_ONLY
def test_a_process_group_that_cannot_be_resolved_still_yields_the_pid(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the partial capture: no group, but the immediate process is known.

    ``os.getpgid`` fails for a driver executable that died between starting and
    being contained.  The pid is still worth carrying, because the immediate
    process can be waited for even when no group can be signalled, and the gap
    is reported rather than passed over.
    """
    service = SimpleNamespace(process=ProcessDouble(pid=987_654))

    containment = driver_module._capture_containment(service)

    assert containment is not None
    assert containment.pid == 987_654
    assert containment.process_group is None
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


def test_a_captured_handle_no_session_adopted_is_reclaimed_not_dropped(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the orphaned-capture path: released, and reported.

    The driver executable started and the WebDriver handshake then failed, so
    nothing ever adopted the handle.  Dropping it would leave a Windows job
    open for the life of the worker and a POSIX group possibly holding a
    browser, so the next provisioning call releases it - which is also what
    closes the job and terminates whatever is in it.
    """
    staged = driver_module._SessionContainment(4242, process=ProcessDouble())
    driver_module._stage_containment(staged)

    released: list[Any] = []

    def record_release(containment: Any) -> bool:
        """Record the release request instead of signalling a real tree.

        :param containment: The handle the module asked to release.
        :returns: ``True``, the confirmed-release answer.
        """
        released.append(containment)

        return True

    monkeypatch.setattr(driver_module, "_release_containment", record_release)
    driver_harness.expose_driver_process(None)
    browser_key("chrome")

    assert driver_module.get_driver() is driver_harness.sessions[0]
    assert released == [staged]
    assert driver_module._containment() is driver_module._NO_LOCAL_PROCESS
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


@POSIX_ONLY
def test_a_live_process_group_is_not_reported_as_empty(process_tree: ProcessTree) -> None:
    """Pin the sense of the emptiness check - the bug it is easy to write.

    ``killpg(pgid, 0)`` performs the existence check and delivers nothing, so
    it **succeeding** means at least one process is still in the group.  Read
    the other way round, every live tree would be reported as already stopped
    and every teardown would confirm a release that never happened.
    """
    assert process_tree.member_count() == 2
    assert driver_module._process_group_is_empty(process_tree.process_group) is False


@POSIX_ONLY
def test_an_empty_process_group_is_reported_as_empty(process_tree: ProcessTree) -> None:
    """Pin the positive case against a group that really has gone.

    The tree is destroyed and reaped first, so the group id resolves to
    nothing at all - the state a successful teardown leaves behind.
    """
    process_tree.destroy()

    assert driver_module._process_group_is_empty(process_tree.process_group) is True


@POSIX_ONLY
def test_an_unanswerable_process_group_is_never_reported_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the indeterminate answers as *not* clean.

    A group this process may no longer signal is a group that still exists,
    and an error that is neither of those is an unknown.  Reporting either as
    empty would let teardown claim a release it could not observe.
    """
    def refuse(group: int, signal_number: int) -> None:
        raise PermissionError("not permitted")

    monkeypatch.setattr(driver_module.os, "killpg", refuse)

    assert driver_module._process_group_is_empty(4242) is False

    def fail(group: int, signal_number: int) -> None:
        raise OSError("inspection failed")

    monkeypatch.setattr(driver_module.os, "killpg", fail)

    assert driver_module._process_group_is_empty(4242) is False
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


def test_releasing_a_session_with_no_local_process_is_silent_and_successful(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the marker's release as a silent success.

    The capture ran and there was no local driver process, so nothing of this
    session could have survived.  A record here would turn ordinary teardown
    into log noise and would break the negative assertions elsewhere in this
    module.
    """
    assert driver_module._release_containment(driver_module._NO_LOCAL_PROCESS) is True
    assert automation_log.records == []


def test_releasing_a_session_that_was_never_contained_is_unconfirmed(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the third state: nothing was captured, so nothing may be claimed.

    ``None`` in the slot means no capture ever ran for this session - the
    wrapper on the service's ``start`` never executed - so whatever it started
    is unaccounted for.  Reporting success would be teardown confirming the
    disappearance of a tree it never looked at, which is exactly what makes a
    browser holding an authenticated session invisible.
    """
    assert driver_module._release_containment(None) is False
    assert [record.levelno for record in automation_log.records] == [logging.ERROR]


@POSIX_ONLY
def test_a_surviving_browser_tree_is_terminated_and_confirmed_gone(
    process_tree: ProcessTree,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the whole point of containment, against a real surviving tree.

    Two processes are alive and the session was never quit, which is what a
    failed ``quit()`` leaves.  The release signals the *group*, so the forked
    child dies with its leader - a ``terminate()`` of the leader alone would
    leave it running - and the result is the verified state afterwards rather
    than the fact that a signal was sent.

    The group signalled is the driver's own and not this process's, which the
    topology assertion states before anything is signalled: a release that
    reached the caller's group would take the worker down with the browser.
    """
    process_tree.assert_topology()

    containment = driver_module._capture_containment(
        SimpleNamespace(process=process_tree.process)
    )

    assert process_tree.member_count() == 2

    released = driver_module._release_containment(containment)
    time.sleep(TREE_REAP_SECONDS)

    assert released is True
    assert process_tree.member_count() == 0
    assert driver_module._process_group_is_empty(process_tree.process_group) is True
    assert any(record.levelno >= logging.WARNING for record in automation_log.records)


@POSIX_ONLY
def test_a_tree_that_cannot_be_emptied_is_reported_rather_than_assumed_gone(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the unconfirmed release as a failure that says so.

    Signals are accepted and nothing ever leaves the group, which on a real
    host means a process stuck in uninterruptible I/O.  The release must return
    ``False`` and record it at ``ERROR`` - the level
    ``app/logging_config.py`` routes to standard error - because the operator
    has a browser holding an authenticated session and no way to learn it from
    the run's own result.
    """
    monkeypatch.setattr(driver_module.os, "killpg", lambda group, number: None)
    monkeypatch.setattr(driver_module, "_CONTAINMENT_GRACE_SECONDS", 0.05)

    containment = driver_module._SessionContainment(
        4242, process_group=4242, process=ProcessDouble()
    )

    assert driver_module._release_containment(containment) is False
    assert [
        record.levelno
        for record in automation_log.records
        if record.levelno >= logging.ERROR
    ] == [logging.ERROR]


@POSIX_ONLY
def test_a_containment_without_a_group_is_confirmed_only_by_the_exit_it_sees(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the honest answer where no group was ever captured.

    With no group there is nothing to signal and no way to ask about
    descendants, so the only observation available is the immediate process
    exiting - and that observation is the whole of what may be reported.  A
    wait that returns is a confirmed release of the one process this module
    knows about; anything else is unverified, which is what makes
    :func:`driver.quit_driver` raise instead of recording a successful
    teardown over a browser that may still be running.
    """
    exited = ProcessDouble()
    containment = driver_module._SessionContainment(exited.pid, process=exited)

    assert driver_module._release_containment(containment) is True
    assert exited.waits == [driver_module._CONTAINMENT_GRACE_SECONDS]
    assert automation_log.records == []


@POSIX_ONLY
def test_a_driver_process_that_will_not_be_waited_for_is_unverified(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the three unobservable outcomes as unverified, each with a record.

    A wait that timed out saw a process still running; a wait that errored saw
    nothing at all.  Neither is a release, and both are invisible to a caller,
    so each returns ``False`` and leaves a diagnostic naming the process rather
    than being folded into a clean teardown.
    """
    for outcome in (subprocess.TimeoutExpired, OSError, ValueError):
        process = ProcessDouble(outcome=outcome)
        containment = driver_module._SessionContainment(process.pid, process=process)

        assert driver_module._release_containment(containment) is False

    assert [record.levelno for record in automation_log.records] == [
        logging.WARNING,
        logging.ERROR,
    ] * 3


def test_a_windows_session_is_contained_in_a_kill_on_close_job(
    windows_platform: FakeKernel32,
) -> None:
    """Pin the Windows mechanism and the order of its five calls.

    A job limited with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is the only
    Windows facility that terminates a tree, and children inherit it - so the
    browser the driver executable starts is in the job too, which is also why
    the capture has to happen while the service is starting and before any
    browser exists.  The process handle is opened with exactly the two rights
    the assignment needs and is closed again whatever the outcome, so the
    handle cannot leak.
    """
    service = SimpleNamespace(process=ProcessDouble(pid=321))

    containment = driver_module._capture_containment(service)

    assert containment is not None
    assert containment.process_group is None
    assert containment.job_handle == FAKE_JOB_HANDLE
    assert windows_platform.targets() == (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",
    )

    open_access = next(value for name, value in windows_platform.calls if name == "OpenProcess")

    assert open_access == (driver_module._PROCESS_ASSIGN_ACCESS, 321)


@pytest.mark.parametrize(
    ("failure", "expected_calls"),
    [
        ({"create": 0}, ("CreateJobObjectW",)),
        (
            {"limit": 0},
            ("CreateJobObjectW", "SetInformationJobObject", "CloseHandle"),
        ),
        (
            {"open_process": 0},
            ("CreateJobObjectW", "SetInformationJobObject", "OpenProcess", "CloseHandle"),
        ),
        (
            {"assign": 0},
            (
                "CreateJobObjectW",
                "SetInformationJobObject",
                "OpenProcess",
                "AssignProcessToJobObject",
                "CloseHandle",
                "CloseHandle",
            ),
        ),
    ],
)
def test_a_job_that_cannot_be_established_leaks_no_handle_and_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
    failure: dict[str, int],
    expected_calls: tuple[str, ...],
) -> None:
    """Pin every Win32 failure step: no handle leaks, and the gap is recorded.

    Each step can fail independently, and a job half-established is worse than
    none: the handle would keep a kill-on-close limit alive against a process
    that was never assigned to it.  Every path therefore closes what it opened
    and reports ``None``, which is the state the release reads as "never
    contained".
    """
    library = FakeKernel32(**failure)

    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    assert driver_module._assign_kill_on_close_job(321) is None
    assert library.targets() == expected_calls
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


def test_releasing_a_windows_session_closes_the_job_and_waits_for_the_process(
    windows_platform: FakeKernel32,
) -> None:
    """Pin the Windows release: the close *is* the termination, then confirm.

    Closing the last handle terminates every process in the job, so the wait
    that follows is the verification that the closure took effect - not a
    second attempt at stopping anything.
    """
    process = ProcessDouble()
    containment = driver_module._SessionContainment(
        process.pid, job_handle=FAKE_JOB_HANDLE, process=process
    )

    assert driver_module._release_containment(containment) is True
    assert windows_platform.calls == [("CloseHandle", FAKE_JOB_HANDLE)]
    assert process.waits == [driver_module._CONTAINMENT_GRACE_SECONDS]


def test_a_windows_session_that_was_never_contained_reports_it(
    windows_platform: FakeKernel32,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the uncontained Windows session as an unverified release.

    With no job there is no way to reach the tree and no way to confirm it
    stopped, and on that platform there is no group to fall back on.  Reporting
    ``False`` is what turns it into a visible teardown failure instead of a
    silent one.
    """
    process = ProcessDouble()
    containment = driver_module._SessionContainment(process.pid, process=process)

    assert driver_module._release_containment(containment) is False
    assert [
        record.levelno
        for record in automation_log.records
        if record.levelno >= logging.ERROR
    ] == [logging.ERROR]


def test_a_windows_process_that_survives_its_job_is_reported(
    windows_platform: FakeKernel32,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the Windows verification failing when the process outlives the job.

    A process still running after its kill-on-close job was closed is the one
    outcome that platform's containment cannot explain away, so it is an
    ``ERROR`` and an unconfirmed release.
    """
    process = ProcessDouble(outcome=subprocess.TimeoutExpired)
    containment = driver_module._SessionContainment(
        process.pid, job_handle=FAKE_JOB_HANDLE, process=process
    )

    assert driver_module._release_containment(containment) is False
    assert [
        record.levelno
        for record in automation_log.records
        if record.levelno >= logging.ERROR
    ] == [logging.ERROR]


@POSIX_ONLY
def test_teardown_reclaims_the_process_tree_and_clears_the_slot(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    process_tree: ProcessTree,
) -> None:
    """Pin the whole teardown path over a real tree, end to end.

    The session's ``quit()`` is a double and does nothing to the processes -
    which is precisely the shape of a ``quit()`` that "succeeded" while leaving
    the browser running.  Teardown must therefore reclaim the tree on its own,
    confirm it, clear the slot, and raise nothing.
    """
    browser_key("chrome")
    driver_harness.expose_driver_process(process_tree.process)
    session = driver_harness.queue_session(StubDriver())

    assert driver_module.get_driver() is session
    assert session.operations() == SETUP_OPERATIONS
    assert process_tree.member_count() == 2

    driver_module.quit_driver()
    time.sleep(TREE_REAP_SECONDS)

    assert process_tree.member_count() == 0
    assert driver_module._session() is None
    assert driver_module._containment() is None


def test_a_tree_that_outlives_a_clean_quit_becomes_a_teardown_failure(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the failure the session itself cannot report.

    ``quit()`` returning means the driver executable accepted the request, not
    that the browser is gone.  A tree that cannot be confirmed stopped is
    raised as :exc:`driver.DriverTeardownError`, because silence would record
    teardown as successful over a live authenticated browser.  The slot is
    still emptied, so the next scenario is not handed the dead session.
    """
    browser_key("chrome")
    monkeypatch.setattr(driver_module, "_release_containment", lambda containment: False)

    assert driver_module.get_driver() is not None

    with pytest.raises(driver_module.DriverTeardownError):
        driver_module.quit_driver()

    assert driver_module._session() is None
    assert driver_module._containment() is None


def test_an_unconfirmed_tree_never_displaces_a_failing_quit(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the precedence between the two teardown failures.

    A ``quit()`` that raised is already reporting the teardown failure, and its
    exception is what a caller must see - replacing it with the containment
    diagnosis would hide the original cause behind a consequence.  The
    containment result is recorded in the log instead, which is why the check
    sits after the ``try``/``finally`` and not inside it.
    """
    browser_key("chrome")
    failure = RuntimeError("session is gone")
    session = driver_harness.queue_session(FailingQuitSession(failure))
    monkeypatch.setattr(driver_module, "_release_containment", lambda containment: False)

    assert driver_module.get_driver() is session

    with pytest.raises(RuntimeError) as raised:
        driver_module.quit_driver()

    assert raised.value is failure
    assert driver_module._session() is None


def test_provisioning_runs_with_a_search_path_the_run_cannot_choose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the narrowed ``PATH`` around a provisioning call.

    The provisioning library resolves programs by bare name on paths this
    module does not control - ``GeckoDriverManager`` consults
    ``platform.processor()`` to recognise Linux on ARM, and on Python 3.14 that
    runs ``uname -p`` through whatever ``PATH`` says - so a planted executable
    earlier in the search order would execute inside the call.  The value is
    narrowed to system directories for its duration and restored afterwards,
    and it is narrowed rather than emptied because a legitimate lookup must
    still succeed.
    """
    monkeypatch.setenv("PATH", "/tmp/planted:/usr/bin")

    with driver_module._trusted_provisioning_environment():
        assert os.environ["PATH"] == driver_module._TRUSTED_PATH
        assert "/tmp/planted" not in os.environ["PATH"]

    assert os.environ["PATH"] == "/tmp/planted:/usr/bin"


def test_the_search_path_is_restored_when_provisioning_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin restoration on the exception path.

    A run that provisioned once must look exactly like a run that never did,
    or the narrowing would leak into the browser launch that follows and into
    every step after it.
    """
    monkeypatch.setenv("PATH", "/tmp/planted:/usr/bin")

    with pytest.raises(RuntimeError), driver_module._trusted_provisioning_environment():
        raise RuntimeError("install failed")

    assert os.environ["PATH"] == "/tmp/planted:/usr/bin"


# --------------------------------------------------------------------------
# The scenario boundary
#
# ``features/environment.py`` is the only caller of the lifecycle, and AAP
# 0.3.3 requires it, this module and ``driver.py`` to state one contract.  The
# tests below are here rather than in a module of their own because what they
# assert *is* that contract: the hook's teardown is the driver module's
# containment, and neither half means anything without the other.
# --------------------------------------------------------------------------


class ScenarioDouble:
    """A finished scenario, exposing only what the hook reads.

    :param failed: What ``status.has_failed()`` reports.
    :param name: The scenario name.
    :param filename: Its feature file.
    :param line: Its line in that file.
    """

    __slots__ = ("filename", "line", "name", "status")

    def __init__(
        self,
        *,
        failed: bool = False,
        name: str = "User can log in",
        filename: str = "features/Login.feature",
        line: int = 13,
    ) -> None:
        self.name = name
        self.filename = filename
        self.line = line
        self.status = SimpleNamespace(has_failed=lambda: failed)


def test_the_scenario_hook_leaves_no_reference_to_a_closed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the hook's teardown as unconditional and reference-clearing.

    ``Hooks.java:17`` sits outside the failure test, so teardown runs for every
    scenario; and the context's own reference is dropped afterwards so no later
    reader can reach a session that has been quit.
    """
    from features import environment

    quits: list[int] = []
    monkeypatch.setattr(environment, "quit_driver", lambda: quits.append(1))

    context = SimpleNamespace(driver=StubDriver())

    environment.after_scenario(context, ScenarioDouble())

    assert quits == [1]
    assert context.driver is None


def test_the_scenario_hook_reports_a_failed_teardown_against_its_scenario(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the one thing the hook knows that the driver module does not.

    A teardown failure means a browser may still be running with the system
    under test's authenticated session in it, and ``quit_driver`` cannot say
    *which scenario* that browser belongs to.  The hook adds exactly that,
    carries the exception so the reclamation diagnostic can be read alongside
    it, and then re-raises - behave turns the propagated failure into
    ``HOOK-ERROR in after_scenario`` with that scenario recorded as
    ``hook_error``, which is how the run surfaces it at all.
    """
    from features import environment

    failure = driver_module.DriverTeardownError("tree not confirmed stopped")

    def fail() -> None:
        raise failure

    monkeypatch.setattr(environment, "quit_driver", fail)

    context = SimpleNamespace(driver=StubDriver())

    with caplog.at_level(logging.WARNING), pytest.raises(driver_module.DriverTeardownError) as raised:
        environment.after_scenario(context, ScenarioDouble(name="User can log out", line=37))

    assert raised.value is failure
    assert context.driver is None

    reported = [
        record
        for record in caplog.records
        if record.name == environment.__name__ and record.levelno == logging.WARNING
    ]

    assert len(reported) == 1
    assert reported[0].exc_info is not None
    assert "User can log out" in reported[0].getMessage()
    assert "features/Login.feature:37" in reported[0].getMessage()



@POSIX_ONLY
def test_the_win32_binding_is_absent_off_windows() -> None:
    """Pin the platform guard on the only Win32 seam.

    Every other Windows test stands a double in front of this function, so
    this is what checks the real one: off Windows it answers ``None`` without
    importing ``ctypes.WinDLL``, which does not exist there. The branches that
    read it treat ``None`` as "no containment available" and say so rather
    than failing.
    """
    assert driver_module._kernel32() is None


def test_no_job_is_attempted_without_a_win32_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the behaviour when Win32 itself is unavailable.

    Nothing is attempted and ``None`` is returned, which the release reads as
    a session that was never contained - the state it reports rather than
    passes over.
    """
    monkeypatch.setattr(driver_module, "_kernel32", lambda: None)

    assert driver_module._assign_kill_on_close_job(321) is None


def test_a_win32_error_while_building_a_job_closes_it_and_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the error path of the job builder, handle included.

    A Win32 call that raises rather than returning a failure code leaves a
    created job behind, and a job holding a kill-on-close limit against
    nothing is exactly what must not be leaked. The handle is closed, the
    failure is recorded with the exception attached, and ``None`` is returned.
    """

    class ExplodingKernel32(FakeKernel32):
        """A binding whose limit call raises instead of returning a code."""

        def SetInformationJobObject(
            self, job: int, info_class: int, info: Any, length: int
        ) -> int:
            """Record the attempt, then fail the way Win32 can."""
            self.calls.append(("SetInformationJobObject", job))
            raise OSError("win32 failure")

    library = ExplodingKernel32()
    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    assert driver_module._assign_kill_on_close_job(321) is None

    # One close, not two: the failure came before the process handle was
    # opened, so there is no second handle for the ``finally`` to release.
    assert library.targets() == (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "CloseHandle",
    )
    assert [record.exc_info is not None for record in automation_log.records] == [True]


@POSIX_ONLY
def test_an_already_empty_group_is_released_without_a_signal(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the ordinary path: a ``quit()`` that worked leaves nothing to do.

    This is every teardown of every passing scenario, so it must send no
    signal and say nothing, while still waiting on the driver executable so it
    is not left a zombie.
    """
    process = ProcessDouble()
    containment = driver_module._SessionContainment(
        process.pid, process_group=os.getpgid(0), process=process
    )
    empty = driver_module._SessionContainment(
        process.pid, process_group=999_999_999, process=process
    )

    assert containment.process_group == os.getpgid(0)
    assert driver_module._release_containment(empty) is True
    assert process.waits == [driver_module._CONTAINMENT_GRACE_SECONDS]
    assert automation_log.records == []


@POSIX_ONLY
def test_a_group_that_disappears_between_signals_stops_the_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the escalation stopping the moment the group is gone.

    The group is non-empty when teardown looks, and gone by the time the
    signal lands - a tree that was exiting on its own. Continuing to a
    ``SIGKILL`` would signal an id that no longer belongs to it, so the loop
    breaks on the first "no such group".
    """
    signals: list[int] = []

    def vanish(group: int, number: int) -> None:
        if number == 0:
            signals.append(number)
            return

        signals.append(number)
        raise ProcessLookupError("gone")

    monkeypatch.setattr(driver_module.os, "killpg", vanish)

    containment = driver_module._SessionContainment(
        4242, process_group=4242, process=ProcessDouble()
    )

    assert driver_module._release_containment(containment) is False
    assert signals.count(signal.SIGKILL) == 0


@POSIX_ONLY
def test_a_group_that_cannot_be_signalled_stops_the_escalation(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the refusal path: reported once, and not retried harder.

    A group this process may not signal will not yield to a second, stronger
    signal either, so the attempt stops and the unconfirmed state is what the
    release returns.
    """
    def refuse(group: int, number: int) -> None:
        if number == 0:
            return

        raise PermissionError("not permitted")

    monkeypatch.setattr(driver_module.os, "killpg", refuse)

    containment = driver_module._SessionContainment(
        4242, process_group=4242, process=ProcessDouble()
    )

    assert driver_module._release_containment(containment) is False
    assert [record.levelno for record in automation_log.records] == [
        logging.WARNING,
        logging.WARNING,
        logging.ERROR,
    ]


def test_a_windows_job_that_cannot_be_closed_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the Windows close failing: unconfirmed, and recorded.

    The close is the termination there, so a close that raised means nothing
    was terminated and nothing can be confirmed.
    """

    class UnclosableKernel32(FakeKernel32):
        """A binding whose ``CloseHandle`` raises."""

        def CloseHandle(self, handle: int) -> int:
            """Record the attempt, then fail."""
            self.calls.append(("CloseHandle", handle))
            raise OSError("win32 failure")

    library = UnclosableKernel32()
    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    containment = driver_module._SessionContainment(
        321, job_handle=FAKE_JOB_HANDLE, process=ProcessDouble()
    )

    assert driver_module._release_containment(containment) is False
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


def test_a_win32_error_before_a_job_exists_closes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the other error path of the job builder: there is no handle yet.

    ``CreateJobObjectW`` itself raised, so no job was created and there is
    nothing to close - and attempting a close over the value it did not return
    would be a call on a handle this process never owned.  The failure is
    recorded with its exception and ``None`` is returned, which the release
    reads as a session that was never contained.
    """

    class UncreatableKernel32(FakeKernel32):
        """A binding whose job creation raises instead of returning a code."""

        def CreateJobObjectW(self, attributes: Any, name: Any) -> int:
            """Record the attempt, then fail the way Win32 can."""
            self.calls.append(("CreateJobObjectW", name))

            raise OSError("win32 failure")

    library = UncreatableKernel32()
    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    assert driver_module._assign_kill_on_close_job(321) is None
    assert library.targets() == ("CreateJobObjectW",)
    assert [record.exc_info is not None for record in automation_log.records] == [True]


def test_a_win32_handle_win32_declines_to_close_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the status of ``CloseHandle`` as read rather than discarded.

    ``CloseHandle`` reports whether it closed anything, and for the containment
    job the close *is* the termination - so a status nobody reads is the
    difference between "the tree was killed" and "nothing happened, and the
    handle is leaked for the life of the worker".  A refusal is therefore an
    unconfirmed release with a record, not a silent success.
    """
    library = FakeKernel32(close=0)

    monkeypatch.setattr(driver_module, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(driver_module, "_kernel32", lambda: library)

    containment = driver_module._SessionContainment(
        321, job_handle=FAKE_JOB_HANDLE, process=ProcessDouble()
    )

    assert driver_module._release_containment(containment) is False
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


def test_every_win32_entry_point_is_declared_pointer_sized() -> None:
    """Pin the ctypes prototypes, which decide whether a handle survives a call.

    An undeclared ``ctypes`` function returns ``c_int``: 32 bits.  A Windows
    ``HANDLE`` is pointer-sized, so on a 64-bit host - every Windows host this
    port targets - an undeclared ``CreateJobObjectW`` or ``OpenProcess`` hands
    back a **truncated** handle that still tests as non-zero, and the code
    believes it holds a job it does not hold.  The same truncation applies to a
    handle passed back in as an argument.

    The declaration is asserted here against the real ``ctypes.wintypes``, over
    a double standing in for the library: both directions of every entry point
    that carries a handle, and the *width* of each, which is the property that
    actually matters.
    """
    entry_points = (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",
    )
    library = SimpleNamespace(
        **{name: SimpleNamespace(restype=None, argtypes=None) for name in entry_points}
    )

    assert driver_module._win32_prototypes(library, wintypes) is library

    handle_size = ctypes.sizeof(ctypes.c_void_p)

    for name in entry_points:
        entry = getattr(library, name)

        assert entry.restype is not None
        assert entry.argtypes is not None

    assert library.CreateJobObjectW.restype is wintypes.HANDLE
    assert library.CreateJobObjectW.argtypes == [wintypes.LPVOID, wintypes.LPCWSTR]
    assert ctypes.sizeof(library.CreateJobObjectW.restype) == handle_size

    assert library.OpenProcess.restype is wintypes.HANDLE
    assert library.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert ctypes.sizeof(library.OpenProcess.restype) == handle_size

    assert library.SetInformationJobObject.restype is wintypes.BOOL
    assert library.SetInformationJobObject.argtypes[0] is wintypes.HANDLE

    assert library.AssignProcessToJobObject.restype is wintypes.BOOL
    assert library.AssignProcessToJobObject.argtypes == [wintypes.HANDLE, wintypes.HANDLE]

    assert library.CloseHandle.restype is wintypes.BOOL
    assert library.CloseHandle.argtypes == [wintypes.HANDLE]


@WINDOWS_ONLY
def test_the_real_win32_binding_arrives_with_its_prototypes_declared() -> None:
    """Pin the real seam on the one platform that has it.

    Everywhere else this module stands a double in front of
    :func:`driver._kernel32`; here the genuine library is loaded, which is what
    checks that the prototypes are applied to it rather than only to a double.
    """
    library = driver_module._kernel32()

    assert library is not None
    assert library.CreateJobObjectW.restype is wintypes.HANDLE
    assert library.CloseHandle.argtypes == [wintypes.HANDLE]


def test_a_windows_job_closed_over_no_known_process_is_confirmed(
    windows_platform: FakeKernel32,
) -> None:
    """Pin the Windows release with nothing left to wait on.

    The job's members were terminated by the close, and with no process object
    there is nothing further to observe - so the release is confirmed rather
    than reported as unknown.
    """
    containment = driver_module._SessionContainment(321, job_handle=FAKE_JOB_HANDLE)

    assert driver_module._release_containment(containment) is True
    assert windows_platform.calls == [("CloseHandle", FAKE_JOB_HANDLE)]


def test_a_windows_process_that_cannot_be_waited_for_is_unconfirmed(
    windows_platform: FakeKernel32,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the Windows wait erroring as an unknown rather than a success.

    A wait that could not be performed observed nothing, and a release nobody
    observed is not a release.
    """
    for outcome in (OSError, ValueError):
        process = ProcessDouble(outcome=outcome)
        containment = driver_module._SessionContainment(
            321, job_handle=FAKE_JOB_HANDLE, process=process
        )

        assert driver_module._release_containment(containment) is False

    assert [record.levelno for record in automation_log.records] == [logging.WARNING] * 2


def test_a_platform_claiming_windows_without_win32_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the seam's own failure: Windows said, Win32 absent.

    A platform that identifies as Windows and cannot supply ``kernel32`` has no
    containment at all.  That is reported rather than raised, because a browser
    run must still start - what changes is that the release afterwards reports
    itself unconfirmed instead of claiming success.

    Both halves are faked, and that is what makes this test mean the same thing
    everywhere: ``os.name`` says Windows, and the loader raises.  Off Windows
    ``ctypes.WinDLL`` does not exist, so ``raising=False`` installs the failing
    stand-in; on Windows it replaces the real loader for the duration.  A test
    that relied on ``WinDLL`` being absent would pass for the wrong reason on
    Linux and fail outright on the platform it is about.
    """

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        """Fail the way a missing or unloadable ``kernel32`` fails.

        :param args: Ignored loader arguments.
        :param kwargs: Ignored loader arguments.
        :returns: Never returns.
        :raises OSError: Always.
        """
        raise OSError("kernel32 is unavailable")

    monkeypatch.setattr(driver_module.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", unavailable, raising=False)

    assert driver_module._kernel32() is None
    assert [record.levelno for record in automation_log.records] == [logging.WARNING]


@POSIX_ONLY
def test_a_containment_with_neither_a_group_nor_a_process_is_unverified(
    automation_log: AutomationLogCapture,
) -> None:
    """Pin the emptiest containment there is as unverified, not as released.

    A record can carry a pid and nothing else - a driver executable that died
    between starting and being contained.  There is no group to signal and no
    process to wait on, so *nothing was observed*, and the release says so:
    answering ``True`` here would have teardown confirm the disappearance of a
    tree it never looked at, which is the one thing the verification exists to
    prevent.  It still must not fail trying.
    """
    containment = driver_module._SessionContainment(4242)

    assert driver_module._release_containment(containment) is False
    assert [record.levelno for record in automation_log.records] == [logging.ERROR]
