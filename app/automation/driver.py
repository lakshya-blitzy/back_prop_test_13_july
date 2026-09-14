r"""Worker-local WebDriver lifecycle - the single owner of every browser session.

The port of the Java ``Driver`` utility (``Driver.java:17-55``), which AAP
0.3.3 names as the one place a browser session is ever created or disposed of.
Two public functions, one per static method: :func:`get_driver` ports
``Driver.getDriver()`` (``Driver.java:21-45``) and :func:`quit_driver` ports
``Driver.closeDriver()`` (``Driver.java:50-55``).

The lifecycle contract AAP 0.3.3 states once, repeated identically in
``features/environment.py`` and ``tests/test_driver.py``: each worker process
holds one slot for a driver, ``before_scenario`` fills an empty slot, and
``after_scenario`` captures failure evidence, calls ``quit()`` and clears it.
Exactly one live session exists per worker at a time, every scenario gets a
fresh one, and nothing touches a driver after ``quit()``.  That boundary is
``features/environment.py``'s: AAP 0.6 fixes capture as happening once, on
failure, before the quit, and nothing is captured or written here.

``Driver.java:17`` keeps its session in an ``InheritableThreadLocal``; this
port shards scenarios across a process pool (AAP deviation 4) and runs the
engine single-threaded per worker, so the slot below holds one session each.

Behaviour preserved rather than improved: ``Driver.java:29-41`` constructs
``"chrome"`` and ``"firefox"`` bare - no capability object, preference or
locale - under no ``default:`` label, so an unrecognised, mis-cased or padded
name is not validated here and fails at first driver use, as AAP 0.4.1 rules;
no page-load or script timeout is set; and nothing bounds provisioning or a
command in wall-clock time, an inherited limit left in place.  The one
correction is AAP deviation 5, commented at its call site, where
``Driver.java:37`` provisions the *Chrome* binary inside the Firefox branch.

``features/environment.py`` owns that boundary.  It is the port of
``Hooks.teardownScenario`` (``Hooks.java:11-18``), and what it reproduces is
what that hook *intended* rather than what it did: ``Hooks.java:5`` imports
``org.junit.After`` instead of ``io.cucumber.java.After``, so the engine never
invoked it - corroborated by the committed baseline artifact, which records a
failed scenario with no attachment and no after-hook entry.  Registering it
for real is AAP deviation 6, and it is what makes the per-scenario half of the
contract above real.  This module supplies the two calls that hook makes; it
never installs itself anywhere and never decides when a scenario begins or
ends.

Why one slot per worker is exactly right
----------------------------------------
``Driver.java:17`` holds its session in an ``InheritableThreadLocal``, because
``pom.xml:21-29`` asked surefire for in-JVM concurrency
(``<parallel>methods</parallel>`` with ``<useUnlimitedThreads>true``, and a
``<threadCount>4</threadCount>`` commented out at ``pom.xml:24``).  The port's
concurrency is **process** isolation instead: its run-orchestration service
shards scenarios across a process pool, which AAP records as deviation 4 - a
departure, not a claim of equivalence, since no in-process thread model
applies to Python Selenium sessions.  The Gherkin engine then runs
single-threaded inside each worker, so the thread-local slot below holds
exactly one session per worker process, which is precisely the invariant the
contract states.  A plain module global would be the same object under a
thread-based runner and is deliberately not used; nothing here is keyed on
state shared across processes, because a process pool has none.

Selenium 3 to 4: the one place this is not a literal transcription
------------------------------------------------------------------
``pom.xml:36-40`` pins ``selenium-java`` 3.141.59, where ``new ChromeDriver()``
picked its binary up from a system property that
``WebDriverManager...setup()`` had set.  This port is written against
``selenium`` 4.48.0 and ``webdriver-manager`` 4.1.2 (AAP 0.5.1, exact pins),
where the driver path is handed over explicitly: a ``Service`` object carries
it into the constructor.  The path this module hands over is one it has
verified, resolved by the provisioning policy below - a pre-provisioned system
binary first, ``.install()`` only as a hardened fallback.  The observable
result is unchanged - the correct binary is in place before the browser starts
- and ``README.md:53``'s manual prerequisite, *"Browser driver (make sure you
have your desired browser driver and class path is set)"*, is still satisfied
by code rather than by the reader.  The *browser* itself must still be
installed (AAP 0.8); only the driver binary is provisioned here.

Parity map: every behaviour below is anchored in a Java line
------------------------------------------------------------
========================  ==========================================
``Driver.java``           Behaviour reproduced here
========================  ==========================================
``:17``                   One session slot per worker (:data:`_holder`)
``:22``                   Create on demand: construct only when empty
``:27``                   Read the ``browser`` key; it may be unset
``:29-42``                Two branches, ``"chrome"`` and ``"firefox"``,
                          and no default branch whatever
``:31-32``, ``:38``       Resolve and verify the binary, then construct
``:32``, ``:38``          Store into the slot *before* touching it
``:33-34``, ``:39-40``    Maximize, *then* a 10-second implicit wait
``:45``                   Return the slot unconditionally - possibly
                          ``None``
``:50-55``                Quit and clear, guarded: a no-op when empty
========================  ==========================================

Deviation 5 - the corrected defect
----------------------------------
``Driver.java:37`` calls ``WebDriverManager.chromedriver().setup()`` inside
the *Firefox* branch, immediately before constructing a ``FirefoxDriver``.
AAP Conflict 6 corrects it here, because *"reproducing the first would
provision the wrong binary"*: the Firefox branch below provisions with
``GeckoDriverManager``.  That single line is inventoried as AAP deviation 5,
carries a comment saying so at its call site, and AAP 0.8 notes each such
correction "is individually reversible" - the comment is what keeps that true.

Driver provisioning policy
--------------------------
Whatever path reaches a ``Service`` is *executed*: Selenium starts it as a
subprocess with this worker's privileges.  So every route to that path passes
through one gate, :func:`_verified_driver_path`, and nothing a ``Service``
receives has skipped it.

**Verification.**  A candidate is accepted only when it is a non-empty
absolute path; ``os.lstat`` reports a regular file, so a symbolic link, a
FIFO, a directory and a device node are refused without being followed; and
the file is executable by this process.  On POSIX three further requirements
apply: neither the file nor its parent directory is writable by its group or by
everyone, and the file's owner is root or this process's effective user.
Anything else raises :exc:`DriverProvisioningError`, whose message names the
browser and one of a fixed set of reason classes and carries no path, no
environment value and no vendor output.

**What Windows is held to, stated rather than implied.**  Those three
requirements are POSIX mode bits and a numeric owner, and Windows has neither:
it expresses permission as an access-control list that ``os.lstat`` does not
report, and answers ``st_uid`` as ``0`` for every file.  This module therefore
does **not** evaluate who else may write a driver on Windows, and claims no
equivalent check - what stands there instead is *where* it looks: the searched
locations are ``Program Files`` style directories that only an administrator
can write.  The first three requirements apply on every platform.

**Three platforms, and why that is a branch rather than a detail.**  AAP 0.8
requires Chrome and Firefox on Windows, Linux and macOS, so every
platform-dependent table below is selected by :func:`_for_platform` with three
arms.  ``os.name`` cannot express that - it is ``"posix"`` for Linux and macOS
alike - while those two agree on almost nothing here: macOS keeps its browsers
inside ``/Applications`` bundles and its command-line tools under
``/opt/homebrew/bin`` or ``/usr/local/bin``, and has no ``/opt/google`` or
``/usr/lib/firefox`` at all.  Giving macOS the Linux tables would leave its
browser probe finding nothing, which is not harmless: the provisioning library
then asks for the *latest* driver instead of the one matching the installed
browser, and a driver newer than the browser refuses to drive it.

**Route one - a pre-provisioned binary.**  ``chromedriver`` and
``geckodriver`` are looked for under their fixed names in
:data:`_SYSTEM_DRIVER_DIRECTORIES` - absolute, non-user-writable system
locations for the platform in use - and the first candidate that verifies is
used.  No subprocess, no
network call and no ``PATH`` lookup is involved: resolving a program *name*
through ``PATH`` is precisely what lets an earlier directory supply a
different binary, so this module never does it.  An operator who installs the
driver into one of those directories never reaches route two, which is what
makes the suite runnable with no provisioning network at all.

**Route two - the managers, hardened.**  AAP 0.5.2 names
``ChromeDriverManager`` and ``GeckoDriverManager`` as this port's provisioning
entry points, so they stay the fallback, with five constraints around
``.install()``:

* ``webdriver_manager.core.config`` calls ``load_dotenv()`` at import, so a
  hidden ``.env`` file anywhere from the working directory upward has already
  pushed its values into ``os.environ`` before anything here runs.  The
  library then reads its trust settings from the environment at call time:
  ``WDM_SSL_VERIFY`` ("0" or "false" turns certificate verification off for
  its downloads) and ``WDM_LOCAL`` (which relocates the driver cache, and
  which ``DriverCacheManager.__init__`` applies *after* an explicitly passed
  cache root, so it overrides one).  :func:`_trusted_provisioning_environment`
  overrides both in ``os.environ`` for the duration of the call - overriding
  there is exactly what rejects hidden ``.env`` control, because that is where
  those values already are - and restores the previous state on every path,
  exception included.  The same window removes the proxy and
  certificate-authority variables the HTTP stack beneath the library honours,
  which are the other half of "where did this binary come from": provisioning
  therefore goes direct and trusts the system store alone, and an environment
  that needs a proxy or a private authority has to supply a pre-provisioned
  driver over route one instead of having the decision quietly taken for it.
  ``GH_TOKEN`` is deliberately left alone: it is an operator-supplied
  credential for the geckodriver release lookup, not a trust-policy control.
* **The cache is private and verified, not wherever ``HOME`` points.**  With
  no explicit root the library computes ``<HOME>/.wdm``, and ``HOME`` is
  legitimately preserved in a worker's environment, so an ambient value there
  would relocate every cache read and write - and a cache entry is executed.
  :func:`_provisioning_manager` therefore passes a ``DriverCacheManager``
  rooted at :func:`_private_cache_root`: a fixed, user-scoped directory under
  the system temporary directory, created ``0700``, and verified before use
  with a no-follow check that it is a directory, owned by this account, and
  writable by nobody else.  A root that exists and fails that check is refused
  rather than used, widened or replaced.
* **Nothing is unpacked into it unchecked.**  ``file_manager=`` carries
  :class:`_ValidatingFileManager`, which validates every archive member before
  a byte is written and extracts tar members through Python's own hardened
  filter; see its class docstring for the library line that makes this
  necessary.
* The library's browser-version probe builds its command from bare program
  names and runs it through a shell (``core/utils.read_version_from_cmd``
  calls ``subprocess.Popen`` with its ``shell`` argument enabled), so a
  ``google-chrome`` or ``firefox`` earlier in ``PATH`` would be executed.  Both managers accept an
  ``os_system_manager``, so :class:`_VerifiedSystemManager` replaces that
  probe with absolute, verified binaries run with ``shell=False``, no
  inherited standard input, an explicit timeout and a strict anchored
  pattern - and returns ``None`` on any failure, which is what the library
  itself does when its probe fails and leaves it to resolve a version its own
  way.
* The path ``.install()`` returns is verified before it goes any further.
  ``webdriver-manager`` 4.1.2 checks no checksum and no signature, extracts
  what it downloaded, and trusts cache metadata under a user-writable home
  directory, so its answer is a candidate and never an authority.

**The environment cannot redirect the binary either.**
``selenium/webdriver/common/service.py:79`` computes
``self._path = self.env_path() or executable_path``, where ``env_path()``
returns ``os.getenv(self.DRIVER_PATH_ENV_KEY)`` - defaulted to
``"SE_CHROMEDRIVER"`` at ``chromium/service.py:50`` and to
``"SE_GECKODRIVER"`` at ``firefox/service.py:49``, each written
``key = key or "SE_..."`` so passing ``None`` does not disable it.  An ambient
variable would therefore be executed *instead of* the verified path, so
:func:`_bind_verified_path` clears that key on the constructed service and
assigns the verified path through the service's own setter.

**What this policy does not claim.**  Route two verifies *where* a driver sits
and *who may write it*, and validates what an archive may contain before
unpacking it - but it attests nothing about where the bytes came from.  No
upstream digest and no signature is pinned anywhere in this port, so a
compromised upstream release, or a cache entry rewritten by this same account,
whose file still lands as a regular, non-world-writable, executable file under
the private root is executed.  Closing that needs a pinned digest, which means
a configuration surface for one; that is a new AAP deviation (0.5.1 pins
packages, not driver binaries) and this module asserts none.  Until it exists,
route one - an administrator-installed driver in a system directory - is the
configuration to prefer, and it is the only one that reaches no network at all.

What this module deliberately does not do
-----------------------------------------
Each item is behaviour to preserve, not an omission:

* **No default branch, and no validation of the browser name.**
  ``Driver.java:29-42`` has two ``case`` labels and nothing else, so an
  unrecognised value leaves the slot empty and the scenario fails at first use
  - AAP 0.4.1: *"Any other value fails at first driver use, as today."*  There
  is no fallback browser and no substitution; an unrecognised value provisions
  nothing, constructs nothing and raises nothing, :exc:`DriverProvisioningError`
  included, since that exception belongs to a branch that matched.
  ``app/config.py`` takes the same position on the value it returns.
* **No case conversion and no trimming of the browser name.**  A Java
  ``switch`` over a ``String`` compares with ``equals``, so ``"Chrome"``,
  ``"CHROME"`` and ``" chrome"`` all fail to match today.  Normalizing would
  make this port accept input the source rejects, which is a change in
  behaviour rather than parity.
* **No third browser.**  The specification's F-003 mentions Internet Explorer,
  but ``Driver.java:29-41`` constructs Chrome and Firefox only, so AAP 0.1.3's
  precedence rule makes that mention aspirational and AAP 0.2.2 puts it out of
  scope.  Add no branch for it.
* **Both browsers are constructed bare.**  No capability object, no switch
  argument, no preferences, no browser data directory and no regional setting
  is passed, because ``Driver.java:29-41`` passes none.  AAP 0.6 leaves the
  question of how the French required-field message of the ``@UPGN-288``
  outline is arranged deliberately open, and 0.8 confirms the port "carries
  the string byte-for-byte ... and adds no key" for it.  A capability layer
  here would silently settle a question the AAP reserves for the user, and
  would change whether that outline passes.
* **No capture of failure evidence, and no toggle for it.**  Capture belongs
  to ``features/environment.py``'s ``after_scenario``, which per AAP 0.6 must
  do it "only on failure, once, before the driver is quit" - so a caller
  captures *first* and calls :func:`quit_driver` *after*.  Nothing here reads
  or writes a report artifact.  ``README.md:42-43`` advertises capture "for
  your tests if you enable it", which AAP 0.6 rules aspirational - *"No enable
  flag is added"* - so this module gains no toggle and the configuration
  surface stays at six keys.
* **No browser timeout other than the implicit wait.**  ``Driver.java`` sets
  neither a page-load nor a script timeout, so neither is set here.  Explicit
  waits are a separate module with a per-call-site timeout.  The one wall-clock
  bound this module does apply is on the browser-version probe of route two
  above, which is a bound on a local subprocess and not a browser setting.

Nothing bounds provisioning or a command in wall-clock time
-----------------------------------------------------------
Stated because it is a real limit of this module rather than an oversight in
it: ``webdriver-manager``'s provisioning HTTP - route two's ``.install()`` -
carries no application timeout, and Selenium's transport passes no timeout of
its own, so its socket deadline is whatever ``socket.getdefaulttimeout()``
returns, which is ``None`` unless something in the process has set it.  Both
``.install()`` and any subsequent WebDriver command can therefore block
indefinitely, and the port supplies no parent cancellation boundary that would
end such a wait from outside.  Route one reaches no network at all, so a
pre-provisioned driver is not exposed to this at provisioning time.

This is inherited, not introduced: ``Driver.java:29-41`` constructs both
browsers bare and configures no transport, and the two settings it does apply
are reproduced exactly (maximize, then a 10-second implicit wait, which is a
per-command element-lookup budget and not a network bound).  It is
deliberately not changed here.  A download limit, a socket or transport
timeout, a ``ClientConfig``, a retry policy or a cancellation policy would all
be behaviour this port added on its own, and AAP 0.1.3 owns "every deviation
in this plan" with an inventory that closes at item 19 - so any wall-clock
bound over provisioning or over a command requires user authorization as a new
deviation, and none is asserted by this module.

So the position this module ships is an accepted risk, stated rather than
silently taken: an unreachable driver-binary host or an unresponsive browser
stalls the worker that hit it for as long as the operator lets the run stand,
and a run's own wall-clock is the only thing that ends it.  Two changes would
remove that, and both are outside this module: a provisioning and transport
bound, which is a ``ClientConfig`` on each constructor below plus a download
timeout on route two's manager call, and a parent cancellation boundary in
``app/services/test_run_service.py``, which owns the worker processes and is
the only place a stalled child can be terminated and its session cleaned up.
Neither is in place and neither is authorized: this paragraph records that
state exactly, and asserts no change beyond it.

Import boundary (AAP 0.4.2)
---------------------------
This module and its two siblings are the only place ``selenium`` is imported,
and this is the only module in the port that imports ``webdriver_manager``
(AAP 0.5.2).  Absorbing both dependencies here is what lets every step module
stay clear of them.  Its one internal import is ``app.config``, for the
``browser`` key, taken at module level: ``app/__init__.py`` performs its web
framework, blueprint, error-handler and command-line imports inside
``create_app()`` precisely so that this package stays importable in a worker
that never builds a web application, so there is no cycle to defend against
and no reason to defer the import into a function body.  Nothing else is
imported - no sibling package of ``app.config``, no service, no reporting
writer, no page object, no web framework and no Gherkin engine.

The test seam
-------------
:data:`_holder` and its accessors are module-private on purpose.  The Java
class exposes no way to discard a session without quitting it, so neither does
this module: there is no ``reset``, no ``clear`` and no setter in the public
surface.  ``tests/test_driver.py`` reaches the private holder directly to
start each test from an empty slot - that is the intended seam, and it is
private rather than public so that production code cannot orphan a live
browser process by using it.

Provisioning has the same kind of seam, and no more: the module-level names
this file binds - :data:`_SYSTEM_DRIVER_DIRECTORIES`,
:func:`_private_cache_root_path`, the two managers, the two ``Service``
factories and the ``webdriver`` namespace - are what a test replaces to
exercise route one, route two and a refusal without a browser, a driver
download, a network or a write outside its own temporary directory.  The two
platform predicates :data:`_IS_WINDOWS` and :data:`_IS_MACOS`, the
:data:`_HAS_POSIX_PERMISSIONS` and :data:`_HAS_PROCESS_GROUPS` capability
flags and the :func:`_kernel32` binding are the same kind of seam for the
platform branches, and replacing them is how the arms of another platform are
covered on this one.  None of these is a configuration point: they are module
constants, module imports and a platform lookup, so replacing one is a test
substitution and never an operator control.

What ``tests/test_driver.py`` asserts
-------------------------------------
Stated here so the suite can be written against this module without rereading
the Java source.  Every item is reachable with a stubbed driver: no test needs
a real browser, a live system under test, or a populated properties file.

1. **Create on demand** (``:22``) - with the ``browser`` key reading
   ``"chrome"``, :func:`get_driver` constructs once and a second call returns
   the *same* object, provisioning and constructing nothing further.
2. **Set-up order** (``:32-34``) - ``maximize_window()`` is called before
   ``implicitly_wait``, and ``implicitly_wait`` receives exactly ``10``.
3. **Firefox provisioning** (deviation 5) - with ``"firefox"``,
   ``GeckoDriverManager`` is used and the Chrome manager is never touched.
4. **No default branch** (``:29-42``) - ``"Chrome"``, ``"CHROME"``,
   ``" chrome"``, ``"safari"``, ``"edge"``, ``""`` and an unset key each yield
   ``None``, raise nothing, construct nothing, and leave a later call free to
   try again.
5. **Guarded teardown** (``:51``) - :func:`quit_driver` on an empty slot
   returns without raising and without calling anything.
6. **Teardown clears the slot** (``:52-53``) - ``quit()`` is called exactly
   once and the following :func:`get_driver` constructs a *new* session.
7. **A failing ``quit()`` propagates, and the slot is cleared anyway** - a
   stub whose ``quit()`` raises makes :func:`quit_driver` raise that exception
   while the following :func:`_session` reads ``None``.  :func:`quit_driver`
   offers no "never raises" guarantee: ``Driver.closeDriver()`` installs no
   handler, and a browser that would not close has to be visible.  Under
   behave the caller is ``after_scenario``, and behave 1.3.3's
   ``runner.run_hook`` turns that into ``HOOK-ERROR in after_scenario: ...``
   with the scenario's status set to ``hook_error``, without aborting the run.
8. **Route one is preferred** - with a verifying candidate in the searched
   system directories, no manager is constructed and no ``.install()`` happens.
9. **Verification refuses what it must** - a symbolic link, a group- or
   world-writable file, a file in a group- or world-writable directory, a
   non-executable file and anything that is not a regular file each raise
   :exc:`DriverProvisioningError` rather than reaching Selenium, and a path the
   manager returned is held to the same gate.
10. **Route two neutralizes the ambient environment** - ``.install()`` runs
    with ``WDM_SSL_VERIFY`` forced true and ``WDM_LOCAL`` forced false, the
    previous values are restored afterwards even when ``.install()`` raises,
    and the browser-version probe it is given resolves absolute binaries with
    no shell and never a bare program name.
11. **Ambient ``SE_CHROMEDRIVER`` / ``SE_GECKODRIVER`` change nothing** - the
    path carried by the service Selenium receives is still the verified one.
12. **The provisioning cache is private, verified and stable** - its root is
    computed from the system temporary directory and this account's user id
    rather than from ``HOME``, created ``0700``, reused by a second call, and
    refused rather than repaired when what stands at that path is a file, a
    symbolic link, group- or world-writable, or owned by another account.
13. **An archive is validated before it is unpacked** - an absolute member
    path, a member escaping the destination and a member that is not a regular
    file or a directory each refuse the whole archive, in both the tar and the
    zip path, leaving nothing on disk; an unreadable or unrecognised archive is
    refused with a reason.
14. **The driver process is contained in its own group inside this session** -
    ``popen_kw`` carries ``process_group=0``, the handle is captured by the
    wrapper on the service's ``start`` and adopted by the session, and teardown
    distinguishes a verified release from "no local process" and from "never
    contained", raising :exc:`DriverTeardownError` for the last.
15. **Every platform arm is reachable** - the macOS and Windows browser and
    driver-directory tables, the POSIX-only half of the verification and the
    Windows Job Object branches are all exercised by replacing the platform
    seams above, whichever platform the suite runs on, and every test that
    needs a facility only one platform has skips where that facility is absent.
"""

import logging
import os
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.driver_cache import DriverCacheManager
from webdriver_manager.core.file_manager import FileManager
from webdriver_manager.core.os_manager import ChromeType, OperationSystemManager
from webdriver_manager.firefox import GeckoDriverManager

from app.config import get_browser

__all__ = ["get_driver", "quit_driver"]

#: The module logger.  Records are emitted from exactly one concern - the
#: OS-level containment of the browser process tree - because that is the one
#: place where something can go wrong that a caller cannot otherwise see: a
#: browser process left running with the system under test's authenticated
#: session in it.  Nothing on the dispatch or set-up paths logs, because
#: nothing there is invisible to the caller.  ``app/logging_config.py`` routes
#: WARNING and above to standard error, which is where a teardown diagnostic
#: belongs.
logger = logging.getLogger(__name__)


class DriverProvisioningError(RuntimeError):
    """A driver executable was refused, so no browser was started with it.

    Raised by :func:`_verified_driver_path` - and therefore only from a branch
    that *matched* a browser this port constructs - when the candidate
    executable fails any requirement of the verification listed in the module
    docstring's provisioning policy.  It is deliberately **not** raised for an
    unrecognised ``browser`` value: that value matches no branch, nothing is
    provisioned for it, and AAP 0.4.1 fixes its behaviour as failing at first
    driver use rather than here.

    The message names the browser and one fixed reason class from
    :data:`_REFUSAL_REASONS`, and carries neither the rejected path, nor any
    environment value, nor any output of the provisioning library - a refusal
    is diagnosed by the operator from the browser and the reason, and echoing
    influenced text into a durable log or a report artifact is what a refusal
    must not do.

    ``RuntimeError`` rather than a new root: a caller that catches
    :exc:`Exception` around ``get_driver`` (``features/environment.py``'s hook
    boundary, and behave's ``runner.run_hook`` above it) already handles this
    correctly, and no caller in this port needs to distinguish it from any
    other failure to start a browser.
    """


class DriverTeardownError(RuntimeError):
    """A session was closed but its process tree could not be confirmed gone.

    Raised by :func:`quit_driver` only on the path where ``quit()`` itself
    *succeeded*: a ``quit()`` that raised is already reporting the teardown
    failure and must not have its exception replaced, so an unconfirmed
    reclamation is logged alongside it rather than raised over it.

    This is the failure the session cannot report on its own.  ``quit()``
    returning means the driver executable accepted the request, not that the
    browser it started is gone - and a browser left running holds the system
    under test's authenticated session.  Raising here is what puts that in
    front of an operator: under behave the caller is ``after_scenario``, and
    ``runner.run_hook`` turns it into ``HOOK-ERROR in after_scenario: ...``
    with that scenario recorded as ``hook_error``, while the remaining
    scenarios still run and all four artifacts are still written.

    The message carries the fixed diagnosis only; the process group or job that
    survived is named in the log record the reclamation already emitted, where
    it belongs, and not in an exception whose text reaches report artifacts.
    """


# The worker-local session slot - ``Driver.java:17``'s
# ``InheritableThreadLocal``, reached through the module rather than passed
# around.  Private together with its three accessors, because the Java class
# offers no way to abandon a session without quitting it and this module offers
# none either; ``tests/test_driver.py`` monkeypatches the holder so each test
# starts from an empty slot, and that seam is private for exactly that reason.
_holder = threading.local()

#: The attribute carrying this worker's session.  Named once so that the
#: accessors below and the test seam cannot drift apart.
_SESSION_ATTRIBUTE = "driver"

#: The attribute carrying the OS-level containment handle for that session -
#: what makes the browser *process tree* reclaimable rather than only the
#: WebDriver object discardable.  A session and its containment are adopted
#: together and cleared together, so the holder never carries one without the
#: other.
_CONTAINMENT_ATTRIBUTE = "containment"

#: The attribute a containment handle waits in between being captured and being
#: adopted.  Those are two different moments and have to be: the handle is taken
#: when the driver executable starts - which is before the browser exists, and
#: therefore before the only instant at which a Windows job can contain it -
#: while the session object it belongs to does not exist until the WebDriver
#: handshake that follows has completed.
#:
#: On the same holder as the session, deliberately: staging is per worker for
#: the same reason a session is, and a replaced holder starts both empty.
_STAGED_CONTAINMENT_ATTRIBUTE = "staged_containment"


def _session() -> webdriver.Remote | None:
    """Return this worker's session, or ``None`` when the slot is empty.

    The read half of ``driverPool.get()``.  A fresh ``threading.local()``
    carries no attributes at all, so the default is what makes an untouched
    slot indistinguishable from one that has been cleared - exactly the
    ``null`` that ``Driver.java:22`` and ``:51`` test against.

    :returns: The live session, or ``None``.
    """
    return getattr(_holder, _SESSION_ATTRIBUTE, None)


def _clear_session() -> None:
    """Empty this worker's slot, the port of ``driverPool.remove()``.

    ``remove()`` (``Driver.java:53``) drops the thread's entry so that a later
    ``get()`` reads ``null``; binding the attribute to ``None`` is observably
    the same through :func:`_session` and avoids a delete that would have to
    tolerate an absent attribute.

    :returns: ``None``.
    """
    setattr(_holder, _SESSION_ATTRIBUTE, None)
    setattr(_holder, _CONTAINMENT_ATTRIBUTE, None)


def _containment() -> _Containment:
    """Return the containment state for this worker's session.

    :returns: What :func:`_adopt_session` adopted - a handle,
        :data:`_NO_LOCAL_PROCESS`, or ``None`` when the slot is empty or no
        capture ever ran for it.
    """
    return getattr(_holder, _CONTAINMENT_ATTRIBUTE, None)


def _stage_containment(containment: _Containment) -> None:
    """Park a freshly captured containment handle until a session claims it.

    :param containment: What :func:`_capture_containment` produced - a handle,
        or :data:`_NO_LOCAL_PROCESS` when the service exposed no local driver
        process.
    :returns: ``None``.
    """
    setattr(_holder, _STAGED_CONTAINMENT_ATTRIBUTE, containment)


def _take_staged_containment() -> _Containment:
    """Return the staged containment handle and empty the staging slot.

    Read-and-clear in one step, so a handle can be adopted exactly once: a
    second reader gets ``None`` rather than a handle somebody else already
    owns, and a session that was never constructed cannot leave its handle
    where the next one would pick it up.

    :returns: The staged state, or ``None`` when no capture ran at all - which
        is what teardown reads as "this session has no verifiable tree".
    """
    staged = getattr(_holder, _STAGED_CONTAINMENT_ATTRIBUTE, None)
    setattr(_holder, _STAGED_CONTAINMENT_ATTRIBUTE, None)

    return staged


def _discard_staged_containment() -> None:
    """Release any containment handle that no session ever adopted.

    Reached only on an abnormal path: the driver executable started, and then
    the WebDriver handshake failed before :func:`_adopt_session` could claim
    the handle, so nothing owns the process tree that start created.  The next
    provisioning call is where that is noticed, and dropping the handle is not
    enough - on Windows the job would stay open for the life of the worker, and
    on POSIX the group may still hold a browser.  So it is released here, which
    is also what closes the Windows job and terminates whatever is in it.

    :returns: ``None``.
    """
    orphaned = _take_staged_containment()

    if not isinstance(orphaned, _SessionContainment):
        return

    logger.warning(
        "Driver process %d was contained but never adopted by a session; "
        "reclaiming its process tree now",
        orphaned.pid,
    )
    _release_containment(orphaned)


def _adopt_session(driver: webdriver.Remote) -> None:
    """Take ownership of ``driver`` and apply the two settings Java applies.

    The shared tail of both branches of ``Driver.java``'s switch - ``:32-34``
    for Chrome and ``:38-40`` for Firefox - which apply identical treatment to
    the newly constructed browser.  Factored into one function so the two
    branches cannot drift apart, where the Java copy-paste only happens to
    agree.

    Two orderings matter and both are the Java one:

    * **Slot first.**  Java stores with ``driverPool.set(...)`` and only then
      dereferences ``driverPool.get().manage()``, so the session is tracked
      before anything can go wrong with it.  If ``maximize_window()`` raises,
      the session is already ours and :func:`quit_driver` can still close the
      browser process rather than leaking it.
    * **Maximize, then the implicit wait**, matching ``:33`` before ``:34``.

    :param driver: The freshly constructed session to take ownership of.
    :returns: ``None``.
    """
    setattr(_holder, _SESSION_ATTRIBUTE, driver)

    # Ownership of the *process tree* is taken in the same breath as ownership
    # of the session object, and before anything can go wrong with either.  The
    # handle itself was captured earlier - when the service started the driver
    # executable, which is the one instant at which the process exists and the
    # browser does not - and has been waiting in the staging slot since; see
    # :func:`_install_containment_capture`.  Adopting it here is what ties it to
    # this session's lifetime, and taking it clears the staging slot so no
    # later session can adopt the same tree.
    #
    # What arrives is one of the three states :class:`_NoLocalProcess`
    # describes, and teardown treats them differently: a handle is released and
    # verified, :data:`_NO_LOCAL_PROCESS` is nothing to reclaim, and ``None`` -
    # no capture ran for this session at all - is a tree teardown cannot verify
    # and must not report as gone.
    setattr(_holder, _CONTAINMENT_ATTRIBUTE, _take_staged_containment())

    # ``manage().window().maximize()`` - Driver.java:33 / :39.
    driver.maximize_window()

    # ``manage().timeouts().implicitlyWait(10, TimeUnit.SECONDS)`` -
    # Driver.java:34 / :40.  The Python binding takes *seconds*, so the
    # literal is 10 and never a millisecond value.
    driver.implicitly_wait(10)


# ---------------------------------------------------------------------------
# Driver provisioning - the verified path every ``Service`` receives
#
# The module docstring's "Driver provisioning policy" section states the
# contract; each definition below implements the part it names.  Nothing here
# is public: the surface stays the two lifecycle functions, and provisioning is
# an implementation detail of the two branches that construct a browser.
# ---------------------------------------------------------------------------

# Refusal reason codes, and the fixed sentence each renders as.  A refusal
# message is assembled only from this mapping, so it can never carry the
# rejected path, an environment value or any output of the provisioning
# library - which is what keeps a refusal safe to log and to publish.
_REASON_NOT_ABSOLUTE: Final[str] = "not-absolute"
_REASON_NOT_INSPECTABLE: Final[str] = "not-inspectable"
_REASON_NOT_REGULAR_FILE: Final[str] = "not-a-regular-file"
_REASON_FILE_WRITABLE: Final[str] = "file-writable-by-others"
_REASON_DIRECTORY_WRITABLE: Final[str] = "directory-writable-by-others"
_REASON_NOT_EXECUTABLE: Final[str] = "not-executable"
_REASON_FOREIGN_OWNER: Final[str] = "foreign-owner"
_REASON_CACHE_UNUSABLE: Final[str] = "cache-root-unusable"
_REASON_CACHE_NOT_DIRECTORY: Final[str] = "cache-root-not-a-directory"
_REASON_CACHE_WRITABLE: Final[str] = "cache-root-writable-by-others"
_REASON_CACHE_FOREIGN_OWNER: Final[str] = "cache-root-foreign-owner"
_REASON_CACHE_NOT_PRIVATE: Final[str] = "cache-root-not-private"
_REASON_ARCHIVE_FORMAT: Final[str] = "archive-format-unsupported"
_REASON_ARCHIVE_UNREADABLE: Final[str] = "archive-unreadable"
_REASON_ARCHIVE_ABSOLUTE_MEMBER: Final[str] = "archive-member-absolute"
_REASON_ARCHIVE_ESCAPING_MEMBER: Final[str] = "archive-member-escapes-cache"
_REASON_ARCHIVE_IRREGULAR_MEMBER: Final[str] = "archive-member-not-a-regular-file"

#: Every reason class a refusal can name, and its message text.
_REFUSAL_REASONS: Final[dict[str, str]] = {
    _REASON_NOT_ABSOLUTE: "the path is empty or is not absolute",
    _REASON_NOT_INSPECTABLE: "the path or its parent directory cannot be inspected",
    _REASON_NOT_REGULAR_FILE: "the path is not a regular file",
    _REASON_FILE_WRITABLE: "the file is writable by its group or by everyone",
    _REASON_DIRECTORY_WRITABLE: "the directory is writable by its group or by everyone",
    _REASON_NOT_EXECUTABLE: "the file is not executable by this process",
    _REASON_FOREIGN_OWNER: "the file is owned by neither root nor this user",
    _REASON_CACHE_UNUSABLE: "the private driver cache root cannot be created or inspected",
    _REASON_CACHE_NOT_DIRECTORY: "the private driver cache root is not a directory",
    _REASON_CACHE_WRITABLE: "the private driver cache root is writable by its group or by everyone",
    _REASON_CACHE_FOREIGN_OWNER: "the private driver cache root is owned by another account",
    _REASON_CACHE_NOT_PRIVATE: (
        "the provisioning library resolved its cache outside the private root"
    ),
    _REASON_ARCHIVE_FORMAT: "the provisioned archive is in no format this port unpacks",
    _REASON_ARCHIVE_UNREADABLE: "the provisioned archive could not be read",
    _REASON_ARCHIVE_ABSOLUTE_MEMBER: "the archive carries a member with an absolute path",
    _REASON_ARCHIVE_ESCAPING_MEMBER: "the archive carries a member that escapes the cache",
    _REASON_ARCHIVE_IRREGULAR_MEMBER: (
        "the archive carries a member that is neither a regular file nor a directory"
    ),
}

#: The write bits that make a file modifiable by somebody other than its
#: owner.  Either of them, on the binary or on the directory holding it, means
#: another local account can decide what this worker executes as a driver.
_WRITABLE_BY_OTHERS: Final[int] = stat.S_IWGRP | stat.S_IWOTH

#: The superuser's user id.  A driver owned by root, in a directory only root
#: can write, is the one this port treats as administrator-supplied.
_ROOT_UID: Final[int] = 0

#: Whether this platform decides who may write a file with the POSIX mode bits
#: :data:`_WRITABLE_BY_OTHERS` names, and reports a meaningful owner in
#: ``st_uid``.  False on Windows, where permission is an access-control list
#: that ``os.lstat`` does not report at all and where every file's ``st_uid``
#: reads as ``0`` - so a mode-bit test there would be a check this module
#: claimed and did not perform.  The trust decisions that depend on it are
#: guarded by it, one by one, and the module docstring states what Windows is
#: held to instead.
_HAS_POSIX_PERMISSIONS: Final[bool] = os.name == "posix"

#: Which of the three platforms AAP 0.8 requires - Windows, Linux and macOS -
#: this process is running on.  Decided from ``sys.platform`` and not from
#: ``os.name``, because ``os.name`` is ``"posix"`` for both Linux and macOS and
#: those two agree on almost nothing that matters below: macOS keeps its
#: browsers inside application bundles under ``/Applications``, installs
#: command-line tools under ``/opt/homebrew/bin`` or ``/usr/local/bin``, and has
#: none of the Linux locations at all.  A third arm is therefore required
#: rather than optional, and "everything else" is the Linux and BSD arm.
_IS_WINDOWS: Final[bool] = sys.platform == "win32"
_IS_MACOS: Final[bool] = sys.platform == "darwin"


def _for_platform[T](windows: T, macos: T, elsewhere: T) -> T:
    """Return whichever of three platform-specific values belongs here.

    A function among the constants, deliberately: every platform-dependent
    table below is selected by this one rule, so the rule is written once and
    the tables cannot drift into disagreeing about how many platforms there
    are.  It also makes each arm reachable from a test on any host, which is
    what gives the macOS and Windows tables coverage on a Linux build agent.

    :param windows: The value for Windows.
    :param macos: The value for macOS.
    :param elsewhere: The value for Linux, and for any other POSIX platform.
    :returns: The value for the platform this process is running on.
    """
    if _IS_WINDOWS:
        return windows

    if _IS_MACOS:
        return macos

    return elsewhere


#: Executable suffix of a driver binary on this platform.
_EXECUTABLE_SUFFIX: Final[str] = ".exe" if _IS_WINDOWS else ""

#: The fixed file names the two drivers are searched for under - the names
#: ``webdriver-manager`` itself installs them as.  Fixed, because a configured
#: file name would be one more ambient input deciding what gets executed.
_CHROME_DRIVER_EXECUTABLE: Final[str] = f"chromedriver{_EXECUTABLE_SUFFIX}"
_GECKO_DRIVER_EXECUTABLE: Final[str] = f"geckodriver{_EXECUTABLE_SUFFIX}"

#: Absolute system directories searched for a pre-provisioned driver, in
#: order.  Administrator-owned locations only: an operator who installs the
#: driver here gets route one, and the search reaches no network and starts no
#: subprocess.
#:
#: ``PATH`` is deliberately absent, and its absence is the point.  Resolving a
#: program *name* through ``PATH`` lets any writable directory earlier in it
#: supply the binary that gets executed, which is exactly the vector the
#: provisioning library's own shell-based browser probe opens and which
#: :class:`_VerifiedSystemManager` closes below.  A per-run override is not
#: offered for the same reason: it would restore the ambient control this
#: search exists to remove.
#: The Linux and BSD arm.
_LINUX_DRIVER_DIRECTORIES: Final[tuple[str, ...]] = (
    "/usr/local/bin",
    "/usr/bin",
    "/opt/selenium",
    "/opt/webdriver",
)

#: The macOS arm.  Homebrew installs ``chromedriver`` and ``geckodriver`` into
#: ``/opt/homebrew/bin`` on Apple silicon and ``/usr/local/bin`` on Intel, which
#: between them are where a macOS operator's driver actually is; ``/usr/bin`` is
#: kept last because the system volume is read-only and nothing can be added to
#: it, so a hit there is genuinely administrator-supplied.
_MACOS_DRIVER_DIRECTORIES: Final[tuple[str, ...]] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
)

#: The Windows arm.  ``Program Files`` style locations only: they are writable
#: by administrators alone, which is what stands in for the POSIX mode-bit
#: checks that :func:`_refusal_reason` cannot perform against an ACL.
_WINDOWS_DRIVER_DIRECTORIES: Final[tuple[str, ...]] = (
    r"C:\Program Files\WebDriver\bin",
    r"C:\Program Files (x86)\WebDriver\bin",
    r"C:\WebDriver\bin",
)

_SYSTEM_DRIVER_DIRECTORIES: Final[tuple[str, ...]] = _for_platform(
    _WINDOWS_DRIVER_DIRECTORIES,
    _MACOS_DRIVER_DIRECTORIES,
    _LINUX_DRIVER_DIRECTORIES,
)

#: Environment variables forced to a trusted value around ``.install()``, and
#: the value each is forced to.  ``WDM_SSL_VERIFY`` decides whether the
#: library verifies certificates on its downloads; ``WDM_LOCAL`` relocates its
#: cache, and ``DriverCacheManager.__init__`` applies it *after* an explicitly
#: passed cache root, so forcing it off is the only way to keep it from
#: choosing where driver files are written.
_WDM_FORCED_SETTINGS: Final[tuple[tuple[str, str], ...]] = (
    ("WDM_SSL_VERIFY", "true"),
    ("WDM_LOCAL", "false"),
)

#: Environment variables removed around ``.install()``: the library's log
#: level, its progress bar, and the pytest-xdist worker id it appends to its
#: cache path.  None of them is a legitimate input to a browser run, and each
#: is read from the environment - a hidden ``.env`` file included.
_WDM_REMOVED_SETTINGS: Final[tuple[str, ...]] = (
    "WDM_LOG",
    "WDM_LOG_LEVEL",
    "WDM_PROGRESS_BAR",
    "PYTEST_XDIST_WORKER",
)

#: Transport inputs removed around ``.install()``, in both spellings the
#: libraries beneath the provisioning call honour.  ``requests`` and
#: ``urllib3`` read the proxy names to decide *where* a driver is fetched from,
#: and the certificate names to decide *what* signs for it - so either class of
#: value is an ambient answer to "is this the real driver host", and each is
#: reachable from a checkout ``.env`` that
#: ``webdriver_manager.core.config``'s import-time ``load_dotenv()`` merged
#: into the environment after a worker launcher had filtered it out.
#:
#: Both spellings are listed because the conventional lower-case names are the
#: ones ``requests`` prefers for proxies, and removing a name that library
#: never reads costs nothing while leaving one it does read would leave the
#: control in place.
_TRANSPORT_REMOVED_SETTINGS: Final[tuple[str, ...]] = (
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

#: Mode a directory this module owns is created with: reachable by its owner
#: and by nobody else.  Used for the private provisioning cache root, whose
#: contents are executed afterwards.
_PRIVATE_DIRECTORY_MODE: Final[int] = 0o700

#: Fixed name of the private provisioning cache root, under the system
#: temporary directory.  Fixed and stable rather than per-process, because a
#: cache that moved every run would be no cache at all; scoped by user id on
#: POSIX, so two accounts sharing one temporary directory never share a root
#: and neither can write the other's.
_DRIVER_CACHE_DIRECTORY: Final[str] = "testinium-qa-drivers"

#: Where ``DriverCacheManager`` records the root it actually resolved.  Read
#: back by :func:`_provisioning_manager` so the private root is a *verified*
#: outcome rather than a requested one: the library applies ``WDM_LOCAL`` after
#: the root it was handed, and a true value there replaces that root outright
#: with a working-directory-relative path.  Named here rather than written
#: inline so the one private attribute this module reaches into is declared
#: once, beside the reason code its absence or disagreement raises.
_CACHE_ROOT_ATTRIBUTE: Final[str] = "_root_dir"

#: The archive formats route two's two managers download, by suffix.  Both are
#: unpacked by :class:`_ValidatingFileManager`; anything else is refused rather
#: than guessed at.
_TAR_ARCHIVE_SUFFIXES: Final[tuple[str, ...]] = (".tar.gz", ".tar.bz2", ".tgz")
_ZIP_ARCHIVE_SUFFIXES: Final[tuple[str, ...]] = (".zip",)

#: A Windows drive-letter prefix, such as ``C:``.  An archive member carrying
#: one is absolute on Windows and is refused on every platform, because the
#: archive's author chose that name and the host reading it decides nothing
#: about what it means.
_DRIVE_PREFIX: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z]:")

#: Serializes the environment override below.  ``os.environ`` is process-wide,
#: so two threads provisioning at once could otherwise interleave their
#: override and their restore and leave a value behind.  The engine runs
#: single-threaded inside each worker, so this is never contended in
#: production; it is what makes the override correct anyway.
_ENVIRONMENT_LOCK = threading.RLock()

#: The name of the program-search variable, spelled once.
_PATH_VARIABLE: Final[str] = "PATH"

#: The search path a provisioning call runs with: system directories only, in
#: the platform's conventional order, and nothing a run's own environment
#: contributed.  Narrow rather than empty, because the provisioning library
#: does resolve programs by bare name and an empty value would make a
#: legitimate lookup fail rather than make a hostile one safe.
_TRUSTED_PATH: Final[str] = (
    os.pathsep.join(
        (
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "system32"),
            os.environ.get("SystemRoot", r"C:\Windows"),
        )
    )
    if _IS_WINDOWS
    else "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
)

#: Wall-clock bound on one browser-version probe, in seconds.  A local
#: ``--version`` call answers in milliseconds; this is the bound that keeps a
#: wedged browser binary from stalling provisioning indefinitely.
_PROBE_TIMEOUT_SECONDS: Final[float] = 10.0

#: The browser type the Gecko manager asks its version probe about.  The
#: Chrome manager's is ``ChromeType.GOOGLE``.
_FIREFOX_BROWSER_TYPE: Final[str] = "firefox"

#: Absolute, no-shell locations of each browser, by the type key the
#: provisioning library passes to its probe.  Verified before being run, so a
#: writable impostor at one of these paths is skipped rather than executed.
#:
#: **Why a missing location is not harmless.**  The probe answers "which
#: browser version is installed", and the provisioning library uses that answer
#: to ask for the *matching* driver.  A probe that finds nothing returns
#: ``None``, at which point the library falls back to the latest driver release
#: - and a driver newer than the installed browser refuses to drive it, so
#: every scenario fails at the first session.  So each platform needs its real
#: locations, and macOS needs its application bundles rather than Linux paths
#: that cannot exist there.
_LINUX_BROWSER_BINARIES: Final[dict[str, tuple[str, ...]]] = {
    ChromeType.GOOGLE: (
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ),
    _FIREFOX_BROWSER_TYPE: (
        "/usr/lib/firefox/firefox",
        "/usr/bin/firefox",
        "/opt/firefox/firefox",
    ),
}
#: The macOS arm: the executable *inside* each application bundle, which is
#: what a bundled browser's binary actually is and what the provisioning
#: library's own mac command probes.  The space in each path is a space and not
#: an escape sequence, because nothing here reaches a shell.
_MACOS_BROWSER_BINARIES: Final[dict[str, tuple[str, ...]]] = {
    ChromeType.GOOGLE: (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ),
    _FIREFOX_BROWSER_TYPE: ("/Applications/Firefox.app/Contents/MacOS/firefox",),
}
_WINDOWS_BROWSER_BINARIES: Final[dict[str, tuple[str, ...]]] = {
    ChromeType.GOOGLE: (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ),
    _FIREFOX_BROWSER_TYPE: (
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ),
}
_BROWSER_BINARIES: Final[dict[str, tuple[str, ...]]] = _for_platform(
    _WINDOWS_BROWSER_BINARIES,
    _MACOS_BROWSER_BINARIES,
    _LINUX_BROWSER_BINARIES,
)

#: Anchored patterns for the output of ``<browser> --version``.  Anchored at
#: the start and naming the product, so arbitrary text a compromised or
#: unrelated executable printed cannot be mined for something version-shaped;
#: the captured group is the same portion the provisioning library's own
#: pattern would have taken, so a successful probe answers it identically.
_BROWSER_VERSION_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    ChromeType.GOOGLE: re.compile(
        r"\A(?:Google Chrome|Chromium)[ ](?P<version>\d+\.\d+\.\d+)(?:\.\d+)?"
    ),
    _FIREFOX_BROWSER_TYPE: re.compile(r"\AMozilla Firefox[ ](?P<version>\d+\.\d+)"),
}


def _refusal_reason(candidate: str) -> str | None:
    """Return why ``candidate`` may not be executed as a driver, or ``None``.

    The whole of the verification stated in the module docstring, in one place
    so that route one's search and route two's gate cannot apply different
    rules.  Every check is a property of the file *as it is on disk*: nothing
    here trusts the string it was handed, and nothing follows a link.

    Three checks apply on every platform - the path is absolute, the thing at
    it is a regular file, and this process may execute it.  The three that
    reason about *who else* may write it are POSIX-only, guarded by
    :data:`_HAS_POSIX_PERMISSIONS`, and the module docstring states plainly
    what Windows is and is not held to: an ACL is not evaluated there, which is
    why the Windows locations searched are administrator-writable ones.

    :param candidate: The absolute path of a candidate driver executable.
    :returns: A key of :data:`_REFUSAL_REASONS` when the candidate is refused,
        or ``None`` when it satisfies every requirement.
    """
    if not candidate or not os.path.isabs(candidate):
        return _REASON_NOT_ABSOLUTE

    try:
        # ``lstat`` and never ``stat``: a symbolic link is refused rather than
        # resolved, so the check cannot be satisfied by a link whose target is
        # replaced afterwards.  Both inspections happen here, so that anything
        # unreadable - an absent file, an absent parent, a directory this
        # process may not traverse - is the one reason class rather than
        # several indistinguishable ones.
        entry = os.lstat(candidate)
        directory = os.lstat(os.path.dirname(candidate))
    except OSError:
        return _REASON_NOT_INSPECTABLE

    if not stat.S_ISREG(entry.st_mode):
        return _REASON_NOT_REGULAR_FILE

    if _HAS_POSIX_PERMISSIONS and entry.st_mode & _WRITABLE_BY_OTHERS:
        return _REASON_FILE_WRITABLE

    if not os.access(candidate, os.X_OK):
        return _REASON_NOT_EXECUTABLE

    if _HAS_POSIX_PERMISSIONS and directory.st_mode & _WRITABLE_BY_OTHERS:
        return _REASON_DIRECTORY_WRITABLE

    # Ownership and the two mode-bit checks above are POSIX concepts.  Windows
    # answers ``st_uid`` as ``0`` for every file and expresses permission in an
    # access-control list that ``os.lstat`` does not report at all, so
    # evaluating either there would be arithmetic on values that carry no
    # meaning - and reporting a pass from it would claim a check that never
    # happened.  What stands on Windows instead is the set of locations
    # searched: :data:`_WINDOWS_DRIVER_DIRECTORIES` holds administrator-writable
    # ``Program Files`` style paths only.
    if _HAS_POSIX_PERMISSIONS and entry.st_uid not in (_ROOT_UID, os.geteuid()):
        return _REASON_FOREIGN_OWNER

    return None


def _verified_driver_path(candidate: str | None, browser: str) -> str:
    """Return ``candidate`` once it has passed verification, or refuse it.

    The single gate: every path that reaches a ``Service`` - route one's
    system binary and route two's ``.install()`` result alike - comes back
    through here first.

    :param candidate: The path to verify.  ``None`` and ``""`` are refused
        like any other unusable value, so a provisioning library that returned
        nothing cannot be mistaken for one that succeeded.
    :param browser: The matched browser name, ``"chrome"`` or ``"firefox"``.
        It reached this function from a branch that compared equal to one of
        those two literals, so naming it in the message discloses nothing
        about the configured value beyond which branch ran.
    :returns: The verified path, unchanged.
    :raises DriverProvisioningError: When the candidate fails any requirement.
    """
    reason = _refusal_reason(candidate or "")

    if reason is not None:
        raise DriverProvisioningError(
            f"refusing to start {browser}: the driver executable is not "
            f"trustworthy - {_REFUSAL_REASONS[reason]} [{reason}]"
        )

    # ``reason is None`` means the candidate was a non-empty absolute path, so
    # the narrowing here is a fact of the check above rather than a cast.
    return str(candidate)


def _pre_provisioned_driver_path(executable_name: str) -> str | None:
    """Return the first verifying driver in the system directories, if any.

    Route one.  The search is over :data:`_SYSTEM_DRIVER_DIRECTORIES` in
    order, with the fixed executable name joined onto each - never ``PATH``,
    never a shell, never a network call.  A candidate that exists but fails
    verification is *skipped* rather than refused, because another directory
    may still hold a good one; a refusal is only ever raised for the path that
    is actually about to be used.

    :param executable_name: ``chromedriver`` or ``geckodriver``, with the
        platform's executable suffix already applied.
    :returns: The absolute path of the first candidate that verifies, or
        ``None`` when no directory holds one.
    """
    for directory in _SYSTEM_DRIVER_DIRECTORIES:
        candidate = os.path.join(directory, executable_name)

        if _refusal_reason(candidate) is None:
            return candidate

    return None


def _resolves_inside(candidate: object, root: str) -> bool:
    """Report whether ``candidate`` names ``root`` itself or a path beneath it.

    The one containment rule this module applies, written once because it
    answers two different questions and both are security decisions: whether
    an archive member would be written outside the directory it is being
    unpacked into, and whether the provisioning library's cache resolved
    outside the private root it was handed.  Two spellings of one rule are two
    chances to disagree.

    ``abspath`` rather than ``normpath``, so a *relative* candidate is resolved
    against the working directory before being compared - which is exactly the
    case the cache check exists for, since the library's fallback root is the
    relative ``.wdm``.  For an already-absolute candidate the two are
    identical.  ``normcase`` makes the comparison case-insensitive and
    separator-agnostic on Windows, where two spellings of one path are one
    path, and is the identity function on POSIX.

    :param candidate: The path under test.  A non-string - which is what
        reading an attribute off a foreign object can yield - is not inside
        anything and answers ``False``.
    :param root: The absolute directory the candidate must not leave.
    :returns: ``True`` when the candidate is the root or sits beneath it.
    """
    if not isinstance(candidate, str) or not candidate:
        return False

    base = os.path.normcase(os.path.abspath(root))
    target = os.path.normcase(os.path.abspath(candidate))

    return target == base or target.startswith(base + os.sep)


def _private_cache_root_path() -> str:
    """Return the fixed location of this account's private driver cache root.

    Route two's cache has to be somewhere this module decides, because the
    provisioning library's own answer is ambient: with no explicit root it
    computes ``DEFAULT_USER_HOME_CACHE_PATH``, which is ``<HOME>/.wdm`` read
    from the environment at import, and ``HOME`` is legitimately preserved in a
    worker's environment - so an ambient ``HOME`` would relocate every cache
    read and every cache write, and the cache is what gets executed.

    The location is therefore derived from the system temporary directory and
    from this process's user id, and from nothing else:

    * **Not from ``HOME``**, which is the input being removed.
    * **Stable, not per-process**, so a second run finds the driver the first
      one provisioned; a per-run directory would download a fresh binary for
      every scenario shard and leave a directory behind for each.
    * **User-scoped on POSIX**, where one temporary directory is shared by
      every account on the host: the user id in the name is what stops two
      accounts from meeting in one root, and the ``0700`` mode and the
      ownership check applied by :func:`_private_cache_root` are what stop
      either from writing the other's.
    * **Already user-scoped on Windows**, where ``tempfile.gettempdir()``
      resolves to the per-user ``%TEMP%`` directory
      (``...\\Users\\<account>\\AppData\\Local\\Temp``) and ``os.getuid`` does
      not exist, so no suffix is added and none is needed.

    :returns: The absolute path of the cache root, whether or not it exists.
    """
    # ``getuid`` rather than ``geteuid``: the name has to be stable for the
    # account that owns the cache across a run, and it is the real user id that
    # a temporary directory's other occupants are distinguished by.  The
    # ownership *check* below uses the effective id, because that is the
    # identity the files are actually created under.
    identity = f"-{os.getuid()}" if hasattr(os, "getuid") else ""

    return os.path.join(tempfile.gettempdir(), f"{_DRIVER_CACHE_DIRECTORY}{identity}")


def _cache_root_refusal(root: str) -> str | None:
    """Return why the private cache root may not be used, or ``None``.

    The same shape of check as :func:`_refusal_reason`, over a directory whose
    contents this module is about to execute.  Nothing is followed and nothing
    is repaired: a root that exists and fails any requirement is refused, never
    silently widened, chmod-ed or replaced, because a directory this process
    did not create under this name is a directory something else controls.

    :param root: The absolute path :func:`_private_cache_root_path` computed.
    :returns: A key of :data:`_REFUSAL_REASONS`, or ``None`` when the root
        satisfies every requirement.
    """
    try:
        # ``lstat`` and never ``stat``: a symbolic link standing where the root
        # belongs is reported as a link - ``S_ISDIR`` is false for it - so the
        # cache is never reached through one, and the target of such a link is
        # never inspected, created in or executed from.
        entry = os.lstat(root)
    except OSError:
        return _REASON_CACHE_UNUSABLE

    if not stat.S_ISDIR(entry.st_mode):
        return _REASON_CACHE_NOT_DIRECTORY

    if _HAS_POSIX_PERMISSIONS:
        if entry.st_mode & _WRITABLE_BY_OTHERS:
            return _REASON_CACHE_WRITABLE

        # This account and no other: unlike a driver binary, which an
        # administrator may legitimately have installed as root, the cache root
        # is created by this process and a root owned by anybody else - root
        # included - is one this process did not make.  This is the check that
        # matters on a temporary directory without a sticky bit, where another
        # account can remove the root and put its own directory under the same
        # name: the substitute is owned by them, so it is refused here rather
        # than read from and executed.
        if entry.st_uid != os.geteuid():
            return _REASON_CACHE_FOREIGN_OWNER

    return None


def _private_cache_root(browser: str) -> str:
    """Create and verify the private cache root, or refuse to provision.

    Called once per provisioning call, inside the trusted environment window,
    because the root is what the library will read cache metadata from and
    write a downloaded driver into.

    :param browser: The matched browser name, for the refusal message.
    :returns: The absolute path of the verified root.
    :raises DriverProvisioningError: When the root cannot be created, or exists
        and fails verification.
    """
    root = _private_cache_root_path()
    reason = _cache_root_refusal(root)

    # Verified first and created only if that verification found nothing there
    # at all.  Two things follow from that order, and both matter: an existing
    # root is never handed to ``makedirs``, so what stands at that path is
    # judged by the check rather than by whichever error the creation happened
    # to raise over it; and the common case - the root a previous run left -
    # costs one ``lstat`` and no write.
    if reason == _REASON_CACHE_UNUSABLE:
        try:
            # ``mode`` applies to the directories this call creates, and
            # ``0700`` survives any umask because it sets no group or other bit
            # for a umask to clear.  ``exist_ok`` tolerates the race with
            # another worker of the same run creating it first; what either
            # worker then trusts is the verification below.
            os.makedirs(root, mode=_PRIVATE_DIRECTORY_MODE, exist_ok=True)
        except OSError as error:
            raise DriverProvisioningError(
                f"refusing to provision {browser}: "
                f"{_REFUSAL_REASONS[_REASON_CACHE_UNUSABLE]} [{_REASON_CACHE_UNUSABLE}]"
            ) from error

        reason = _cache_root_refusal(root)

    if reason is not None:
        raise DriverProvisioningError(
            f"refusing to provision {browser}: {_REFUSAL_REASONS[reason]} [{reason}]"
        )

    return root


@contextmanager
def _trusted_provisioning_environment() -> Iterator[None]:
    """Run a provisioning call with the library's ambient settings neutralized.

    ``webdriver_manager.core.config`` calls ``load_dotenv()`` at import, so by
    the time anything here runs, a hidden ``.env`` file from the working
    directory upward has already been merged into ``os.environ`` - and that is
    where the library reads its trust settings from, at call time.  Overriding
    them *in the environment* is therefore what rejects that control:
    :data:`_WDM_FORCED_SETTINGS` pins certificate verification on and the
    relocatable cache off, :data:`_WDM_REMOVED_SETTINGS` drops the remaining
    library variables, and :data:`_TRANSPORT_REMOVED_SETTINGS` drops the proxy
    and certificate-authority variables the HTTP stack beneath the library
    honours.  That last group is the one a ``.env`` can put back *after* a
    worker launcher has filtered it out of a child environment, which is why it
    is neutralized here as well as there.

    **The consequence, stated rather than discovered.**  A provisioning
    download therefore goes direct, with no proxy, and is verified against the
    system trust store alone - no private certificate authority and no bundle
    file is honoured.  An environment that requires either cannot provision
    over route two at all: it must supply a pre-provisioned driver in one of
    :data:`_SYSTEM_DRIVER_DIRECTORIES` instead, which is route one and reaches
    no network.  That is a loud failure by choice: honouring an ambient bundle
    or proxy would let a hidden file decide what signs for the driver binary
    this worker is about to execute, and the quiet version of that is a
    downgrade nobody sees.

    ``GH_TOKEN`` is untouched by design: it is an operator-supplied credential
    for the geckodriver release lookup, not a trust-policy control, and
    removing it would break an authenticated lookup rather than harden one.

    The previous state is restored on every path, an exception from the body
    included, so a run that provisioned once looks exactly like a run that
    never did.

    :yields: ``None`` - the context manager is entirely about the environment
        around the call.
    """
    with _ENVIRONMENT_LOCK:
        managed = (
            *(name for name, _ in _WDM_FORCED_SETTINGS),
            *_WDM_REMOVED_SETTINGS,
            *_TRANSPORT_REMOVED_SETTINGS,
            _PATH_VARIABLE,
        )
        previous = {name: os.environ.get(name) for name in managed}

        try:
            for name, value in _WDM_FORCED_SETTINGS:
                os.environ[name] = value

            for name in (*_WDM_REMOVED_SETTINGS, *_TRANSPORT_REMOVED_SETTINGS):
                os.environ.pop(name, None)

            # The library resolves programs by bare name through ``PATH`` on
            # paths this module does not control: ``GeckoDriverManager``
            # consults ``platform.processor()`` to recognise Linux on ARM, and
            # on Python 3.14 that runs ``uname -p`` through whatever ``PATH``
            # says.  A planted ``uname`` earlier in the search order would
            # therefore execute inside this call.  Narrowing ``PATH`` to
            # :data:`_TRUSTED_PATH` for its duration closes that without
            # touching the probe below, which resolves absolute paths and
            # never consults ``PATH`` at all.
            os.environ[_PATH_VARIABLE] = _TRUSTED_PATH

            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class _VerifiedSystemManager(OperationSystemManager):
    """The provisioning library's platform helper, with a safe browser probe.

    Passed as ``os_system_manager=`` to both managers, which is the seam they
    offer for exactly this.  Only one method is overridden, and overriding the
    whole of it is deliberate: the original builds its command table for every
    browser and platform *eagerly*, so merely calling it shells out on Windows
    to decide whether it is running under PowerShell, and then runs the
    selected command through ``subprocess.Popen`` with its ``shell`` argument
    enabled over bare program names such as ``google-chrome`` or ``firefox``.
    Everything else the base class does - operating-system name, architecture,
    ARM detection - is pure platform inspection and is inherited unchanged.
    """

    def get_browser_version_from_os(self, browser_type: str | None = None) -> str | None:
        """Report the installed browser's version, or ``None`` if unknown.

        Absolute paths only, from :data:`_BROWSER_BINARIES`, each verified by
        :func:`_refusal_reason` before it is run; ``shell=False`` so nothing is
        interpreted by a shell; standard input from ``DEVNULL`` so a probe can
        never consume or inherit the run's input; standard error discarded, as
        browsers routinely write diagnostics there; an explicit timeout; and a
        strict anchored pattern over the output.

        :param browser_type: The library's browser key - ``ChromeType.GOOGLE``
            from the Chrome manager, ``"firefox"`` from the Gecko manager.
        :returns: The version string the library expects, or ``None`` when no
            listed binary answered.  ``None`` is the library's own behaviour
            when its probe fails, and it resolves a driver version its own way
            from there, so failing this way degrades rather than breaks.
        """
        pattern = _BROWSER_VERSION_PATTERNS.get(browser_type or "")

        if pattern is None:
            return None

        for binary in _BROWSER_BINARIES.get(browser_type or "", ()):
            if _refusal_reason(binary) is not None:
                continue

            try:
                # Absolute, verified argv; no shell, so nothing in it is
                # interpreted and no name is resolved through ``PATH``.
                probe = subprocess.run(
                    [binary, "--version"],
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                # An unreadable, unstartable or wedged binary is one candidate
                # failing, never the run failing: try the next location.
                continue

            if probe.returncode != 0:
                continue

            match = pattern.match(probe.stdout.decode("utf-8", errors="replace"))

            if match is not None:
                return match.group("version")

        return None


def _archive_refusal(reason: str) -> DriverProvisioningError:
    """Build the refusal for one class of hostile or unusable driver archive.

    The message names the reason class and nothing else - in particular not the
    offending member's name, which is a string the archive's author chose and
    which would otherwise travel into a log or a report artifact.

    :param reason: A key of :data:`_REFUSAL_REASONS`.
    :returns: The exception to raise.
    """
    return DriverProvisioningError(
        "refusing to unpack the provisioned driver archive: "
        f"{_REFUSAL_REASONS[reason]} [{reason}]"
    )


class _ValidatingFileManager(FileManager):
    """The provisioning library's unpacker, with every member validated first.

    Passed as ``file_manager=`` to :class:`DriverCacheManager`, which is the
    seam the library offers for exactly this: ``save_file_to_cache`` writes the
    download, calls ``unpack_archive`` and then reports the binary it found, so
    ``unpack_archive`` is the one place between "bytes arrived" and "a path is
    executed" where the contents can be refused.

    **What the library does, and why it is overridden.**
    ``webdriver_manager/core/file_manager.py:113`` extracts a tar archive with
    ``tar.extractall(to_directory, filter="fully_trusted")``, which explicitly
    turns Python's own safe extraction filter *off*.  A hostile geckodriver
    archive can therefore write outside the cache root through an absolute
    member path or a ``..`` member, or place a symbolic link, a hard link, a
    device node or a FIFO where a later step will follow it.  The zip path is
    less exposed - ``zipfile`` sanitizes member names as it extracts - but it
    performs no type check either.

    **What this class does instead.**  Every member is inspected before
    anything is written: a member whose path is absolute, or whose normalized
    destination escapes the target directory, or which is neither a regular
    file nor a directory, refuses the whole archive with
    :exc:`DriverProvisioningError`.  Nothing partial is left behind, because
    the validation completes before extraction begins.  Tar extraction then
    uses ``filter="data"``, Python's own hardened filter, so the guarantee does
    not rest on this validation alone; zip extraction keeps going through
    ``zipfile``, whose own member sanitization is already safe.

    **One library behaviour deliberately not reproduced.**  The original picks
    ``LinuxZipFileWithPermissions`` on Linux to carry an archive's mode bits
    onto the extracted files.  Mode bits an untrusted archive chose are not
    worth restoring, and nothing is lost by dropping them: both managers
    ``os.chmod(driver_path, 0o755)`` the binary they return, so the driver is
    executable either way, and :func:`_verified_driver_path` is what decides
    whether the result may be executed at all.

    :param os_system_manager: The platform helper the base class keeps, used
        for nothing on this path but retained because the base class stores it.
    """

    def unpack_archive(self, archive_file: Any, target_dir: str) -> list[str]:
        """Unpack one provisioned archive, refusing anything unexpected in it.

        :param archive_file: The library's ``Archive`` wrapper, whose
            ``file_path`` is the downloaded file.
        :param target_dir: The directory to extract into - a path inside the
            verified private cache root.
        :returns: The member names, which is what the library's cache manager
            selects the driver binary from.
        :raises DriverProvisioningError: When the archive is in an unsupported
            format, cannot be read, or carries a member this port refuses to
            write.
        """
        archive_path = str(getattr(archive_file, "file_path", ""))

        if archive_path.endswith(_TAR_ARCHIVE_SUFFIXES):
            return self._unpack_tar(archive_path, target_dir)

        if archive_path.endswith(_ZIP_ARCHIVE_SUFFIXES):
            return self._unpack_zip(archive_path, target_dir)

        # The two managers this port uses download exactly those two formats.
        # Anything else is an archive nobody here can vouch for, and the
        # library's own answer - returning ``None`` and letting the caller fail
        # while looking for a binary - loses the reason.
        raise _archive_refusal(_REASON_ARCHIVE_FORMAT)

    def _unpack_tar(self, archive_path: str, destination: str) -> list[str]:
        """Validate and extract a tar archive.

        :param archive_path: The downloaded archive.
        :param destination: The directory to extract into.
        :returns: The member names, in archive order.
        :raises DriverProvisioningError: When the archive cannot be read or
            carries a refused member.
        """
        try:
            # ``r:*`` covers the gzip the geckodriver releases ship and the
            # bzip2 the library's own reader falls back to, without asking this
            # code to guess which one arrived.
            with tarfile.open(archive_path, mode="r:*") as archive:
                names = []

                for member in archive.getmembers():
                    self._validate_member(
                        member.name,
                        destination,
                        regular=member.isfile() or member.isdir(),
                    )
                    names.append(member.name)

                # Python's own hardened filter, and the line the library
                # disables: it refuses absolute paths, parent-directory
                # traversal and special files on its own, so extraction is
                # guarded twice over and the guarantee does not depend on the
                # loop above having been written correctly.
                archive.extractall(destination, filter="data")
        except (tarfile.TarError, OSError) as error:
            raise _archive_refusal(_REASON_ARCHIVE_UNREADABLE) from error

        return names

    def _unpack_zip(self, archive_path: str, destination: str) -> list[str]:
        """Validate and extract a zip archive.

        :param archive_path: The downloaded archive.
        :param destination: The directory to extract into.
        :returns: The member names, in archive order.
        :raises DriverProvisioningError: When the archive cannot be read or
            carries a refused member.
        """
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.infolist():
                    # A zip records the POSIX mode in the upper half of
                    # ``external_attr``, and an archiver is free to record no
                    # file *type* in it at all - Windows tools store permission
                    # bits only, and Python's own writer stores ``0o600`` for a
                    # member given none.  So the type is a refusal only when it
                    # is present and names something other than a plain file or
                    # a directory; an absent type is the ordinary file the
                    # archive is claiming, and reading it otherwise would refuse
                    # every legitimate Windows driver archive.
                    mode = entry.external_attr >> 16

                    self._validate_member(
                        entry.filename,
                        destination,
                        regular=not stat.S_IFMT(mode)
                        or stat.S_ISREG(mode)
                        or stat.S_ISDIR(mode),
                    )

                names = archive.namelist()
                archive.extractall(destination)
        except (zipfile.BadZipFile, OSError) as error:
            raise _archive_refusal(_REASON_ARCHIVE_UNREADABLE) from error

        return names

    @staticmethod
    def _validate_member(name: str, destination: str, *, regular: bool) -> None:
        """Refuse one archive member unless it may safely be written.

        The three requirements, in the order a hostile archive would try them:

        1. **Not absolute.**  Tested against every platform's spelling - a
           leading separator of either kind and a drive-letter prefix -
           because the archive was written by whoever built it and not by this
           host, so the platform running the check decides nothing.
        2. **Inside the destination.**  The member's path is joined and
           resolved by :func:`_resolves_inside`, and the result has to be the
           destination itself or sit beneath it, which is what closes ``..``
           traversal.
        3. **A regular file or a directory.**  A symbolic link, a hard link, a
           device node, a FIFO or a socket is refused rather than created: the
           driver binary and the metadata beside it are read back and executed,
           and a link is how an archive makes that reach somewhere else.

        :param name: The member's name as the archive records it.
        :param destination: The directory extraction would write into.
        :param regular: Whether the member is a regular file or a directory,
            decided by the caller from the archive's own type information.
        :returns: ``None`` when the member may be written.
        :raises DriverProvisioningError: When it may not.
        """
        if name.startswith(("/", "\\")) or os.path.isabs(name) or _DRIVE_PREFIX.match(name):
            raise _archive_refusal(_REASON_ARCHIVE_ABSOLUTE_MEMBER)

        if not _resolves_inside(os.path.join(destination, name), destination):
            raise _archive_refusal(_REASON_ARCHIVE_ESCAPING_MEMBER)

        if not regular:
            raise _archive_refusal(_REASON_ARCHIVE_IRREGULAR_MEMBER)


def _provisioning_manager(browser: str, manager_factory: Callable[..., Any]) -> Any:
    """Build a provisioning manager whose trust inputs are all supplied here.

    Route two's manager, assembled from three collaborators rather than
    accepting the library's defaults for any of them.  Each keyword closes one
    ambient input, and the library documents every one of them as a seam:

    * ``os_system_manager`` - :class:`_VerifiedSystemManager`, whose browser
      probe runs absolute verified binaries with no shell.  It is passed to the
      *cache manager* as well as to the manager itself, and that is not
      redundant: ``DriverCacheManager.find_driver`` asks its own platform
      helper for the installed browser version on every cache lookup, so a
      cache manager left to build its own would reintroduce the library's
      shell-based probe on the most common path of all - the cache hit.
    * ``cache_manager`` - a cache rooted where :func:`_private_cache_root`
      verified, so no ambient ``HOME`` decides where a driver is read from or
      written to.  The root it *resolved* is then read back and required to sit
      inside that private root, which is the check the paragraph below explains.
    * ``file_manager`` - :class:`_ValidatingFileManager`, which validates every
      archive member before anything is written into that root.

    The cache location is **verified rather than requested**, because
    ``DriverCacheManager.__init__`` (``core/driver_cache.py:18-30``) reads
    ``WDM_LOCAL`` from the environment and applies it *after* the ``root_dir``
    it was handed: a true value there discards that root outright and replaces
    it with ``DEFAULT_PROJECT_ROOT_CACHE_PATH``, a working-directory-relative
    ``.wdm``.  Forcing that variable false is what
    :func:`_trusted_provisioning_environment` does, and the production caller
    builds this manager inside that window - so the private root holds today
    as a consequence of *when* this function is called.  A security property
    that depends on its caller's ordering is one a later caller loses in
    silence, so the resolved root is read back and disagreement is a refusal:
    the property becomes something this function establishes for itself,
    whoever calls it and in whatever order.

    :param browser: The matched browser name, for a refusal message.
    :param manager_factory: ``ChromeDriverManager`` or ``GeckoDriverManager``.
    :returns: The constructed manager, ready for ``.install()``.
    :raises DriverProvisioningError: When the private cache root cannot be
        created, fails verification, or is not where the library's own cache
        resolved.
    """
    system_manager = _VerifiedSystemManager()
    root = _private_cache_root(browser)

    cache_manager = DriverCacheManager(
        root_dir=root,
        file_manager=_ValidatingFileManager(system_manager),
        os_system_manager=system_manager,
    )

    if not _resolves_inside(getattr(cache_manager, _CACHE_ROOT_ATTRIBUTE, None), root):
        raise DriverProvisioningError(
            f"refusing to provision {browser}: "
            f"{_REFUSAL_REASONS[_REASON_CACHE_NOT_PRIVATE]} "
            f"[{_REASON_CACHE_NOT_PRIVATE}]"
        )

    return manager_factory(
        os_system_manager=system_manager,
        cache_manager=cache_manager,
    )


def _bind_verified_path(service: Any, verified: str) -> None:
    """Make ``verified`` the path the service will execute, whatever else says.

    ``selenium/webdriver/common/service.py:79`` computes
    ``self._path = self.env_path() or executable_path``, and ``env_path()``
    returns ``os.getenv(self.DRIVER_PATH_ENV_KEY)`` - a key defaulted to
    ``"SE_CHROMEDRIVER"`` at ``chromium/service.py:50`` and to
    ``"SE_GECKODRIVER"`` at ``firefox/service.py:49``, each written
    ``key = key or "SE_..."`` so that passing ``None`` to the constructor does
    *not* disable it.  An ambient variable would therefore be executed instead
    of the path this module just verified.  Two assignments close that: the key
    is cleared, so nothing later consults the environment for a path, and the
    verified path is assigned through the service's own ``path`` setter, which
    is what overwrites the value the constructor computed.

    :param service: The freshly constructed ``Service`` object.
    :param verified: The path returned by :func:`_verified_driver_path`.
    :returns: ``None``.
    """
    service.DRIVER_PATH_ENV_KEY = None
    service.path = verified


# ---------------------------------------------------------------------------
# OS-level containment of the browser process tree
#
# ``driver.quit()`` is a WebDriver command: it asks the driver executable to
# close the browser, and Selenium's ``Service.stop()`` then terminates the
# driver executable itself.  When that conversation succeeds, everything it
# started is gone.  When it does not - a wedged renderer, a driver that stopped
# answering, a session killed from outside - ``quit()`` raises and the only
# reference to the session is dropped, while the driver executable and the
# browser it started are *still running* with the system under test's
# authenticated session in them.  Dropping a reference is not a process
# lifecycle, so this module owns one.
#
# The containment is established the moment the driver executable exists and
# before the browser does - :func:`_verified_service` wraps the service's own
# ``start`` - and it is *adopted* together with the session.  A handle taken
# later would be taken from an object that may already be unusable, and on
# Windows a job assigned later would not contain the browser processes at all,
# because a process only inherits the job of its parent at creation time.  It
# is released - and the release *verified* - on every teardown path.  Two
# mechanisms, one per platform, both of them the OS facility for exactly this:
#
# * POSIX: the driver executable is started in a process group of its own but
#   in the *same session* as the worker, so the browser it forks inherits that
#   group.  Signalling the group reaches all of it without reaching the worker,
#   and ``killpg(pgid, 0)`` answers "is anything still in it" without sending a
#   signal - which is what turns "we asked it to stop" into "we confirmed it
#   stopped".
# * Windows: the driver executable is assigned to a Job Object limited with
#   ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, which children inherit.  Closing
#   the job handle terminates every process in it, and the immediate process is
#   then waited for to confirm the termination took effect.
#
# Where containment could not be established, teardown reports *unverified*
# rather than assuming success: a tree nobody can reach is exactly the tree an
# operator has to be told about.
# ---------------------------------------------------------------------------

#: How long a signalled process tree is given to disappear before it is killed,
#: and again after the kill before survivors are reported.  Short on purpose:
#: this runs between scenarios, and a teardown that waits a long time on a
#: process that is never going to exit delays every scenario after it.
_CONTAINMENT_GRACE_SECONDS: Final[float] = 5.0

#: How often the wait above re-checks.  Polling rather than blocking, because
#: the thing being waited for is a *group*, which no single call can wait on.
_CONTAINMENT_POLL_SECONDS: Final[float] = 0.05

#: Whether this platform can place a process in a group of its own and signal
#: that group as a unit.  Each name is one thing the containment below needs:
#: ``setpgid`` is what ``subprocess``'s ``process_group`` argument calls,
#: ``getpgid`` is how the group is read back, and ``killpg`` is how it is
#: signalled and inspected.  True on POSIX; false on Windows, where the Job
#: Object below is the equivalent.
_HAS_PROCESS_GROUPS: Final[bool] = (
    hasattr(os, "killpg") and hasattr(os, "getpgid") and hasattr(os, "setpgid")
)

#: The signal a tree that ignored the polite request is killed with.
_CONTAINMENT_KILL_SIGNAL: Final[int] = int(getattr(signal, "SIGKILL", signal.SIGTERM))

#: ``JobObjectExtendedLimitInformation``, the information class carrying the
#: limit below.  From ``winnt.h``; named here because ``ctypes`` offers no
#: symbolic access to it.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final[int] = 9

#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: every process in the job is
#: terminated when the last handle to it closes.  This is the whole reason a
#: job is used rather than a process group - Windows process groups do not kill
#: a tree.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x00002000

#: ``PROCESS_SET_QUOTA | PROCESS_TERMINATE``, the access rights
#: ``AssignProcessToJobObject`` requires on the process handle.
_PROCESS_ASSIGN_ACCESS: Final[int] = 0x0100 | 0x0001

#: What each Win32 handle this module opens refers to, for the one diagnostic
#: that names it.  Spelled once so a log record and the code cannot disagree.
_JOB_HANDLE_SUBJECT: Final[str] = "containment job"
_PROCESS_HANDLE_SUBJECT: Final[str] = "driver process"


class _SessionContainment:
    """What is needed to reclaim one session's whole process tree.

    Exactly one of the two fields is meaningful, decided by platform at capture
    time: ``process_group`` on POSIX, ``job_handle`` on Windows.  ``pid`` is
    carried on both, because it is what a diagnostic has to name and what the
    Windows verification waits on.

    :param pid: The driver executable's process id.
    :param process_group: Its process-group id on POSIX, else ``None``.
    :param job_handle: The kill-on-close Job Object handle on Windows, else
        ``None``.
    :param process: The :class:`subprocess.Popen` Selenium started, used to
        reap the immediate child so it cannot be left a zombie.
    """

    __slots__ = ("job_handle", "pid", "process", "process_group")

    def __init__(
        self,
        pid: int,
        *,
        process_group: int | None = None,
        job_handle: int | None = None,
        process: Any = None,
    ) -> None:
        self.pid = pid
        self.process_group = process_group
        self.job_handle = job_handle
        self.process = process


class _NoLocalProcess:
    """Marker for a session that has no local process tree to reclaim.

    Three states have to be distinguishable at teardown, and collapsing any two
    of them loses something an operator needs:

    * a **handle** - a driver process was contained, and the release can be
      performed and verified;
    * **this marker** - the capture ran and the service exposed no local driver
      process at all, so there is genuinely nothing to reclaim and a silent
      success is the truth;
    * **``None``** - no capture ever ran for this session, so nothing is known
      about what it started.  That is reported as an unconfirmed release, not
      as success: a browser may be running and this module cannot see it.
    """

    __slots__ = ()


#: The one instance of the marker above, compared by identity.
_NO_LOCAL_PROCESS: Final[_NoLocalProcess] = _NoLocalProcess()

#: What the holder's containment slot may carry, spelled once.
_Containment = _SessionContainment | _NoLocalProcess | None


def _win32_prototypes(library: Any, types: Any) -> Any:
    """Declare the exact signature of every ``kernel32`` entry point used.

    ``ctypes`` defaults an undeclared function to ``restype = c_int``, which is
    32 bits wide.  A Windows ``HANDLE`` is pointer-sized, so on a 64-bit host an
    undeclared ``CreateJobObjectW`` or ``OpenProcess`` returns a **truncated**
    handle: the value tests as non-zero, so the code believes it succeeded, and
    every later call on it fails or - worse - names a different object.  The
    same truncation applies in reverse to a handle passed *in* as an argument.
    Declaring both directions is what makes the containment below mean anything
    on a 64-bit Windows host, which is every Windows host this port targets.

    :param library: The loaded ``kernel32``.
    :param types: The ``ctypes.wintypes`` module, whose ``HANDLE`` is the
        pointer-sized type these signatures turn on.
    :returns: ``library``, with the five signatures applied.
    """
    library.CreateJobObjectW.restype = types.HANDLE
    library.CreateJobObjectW.argtypes = [types.LPVOID, types.LPCWSTR]

    library.SetInformationJobObject.restype = types.BOOL
    library.SetInformationJobObject.argtypes = [
        types.HANDLE,
        types.INT,
        types.LPVOID,
        types.DWORD,
    ]

    library.OpenProcess.restype = types.HANDLE
    library.OpenProcess.argtypes = [types.DWORD, types.BOOL, types.DWORD]

    library.AssignProcessToJobObject.restype = types.BOOL
    library.AssignProcessToJobObject.argtypes = [types.HANDLE, types.HANDLE]

    library.CloseHandle.restype = types.BOOL
    library.CloseHandle.argtypes = [types.HANDLE]

    return library


def _kernel32() -> Any:
    """Return the Win32 ``kernel32`` binding, or ``None`` off Windows.

    A function rather than a module-level import so that this module still
    imports on POSIX, where ``ctypes.WinDLL`` does not exist, and so that the
    Windows branches below have one seam a test can stand a double in front of.
    The signatures are declared here, once, because every caller of this
    function goes on to pass or receive a handle.

    :returns: The loaded library with its signatures declared, or ``None`` when
        it is unavailable.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        return _win32_prototypes(ctypes.WinDLL("kernel32", use_last_error=True), wintypes)
    except (ImportError, OSError, AttributeError, ValueError):
        logger.warning("Win32 kernel32 unavailable; browser processes cannot be contained")
        return None


def _containment_keywords() -> dict[str, Any]:
    """Return the ``popen_kw`` that isolates the driver executable.

    Selenium forwards this mapping straight into its own
    :class:`subprocess.Popen` call (``common/service.py:230-239``, which
    expands ``**self.popen_kw`` and passes no process-placement argument of its
    own), so it is the one place the driver process's placement can be asked
    for.

    **The topology, which is the load-bearing decision in this file.**  On
    POSIX the driver executable runs in a **process group of its own** and in
    the **same session as the worker**.  Both halves are required and each
    rules out an alternative:

    * *Its own group* is what gives this module something to signal.  Every
      browser process the driver forks inherits the group, so one ``killpg``
      reaches the whole tree - and it reaches nothing else, so a teardown
      signal can never touch the worker that is running the scenarios, which
      shares neither the group nor its fate.
    * *The same session* is what keeps the tree reclaimable from outside.  A
      run supervisor that cancels a worker signals that worker's process group
      and, failing that, walks the worker's session; a driver started with
      ``start_new_session`` would be a session leader, would leave the worker's
      process group *and* its session, and would survive both - which is the
      shape this must not have.

    ``process_group=0`` is precisely that pair: ``subprocess`` calls
    ``setpgid(0, 0)`` in the child (supported from Python 3.11), so the child's
    group id becomes its own pid while its session is still the parent's.

    :returns: ``{"process_group": 0}`` on POSIX; an empty mapping on Windows,
        where process groups do not kill a tree and containment is the Job
        Object assigned once the process exists.
    """
    if _HAS_PROCESS_GROUPS:
        return {"process_group": 0}
    return {}


def _close_win32_handle(library: Any, handle: Any, subject: str) -> bool:
    """Close one Win32 handle and report whether the close actually happened.

    ``CloseHandle`` returns a status, and discarding it hides the two outcomes
    that matter: a handle that stays open is leaked for the life of the worker,
    and - for the containment job specifically - the close *is* the
    termination, so a close that did not happen means nothing was terminated.

    :param library: The ``kernel32`` binding.
    :param handle: The handle to close.
    :param subject: What the handle refers to, for the diagnostic.
    :returns: ``True`` when the handle is closed.
    """
    try:
        closed = bool(library.CloseHandle(handle))
    except OSError as error:
        logger.warning("Could not close the %s handle: %s", subject, error)
        return False

    if not closed:
        logger.warning("Win32 refused to close the %s handle; it is leaked", subject)

    return closed


def _assign_kill_on_close_job(pid: int) -> int | None:
    """Put one process in a fresh Job Object that kills its tree on close.

    Assignment happens after the process has started, which Windows 8 and
    later allow (jobs nest), and is the Windows counterpart of the POSIX
    process group: children created afterwards inherit the job, so closing the
    single handle returned here terminates the driver executable and every
    browser process under it.

    :param pid: The driver executable's process id.
    :returns: The job handle, or ``None`` when a job could not be created or
        the process could not be assigned - in which case the caller keeps the
        pid-only containment and says so.
    """
    library = _kernel32()

    if library is None:
        return None

    import ctypes  # Imported here for the same reason _kernel32 defers it.

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        """``JOBOBJECT_BASIC_LIMIT_INFORMATION`` from ``winnt.h``."""

        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        )

    class _IoCounters(ctypes.Structure):
        """``IO_COUNTERS`` from ``winnt.h``."""

        _fields_ = tuple((name, ctypes.c_uint64) for name in
                         ("ReadOperationCount", "WriteOperationCount",
                          "OtherOperationCount", "ReadTransferCount",
                          "WriteTransferCount", "OtherTransferCount"))

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` from ``winnt.h``."""

        _fields_ = (
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    job = None
    process_handle = None

    try:
        job = library.CreateJobObjectW(None, None)

        if not job:
            logger.warning("Could not create a containment job for driver process %d", pid)
            return None

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        if not library.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            logger.warning("Could not limit the containment job for driver process %d", pid)
            _close_win32_handle(library, job, _JOB_HANDLE_SUBJECT)
            return None

        process_handle = library.OpenProcess(_PROCESS_ASSIGN_ACCESS, False, pid)

        if not process_handle:
            logger.warning("Could not open driver process %d for containment", pid)
            _close_win32_handle(library, job, _JOB_HANDLE_SUBJECT)
            return None

        if not library.AssignProcessToJobObject(job, process_handle):
            logger.warning("Could not contain driver process %d in a job", pid)
            _close_win32_handle(library, job, _JOB_HANDLE_SUBJECT)
            return None

        return int(job)
    except OSError as error:
        logger.warning(
            "Containment job for driver process %d failed: %s", pid, error, exc_info=True
        )

        if job:
            _close_win32_handle(library, job, _JOB_HANDLE_SUBJECT)

        return None
    finally:
        # The process handle is only needed for the assignment: the job now
        # holds the process, so closing this hands nothing back and closing it
        # on every path is what keeps a failed assignment from leaking one.
        if process_handle:
            _close_win32_handle(library, process_handle, _PROCESS_HANDLE_SUBJECT)


def _capture_containment(service: Any) -> _SessionContainment | _NoLocalProcess:
    """Take ownership of the process tree behind a just-started driver service.

    Called from the wrapper :func:`_install_containment_capture` puts on the
    service's ``start``, so it runs between the driver executable being spawned
    and the WebDriver session being created - which is before any browser
    process exists.  That instant is what makes the Windows job effective: a
    process inherits its parent's job when it is created, so a job assigned
    after the browser existed would not contain the browser.

    Read through :func:`getattr` rather than by attribute access, because the
    only thing this needs is a started local driver process and a service that
    has none - a double in a test, or a service that failed to start - must
    yield ``None`` rather than an error.

    :param service: The service whose ``start`` has just returned.
    :returns: The containment handle, or :data:`_NO_LOCAL_PROCESS` when the
        service exposes no local driver process - which is a state and not a
        failure, and is deliberately not ``None``: ``None`` is reserved for
        "no capture ran", which teardown cannot report as success.
    """
    process = getattr(service, "process", None)
    pid = getattr(process, "pid", None)

    if not isinstance(pid, int) or pid <= 0:
        return _NO_LOCAL_PROCESS

    if _HAS_PROCESS_GROUPS:
        try:
            group = os.getpgid(pid)
        except OSError as error:
            # The driver executable is already gone, or this platform refused
            # the lookup.  The pid is still worth carrying: the immediate
            # process can be reaped even without a group.
            logger.warning(
                "Driver process %d has no reclaimable process group: %s", pid, error
            )
            return _SessionContainment(pid, process=process)

        return _SessionContainment(pid, process_group=group, process=process)

    return _SessionContainment(
        pid, job_handle=_assign_kill_on_close_job(pid), process=process
    )


def _install_containment_capture(service: Any) -> None:
    """Make a service contain the driver process at the instant it starts it.

    Selenium's constructors call ``self.service.start()`` unconditionally
    (``chromium/webdriver.py:55``, ``firefox/webdriver.py:60``), so the service
    cannot be started beforehand and the process cannot be contained by the
    caller before the browser exists.  Wrapping the service instance's own
    ``start`` puts this module exactly in that gap: the real ``start`` returns
    once the driver executable is running and answering, and the browser is not
    created until the WebDriver session handshake that follows - so what runs
    here runs after the process exists and before it has any children.

    On Windows that ordering is the difference between a job that contains the
    browser and a job that contains nothing: a process joins its parent's job
    when it is created, so a job assigned later would never hold the browser
    processes the driver went on to start.

    The wrapper binds on the instance and delegates to the bound method it
    replaced, so nothing about the class is touched and no other service object
    in the process is affected.  The handle is staged rather than stored
    directly, because the session it belongs to does not exist yet;
    :func:`_adopt_session` takes it from there.

    :param service: The freshly constructed ``Service`` object.
    :returns: ``None``.
    """
    start = service.start

    def start_and_contain() -> None:
        """Start the driver executable, then take ownership of its tree.

        :returns: ``None``.
        """
        start()
        _stage_containment(_capture_containment(service))

    service.start = start_and_contain


def _process_group_is_empty(group: int) -> bool:
    """Whether nothing is left in one process group.

    Signal ``0`` performs the permission and existence checks and delivers
    nothing, so this asks the kernel the question rather than inferring the
    answer from what was sent earlier.

    :param group: The process-group id.
    :returns: ``True`` when the group holds no process, ``False`` when it holds
        at least one or when the answer could not be obtained - an
        indeterminate answer is never reported as clean.
    """
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        # Nothing answers to the group id: every process in it has exited and
        # been reaped.  This is the only outcome that means "empty".
        return True
    except PermissionError:
        # Something is there; this process may no longer signal it.
        return False
    except OSError as error:
        logger.warning("Could not inspect process group %d: %s", group, error)
        return False

    # The signal was accepted, which means at least one process is still in
    # the group.  Success here is the *negative* answer, and reading it the
    # other way would report every live tree as already stopped.
    return False


def _await_process_group_exit(group: int, deadline: float) -> bool:
    """Poll a process group until it empties or ``deadline`` passes.

    :param group: The process-group id.
    :param deadline: A :func:`time.monotonic` value to stop at.
    :returns: ``True`` when the group emptied in time.
    """
    while True:
        if _process_group_is_empty(group):
            return True

        if time.monotonic() >= deadline:
            return False

        time.sleep(_CONTAINMENT_POLL_SECONDS)


def _reap_immediate_process(process: Any) -> bool:
    """Wait briefly on the driver executable, and report whether it has exited.

    Selenium's ``Service`` may already have waited; this is the path where it
    did not, because ``quit()`` failed before ``stop()`` ran.  Waiting is what
    keeps an exited process from staying a zombie, and the *return value* is
    what lets a caller with no process group to inspect say honestly whether
    anything is still running.

    :param process: The :class:`subprocess.Popen` Selenium started, or ``None``
        when the containment never knew of one.
    :returns: ``True`` only when this call observed the process exit.  ``None``
        for a process, a timeout, or a wait that could not be performed are all
        ``False``: nothing was observed, so nothing may be claimed.
    """
    if process is None:
        return False

    try:
        process.wait(timeout=_CONTAINMENT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("Driver process did not exit within the teardown grace period")
        return False
    except (OSError, ValueError) as error:
        logger.warning("Could not wait for the driver process: %s", error)
        return False

    return True


def _release_posix_containment(containment: _SessionContainment) -> bool:
    """Terminate and verify a POSIX process group.

    ``SIGTERM`` first, so a driver still able to close its browser cleanly
    does; then a bounded wait, a ``SIGKILL``, and a second wait.  The return
    value is the *verified* state after all of that, never an assumption from
    the fact that a signal was sent.

    :param containment: The handle captured when the driver executable started.
    :returns: ``True`` when the group is confirmed empty, or - where no group
        was ever obtained - when the immediate process is observed to have
        exited.  ``False`` whenever nothing could be observed.
    """
    group = containment.process_group

    if group is None:
        # No group was obtainable, so there is nothing to signal and no way to
        # ask about descendants.  The immediate process is the only thing left
        # to account for, and the answer is honest either way: it exited, so
        # the one process this module knows of is gone; or it did not, and a
        # driver executable is still running with a browser this module cannot
        # reach.  Reporting success for the second case is what would let
        # teardown pass over a live authenticated session.
        exited = _reap_immediate_process(containment.process)

        if not exited:
            logger.error(
                "Driver process %d was never contained in a process group and "
                "cannot be confirmed stopped; a browser process may still be "
                "running with an authenticated session",
                containment.pid,
            )

        return exited

    if _process_group_is_empty(group):
        _reap_immediate_process(containment.process)
        return True

    logger.warning(
        "Browser process group %d outlived its session; terminating it", group
    )

    for signal_number in (signal.SIGTERM, _CONTAINMENT_KILL_SIGNAL):
        try:
            os.killpg(group, signal_number)
        except ProcessLookupError:
            break
        except OSError as error:
            logger.warning(
                "Could not signal browser process group %d: %s", group, error
            )
            break

        # Reaped *before* the group is polled, and that order is load-bearing:
        # an exited process stays in the process table as a zombie until it is
        # waited for, and a zombie is still a member of its group.  Polling
        # first would therefore wait out the whole grace period on a tree that
        # had already stopped, and then report it as a survivor.
        _reap_immediate_process(containment.process)

        if _await_process_group_exit(
            group, time.monotonic() + _CONTAINMENT_GRACE_SECONDS
        ):
            break

    released = _process_group_is_empty(group)

    if not released:
        logger.error(
            "Browser process group %d could not be stopped and may still be "
            "running with an authenticated session",
            group,
        )

    return released


def _release_windows_containment(containment: _SessionContainment) -> bool:
    """Close a kill-on-close job and confirm the driver executable exited.

    :param containment: The handle captured at adoption.
    :returns: ``True`` when the tree is confirmed gone, ``False`` when no job
        could be established at capture time or the process outlived the job's
        closure.
    """
    library = _kernel32()
    job = containment.job_handle

    if library is None or job is None:
        logger.error(
            "Driver process %d was never contained; its browser processes "
            "cannot be confirmed stopped",
            containment.pid,
        )
        _reap_immediate_process(containment.process)
        return False

    # The close *is* the termination, so its status is the difference between
    # "the tree was killed" and "nothing happened": a refused close leaves
    # every process in the job running and the handle open.
    if not _close_win32_handle(library, job, _JOB_HANDLE_SUBJECT):
        return False

    process = containment.process

    if process is None:
        return True

    try:
        process.wait(timeout=_CONTAINMENT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error(
            "Driver process %d survived its containment job and may still be "
            "running with an authenticated session",
            containment.pid,
        )
        return False
    except (OSError, ValueError) as error:
        logger.warning("Could not wait for driver process %d: %s", containment.pid, error)
        return False

    return True


def _release_containment(containment: _Containment) -> bool:
    """Reclaim a session's process tree and report whether that is confirmed.

    The three states of :class:`_NoLocalProcess`'s docstring, answered
    differently on purpose:

    * a handle is released by the platform's mechanism and the result is the
      *verified* state afterwards - which in the common case of a ``quit()``
      that worked is an already-empty group, released silently;
    * :data:`_NO_LOCAL_PROCESS` is a silent ``True``: the capture ran and there
      was no local driver process, so there is nothing that could have
      survived;
    * ``None`` is an unconfirmed release: no capture ran for this session, so
      whatever it started is unaccounted for and saying otherwise would let
      teardown report success over a browser nobody looked for.

    Never raises: this runs in a ``finally`` during teardown, where an
    exception would displace the failure the teardown is already reporting.
    What it does instead is *return* the verified state, so the caller decides
    what an unconfirmed release means.

    :param containment: What the session adopted.
    :returns: ``True`` when no process of the session's tree remains, or when
        there was demonstrably nothing to reclaim.
    """
    if containment is _NO_LOCAL_PROCESS:
        return True

    if not isinstance(containment, _SessionContainment):
        logger.error(
            "This session's driver process was never contained, so its browser "
            "processes cannot be confirmed stopped; one may still be running "
            "with an authenticated session"
        )
        return False

    if _HAS_PROCESS_GROUPS:
        return _release_posix_containment(containment)

    return _release_windows_containment(containment)


def _verified_service(
    browser: str,
    executable_name: str,
    manager_factory: Callable[..., Any],
    service_factory: Callable[..., Any],
) -> Any:
    """Resolve, verify and carry a driver path into a new ``Service``.

    The whole of the provisioning policy in call order: route one first, route
    two only when route one found nothing, one verification gate over both,
    and the environment override closed on the way out.

    :param browser: The matched browser name, ``"chrome"`` or ``"firefox"``,
        used only to name the browser in a refusal.
    :param executable_name: The driver's fixed file name for route one.
    :param manager_factory: The provisioning manager *class* for route two -
        ``ChromeDriverManager`` or ``GeckoDriverManager``.  Constructed by
        :func:`_provisioning_manager` and then asked for ``.install()``, which
        is the surface both of them share.
    :param service_factory: ``ChromeService`` or ``FirefoxService``, called
        with the verified path.
    :returns: The service object, carrying the verified path and primed to
        contain the driver process it starts.
    :raises DriverProvisioningError: When neither route produced a driver
        executable that passes verification, or when the private provisioning
        cache root could not be established.
    """
    # Anything still staged belongs to a driver process no session ever
    # adopted, and this is the first moment a second session could pick it up
    # by mistake.  Released rather than dropped: see
    # :func:`_discard_staged_containment`.
    _discard_staged_containment()

    candidate = _pre_provisioned_driver_path(executable_name)

    if candidate is None:
        # Route two.  The override is held for the whole call, because the
        # library reads every one of those variables lazily - inside
        # ``.install()`` - rather than at import, and because the cache manager
        # below reads two of them in its own constructor.
        with _trusted_provisioning_environment():
            candidate = _provisioning_manager(browser, manager_factory).install()

    verified = _verified_driver_path(candidate, browser)

    # ``popen_kw`` is the only argument beyond the path, and it is not a
    # browser capability: Selenium expands it into the ``subprocess.Popen``
    # call that starts the *driver executable* (``common/service.py:230-239``),
    # so it is where that process's OS-level isolation is asked for.  Nothing
    # about the browser's own configuration is touched, which keeps
    # ``Driver.java:29-41``'s bare construction intact.
    service = service_factory(verified, popen_kw=_containment_keywords())
    _bind_verified_path(service, verified)
    _install_containment_capture(service)

    return service


# ---------------------------------------------------------------------------
# The public surface - two functions, one per static method of ``Driver.java``
# ---------------------------------------------------------------------------


def get_driver() -> webdriver.Remote | None:
    r"""Return this worker's session, creating it on first use.

    The port of ``Driver.getDriver()`` (``Driver.java:21-45``) - *"a re-usable
    utility method which will return same driver instance when we call it"*.
    Every step and every page object reaches its browser through this function,
    and nothing else in the port constructs a session.

    An occupied slot is returned as it is (``:22``): the ``browser`` key is not
    re-read, no binary is provisioned and no browser is started.  Otherwise the
    key is read through :func:`app.config.get_browser` and compared with ``==``
    against ``"chrome"`` and then ``"firefox"``, mirroring a Java ``switch``
    over a ``String``; the matched branch provisions the driver binary,
    constructs the session over it and hands it to :func:`_adopt_session` for
    the maximize and the 10-second implicit wait.

    1. **Create on demand** (``:22``).  When the slot already holds a session
       it is returned as it is: the ``browser`` key is not re-read, no binary
       is provisioned and no browser is started.
    2. **Read the browser name** (``:27``), through
       :func:`app.config.get_browser`, which is the port of
       ``ConfigurationReader.getProperty("browser")``.
    3. **Match it exactly** against ``"chrome"`` and then ``"firefox"``
       (``:30`` and ``:36``).  Comparison is ``==`` on the value as supplied,
       mirroring a Java ``switch`` over a ``String``, which compares with
       ``equals``.
    4. **Resolve, verify, construct, adopt** - the matched branch resolves the
       driver binary under the provisioning policy stated in the module
       docstring, verifies it, builds the session over it, and hands the
       session to :func:`_adopt_session`, which stores it and applies the
       maximize and the 10-second implicit wait in the Java order.
    5. **Return the slot unconditionally** (``:45``).

    An unrecognised name matches neither branch, so the slot stays empty and
    this function returns ``None`` - AAP 0.4.1: *"Any other value fails at
    first driver use, as today."*  Nothing is validated, substituted or raised
    here, and because the create-on-demand guard is simply "the slot is
    empty", a later call tries again exactly as ``:22`` does.  There is no
    "already failed, do not retry" state, since the Java method keeps none.

    :returns: The live session for this worker, or ``None`` when the
        configured browser name is neither of the two this port constructs.
        A caller receiving ``None`` fails on its next interaction, which is
        the source's behaviour and is what surfaces a configuration problem at
        the point of use.
    :raises DriverProvisioningError: When a *matched* browser has no driver
        executable that passes verification.  An unrecognised name never
        reaches provisioning and therefore never raises this: it returns
        ``None`` with nothing provisioned and nothing constructed.
    """
    if _session() is None:
        # Read inside the guard, so a slot hit consults no configuration.  The
        # value may be ``None`` when neither the command-line override nor the
        # properties file supplies the key: a Java ``switch`` over a ``null``
        # ``String`` throws at :29, whereas ``None`` matches neither branch and
        # the scenario fails at its first interaction instead - the outcome AAP
        # 0.4.1 rules for any unrecognised value, so no exception is
        # synthesized here to imitate the throw.
        browser = get_browser()

        if browser == "chrome":
            # Driver.java:30-35.  ``WebDriverManager.chromedriver().setup()``
            # became an explicit path handed to a ``Service``; see the
            # docstring's Selenium 3-to-4 note.  The path is resolved and
            # verified by the provisioning policy - a pre-provisioned
            # ``chromedriver`` first, ``ChromeDriverManager`` as the hardened
            # fallback - and the service carries nothing else.
            service = _verified_service(
                "chrome",
                _CHROME_DRIVER_EXECUTABLE,
                ChromeDriverManager,
                ChromeService,
            )

            _adopt_session(webdriver.Chrome(service=service))

        elif browser == "firefox":
            # Driver.java:37 provisions the *Chrome* binary here, immediately
            # before constructing a ``FirefoxDriver`` at :38 - the defect AAP
            # Conflict 6 corrects, inventoried as deviation 5, because
            # reproducing it would provision the wrong binary.  Restoring the
            # source's exact behaviour means swapping this one manager back -
            # ``GeckoDriverManager`` below for the Chrome one - which is what
            # AAP 0.8 means by "individually reversible".
            service = _verified_service(
                "firefox",
                _GECKO_DRIVER_EXECUTABLE,
                GeckoDriverManager,
                FirefoxService,
            )

            _adopt_session(webdriver.Firefox(service=service))

        # No ``else``.  Driver.java:29-42 has no ``default:`` label, so there
        # is no branch here either: an unmatched name leaves the slot empty
        # and falls through to the unconditional return below.  Nothing is
        # substituted, raised, validated or logged - a record naming the
        # configured value would be a behaviour this port added on its own.

    return _session()


def quit_driver() -> None:
    r"""Close this worker's session and empty the slot.

    The port of ``Driver.closeDriver()`` (``Driver.java:50-55``) - *"make sure
    our driver value is always null after using quit() method"*.
    ``features/environment.py``'s ``after_scenario`` is its only caller and
    must capture failure evidence first: AAP 0.6 fixes capture as happening
    once, on failure, before the driver is quit, so once this returns there is
    no session left to photograph, and nothing is captured or written here.

    With an empty slot this is a no-op (``:51``) - nothing is called and
    nothing is raised - so a scenario that never built a session tears down as
    quietly as one that did.  Otherwise ``quit()`` closes the browser and the
    slot is emptied (``:52-53``), giving the next scenario the fresh session
    the lifecycle contract promises it.

    :returns: ``None``.
    :raises Exception: Whatever the session raises from ``quit()``.  This is
        not a "never raises" function: ``Driver.closeDriver()`` installs no
        handler and AAP 0.1.3's deviation 19 sanctions suppression for
        screenshot capture alone, so a browser that may still be running stays
        visible rather than absorbed.  The slot is cleared either way.  Under
        behave the caller is a hook, so ``runner.run_hook`` reports
        ``HOOK-ERROR in after_scenario: ...``, sets that scenario's status to
        ``hook_error`` and continues the run.
    """
    driver = _session()

    if driver is None:
        return

    # Read before ``quit()``, because that call is what may make the session
    # unusable: afterwards there may be no object left to ask for its process.
    containment = _containment()

    # No ``except`` clause of any kind.  ``Driver.closeDriver()`` installs no
    # handler, so a failed ``quit()`` propagates to the caller exactly as it
    # does in Java, and AAP 0.1.3's deviation 19 sanctions suppression for
    # screenshot capture alone - never for browser teardown.  A session that
    # refused to close may have left a live browser process behind, and that
    # has to be visible rather than absorbed here.
    try:
        driver.quit()
    finally:
        # Driver.java:53 - ``driverPool.remove();``.  The Java line is reached
        # only when ``quit()`` returned; this one is reached on both paths,
        # which is the single deliberate departure from the source statement
        # order.  It is an adjudication rather than an oversight, and AAP
        # 0.3.3 is the authority for it: the lifecycle contract there states
        # that ``after_scenario`` "calls quit(), and clears the slot" so that
        # "no code ever touches a driver after quit()".  Clearing only on
        # success would leave a quit-attempted session in the slot, and
        # :func:`get_driver`'s create-on-demand guard - "the slot is empty" -
        # would hand that dead session straight to the next scenario in this
        # worker, and to every scenario after it, each failing on a browser
        # that is already gone.  Reproducing the Java line exactly would
        # therefore trade one visible teardown failure for a silently poisoned
        # worker, which the contract forbids.  Clearing here changes nothing
        # about the failure itself, which still propagates out of the ``try``
        # unsuppressed; if the source's exact statement order is wanted
        # instead, that is a deliberate relaxation of AAP 0.3.3 and a decision
        # for the plan's owner rather than for this module.
        #
        # The process tree is reclaimed here too, and on the same reasoning:
        # ``quit()`` is a request to the driver executable, and a request that
        # failed leaves the executable and the browser it started running with
        # the system under test's authenticated session in them.  A dropped
        # reference is not a terminated process.  This step never raises - it
        # returns the verified state instead - so it cannot displace a failure
        # already travelling out of the ``try``.
        released = _release_containment(containment)
        _clear_session()

    # Reached only when ``quit()`` returned: a ``quit()`` that raised is
    # already propagating and must not be replaced by this one, which is why
    # the check sits after the ``try``/``finally`` rather than inside it.  A
    # clean ``quit()`` over a tree that is still running is the case the
    # session itself cannot report, and silence here would let teardown be
    # recorded as successful over a live browser.
    if not released:
        raise DriverTeardownError(
            "the browser session was closed but its process tree could not be "
            "confirmed stopped; a browser process may still be running"
        )
