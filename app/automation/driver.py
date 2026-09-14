r"""Worker-local WebDriver lifecycle - the single owner of every browser session.

The Python port of the Java utility class ``com.testinium.utilities.Driver``
- cited throughout as ``Driver.java`` - at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds as
REFERENCE and this port never modifies.  AAP 0.3.3's ownership table names
this module as the one place a session is ever created or disposed of:
*"Driver creation and disposal | ``app/automation/driver.py``, called from
``features/environment.py`` | One owner for the lifecycle, so no step or page
ever creates or quits a driver."*

Two public functions, one per static method of the Java class:
:func:`get_driver` ports ``Driver.getDriver()`` (``Driver.java:21-45``) and
:func:`quit_driver` ports ``Driver.closeDriver()`` (``Driver.java:50-55``).
``app/automation/__init__.py`` re-exports both under exactly these names, and
they are the whole of this module's surface.  The Java original is a static
utility with a private constructor (``Driver.java:13-15``) and no instance
state, so module-level functions over a module-private holder reproduce its
shape without inventing a class nobody would instantiate.

The lifecycle contract
----------------------
Written here in the same words as ``features/environment.py`` and
``tests/test_driver.py``, because AAP 0.3.3 requires all three to state one
contract identically:

    Each worker process holds one slot for a driver.  ``before_scenario``
    creates a driver into that slot if it is empty; ``after_scenario``
    captures failure evidence, calls ``quit()``, and clears the slot.  Exactly
    one live session exists per worker at any moment, every scenario gets a
    fresh session, and no code ever touches a driver after ``quit()``.

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
where the provisioned path is handed over explicitly: ``.install()`` returns
the path and a ``Service`` object carries it into the constructor.  The
observable result is unchanged - the correct binary is provisioned before the
browser starts - and ``README.md:53``'s manual prerequisite, *"Browser driver
(make sure you have your desired browser driver and class path is set)"*, is
still satisfied by code rather than by the reader.  The *browser* itself must
still be installed (AAP 0.8); only the driver binary is provisioned here.

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
``:31-32``, ``:38``       Provision the binary, then construct
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

What this module deliberately does not do
-----------------------------------------
Each item is behaviour to preserve, not an omission:

* **No default branch, and no validation of the browser name.**
  ``Driver.java:29-42`` has two ``case`` labels and nothing else, so an
  unrecognised value leaves the slot empty and the scenario fails at first use
  - AAP 0.4.1: *"Any other value fails at first driver use, as today."*  There
  is no fallback browser, no substitution and nothing raised from here, and
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
* **No timeout other than the implicit wait.**  ``Driver.java`` sets neither a
  page-load nor a script timeout, so neither is set here.  Explicit waits are
  a separate module with a per-call-site timeout.

Nothing bounds provisioning or a command in wall-clock time
-----------------------------------------------------------
Stated because it is a real limit of this module rather than an oversight in
it: ``webdriver-manager``'s provisioning HTTP - the ``.install()`` calls below
- carries no application timeout, and Selenium's transport passes no timeout
of its own, so its socket deadline is whatever ``socket.getdefaulttimeout()``
returns, which is ``None`` unless something in the process has set it.  Both
``.install()`` and any subsequent WebDriver command can therefore block
indefinitely, and the port supplies no parent cancellation boundary that would
end such a wait from outside.

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
timeout on the two manager calls, and a parent cancellation boundary in
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
"""

import threading

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager

from app.config import get_browser

__all__ = ["get_driver", "quit_driver"]


# ---------------------------------------------------------------------------
# The worker-local session slot - the port of ``Driver.java:17``
#
# ``threading.local()`` is the closest correspondent to the Java field's
# ``InheritableThreadLocal``: per-thread storage, reached through the module
# rather than passed around.  The module docstring explains why one slot is
# exactly right here - the engine runs single-threaded inside each worker
# process, so this holds one session per worker.
#
# Private, together with its three accessors, because the Java class offers no
# way to abandon a session without quitting it and this module offers none
# either.  ``tests/test_driver.py`` monkeypatches the holder to start from an
# empty slot; that seam is intentional and is documented in the docstring.
# ---------------------------------------------------------------------------
_holder = threading.local()

#: The single attribute the holder ever carries.  Named once so that the three
#: accessors below and the test seam cannot drift apart.
_SESSION_ATTRIBUTE = "driver"


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

    # ``manage().window().maximize()`` - Driver.java:33 / :39.
    driver.maximize_window()

    # ``manage().timeouts().implicitlyWait(10, TimeUnit.SECONDS)`` -
    # Driver.java:34 / :40.  The Python binding takes *seconds*, so the
    # literal is 10 and never a millisecond value.
    driver.implicitly_wait(10)


# ---------------------------------------------------------------------------
# The public surface - two functions, one per static method of ``Driver.java``
# ---------------------------------------------------------------------------


def get_driver() -> webdriver.Remote | None:
    r"""Return this worker's session, creating it on first use.

    The port of ``Driver.getDriver()`` (``Driver.java:21-45``), whose comment
    describes it as *"a re-usable utility method which will return same driver
    instance when we call it"*.  Every step and every page object reaches its
    browser through this function, and nothing else in the port constructs a
    session.

    Behaviour, in the order the Java method performs it:

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
    4. **Provision, construct, adopt** - the matched branch installs the
       driver binary, builds the session over it, and hands it to
       :func:`_adopt_session`, which stores it and applies the maximize and
       the 10-second implicit wait in the Java order.
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
    """
    # Driver.java:22 - ``if (driverPool.get() == null)``.  Shaped as the Java
    # guard rather than an early return so that the single exit below is
    # visibly the ``return driverPool.get()`` of :45.
    if _session() is None:
        # Driver.java:27.  Reading it here, inside the guard, is what makes a
        # slot hit cost nothing: the configuration is consulted only when a
        # session actually has to be built.
        #
        # This may be ``None``, when neither the command-line override nor the
        # properties file supplies the key - a missing file being tolerated by
        # design.  One line of the source cannot be reproduced and does not
        # need to be: a Java ``switch`` over a ``null`` ``String`` throws a
        # ``NullPointerException`` at :29, whereas ``None`` here simply
        # matches neither branch and the failure surfaces at the first
        # interaction instead.  The same scenario fails either way, and AAP
        # 0.4.1's ruling is "fails at first driver use, as today", so no
        # exception is synthesized to imitate the throw.
        browser = get_browser()

        if browser == "chrome":
            # Driver.java:30-35.  ``WebDriverManager.chromedriver().setup()``
            # became an explicit path handed to a ``Service``; see the
            # docstring's Selenium 3-to-4 note.
            service = ChromeService(ChromeDriverManager().install())

            # Constructed bare, as Driver.java:32 constructs it: no capability
            # object and no argument of any kind.
            _adopt_session(webdriver.Chrome(service=service))

        elif browser == "firefox":
            # Driver.java:37 provisions the *Chrome* binary here, immediately
            # before constructing a ``FirefoxDriver`` at :38 - the defect AAP
            # Conflict 6 corrects, inventoried as deviation 5, because
            # reproducing it would provision the wrong binary.  Restoring the
            # source's exact behaviour means swapping this one manager back,
            # which is what AAP 0.8 means by "individually reversible".
            service = FirefoxService(GeckoDriverManager().install())

            # Driver.java:38, likewise bare.
            _adopt_session(webdriver.Firefox(service=service))

        # No ``else``.  Driver.java:29-42 has no ``default:`` label, so there
        # is no branch here either: an unmatched name leaves the slot empty
        # and falls through to the unconditional return below.  Nothing is
        # substituted, raised, validated or logged - a record naming the
        # configured value would be a behaviour this port added on its own.

    # Driver.java:44-45 - ``return driverPool.get();``.  Unconditional, and
    # therefore ``None`` when no branch above matched.
    return _session()


def quit_driver() -> None:
    r"""Close this worker's session and empty the slot.

    The port of ``Driver.closeDriver()`` (``Driver.java:50-55``), whose
    comment states the intent as *"make sure our driver value is always null
    after using quit() method"*.  ``features/environment.py``'s
    ``after_scenario`` is its only caller, and it must **capture failure
    evidence first**: AAP 0.6 fixes capture as happening "only on failure,
    once, before the driver is quit", so once this function returns there is
    no session left to photograph.  Nothing is captured, attached or written
    here.

    Behaviour:

    * **Guarded** (``:51``).  With an empty slot this is a no-op: nothing is
      called and nothing is raised, so a scenario that never built a session -
      one whose configured browser matched neither branch, for instance - tears
      down as quietly as one that did.
    * **Quit, then clear** (``:52-53``).  ``quit()`` closes the browser and
      the slot is emptied, so the next :func:`get_driver` builds a fresh
      session.  That is the half of the lifecycle contract which guarantees
      "every scenario gets a fresh session".
    * **A failing ``quit()`` propagates.**  A dead session or an
      already-gone browser process raises on ``quit()``, and nothing here
      catches it: ``Driver.closeDriver()`` installs no handler, and AAP
      0.1.3's deviation 19 authorizes suppression for screenshot capture and
      for nothing else.  A browser that could not be closed may still be
      running, so the failure has to reach the caller; absorbing it would let
      teardown report success over a leaked process.
    * **The slot is cleared even when ``quit()`` fails.**  A session that
      could not be closed must never be handed to a later scenario, which is
      what the contract's "no code ever touches a driver after ``quit()``"
      forbids, so the clear happens in a ``finally`` while the failure travels
      on past it.

    In production the caller is ``after_scenario``, and behave 1.3.3's
    ``runner.run_hook`` catches what a hook raises: it prints
    ``HOOK-ERROR in after_scenario: ...``, counts a hook failure and sets that
    scenario's status to ``hook_error``, then continues with the remaining
    scenarios, so all four artifacts are still written and the port's own exit
    contract still returns 0 for any test outcome.

    :returns: ``None`` on success.
    :raises Exception: Whatever the browser session raises from ``quit()``.
        This function is deliberately **no longer** a "never raises"
        guarantee: the slot is always emptied, but a teardown failure is
        reported rather than hidden.
    """
    driver = _session()

    # Driver.java:51 - ``if (driverPool.get() != null)``.  The guard is the
    # whole of the empty-slot behaviour: no-op, no complaint.
    if driver is None:
        return

    # No ``except`` clause of any kind.  ``Driver.closeDriver()`` installs no
    # handler, so a failed ``quit()`` propagates to the caller exactly as it
    # does in Java, and AAP 0.1.3's deviation 19 sanctions suppression for
    # screenshot capture alone - never for browser teardown.  A session that
    # refused to close may have left a live browser process behind, and that
    # has to be visible rather than absorbed here.
    try:
        # Driver.java:52 - ``driverPool.get().quit();``
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
        _clear_session()
