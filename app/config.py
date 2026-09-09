r"""Named accessors for the six configuration keys the Selenium suite reads.

The Python port of the *read surface* of the Java class
``com.testinium.utilities.ConfigurationReader`` (AAP 0.4.1: *"``app/config.py``
| CREATE | ``ConfigurationReader.java`` | Accessors for the six keys tabulated
below, plus the CLI-override precedence stated under ``--browser``"*).

**This is not Flask configuration.**  A Flask application object also carries a
``.config`` attribute, and the two have nothing whatever to do with each other.
This module is ``app.config``: the browser choice, the two URLs, the sign-in
credentials and the expected page title that drive the *browser* suite.
``app/__init__.py`` builds a read-only viewer over the generated report
artifacts and has no business with any key named here, so nothing in this
module reads or writes ``flask_app.config``, and ``create_app()`` must not seed
Flask config from these keys.  That confusion is the single most likely
misreading of this file.

Responsibility split
--------------------
Fixed by AAP 0.4.2: this module is the **only** importer of
``app/utils/properties.py``, and that module is the only reader of the
properties file.  So the split is:

* ``app/utils/properties.py`` owns the file - its name, the ``java.util``
  grammar, the once-per-process load, the cache and the missing-file warning.
* ``app/config.py`` - this module - owns the **six key names**, the six named
  accessors and the behave-userdata override precedence.  It performs no file
  I/O, holds no cache, and contains no path literal.

Its consumers are exactly four, each reaching only the keys its Java original
read (AAP 0.4.2): ``features/steps/employee_steps.py`` (``url``,
``EmplTitle``), ``features/steps/session_steps.py`` (``web.table.url``,
``username``, ``password``), ``features/steps/login_steps.py``
(``web.table.url``) and ``app/automation/driver.py`` (``browser``).

The six keys, and their read sites in the reference implementation
------------------------------------------------------------------
Ten call sites across six keys, verified by grepping every
``ConfigurationReader.getProperty`` call in the reference implementation at
commit ``47e9d697e4a9a85da889f94a846fdf47af28a240``:

``browser`` - :func:`get_browser`
    Read at ``Driver.java:27``.  Selects the browser the driver constructs.
``web.table.url`` - :func:`get_web_table_url`
    Read at ``LoginSD.java:22``, ``Session.java:14`` and
    ``EmployeeStage.java:18``.  The address of the sign-in page.
``url`` - :func:`get_url`
    Read at ``EmployeeStage.java:24``, ``:60`` and ``:93``.  The address of
    the Employee module - a different page from ``web.table.url``.
``username`` - :func:`get_username`
    Read at ``Session.java:15``.  The shared-precondition sign-in name.
``password`` - :func:`get_password`
    Read at ``Session.java:16``.  The shared-precondition password.
``EmplTitle`` - :func:`get_empl_title`
    Read at ``EmployeeStage.java:31``.  The page title the Employee flow waits
    for and asserts.

**Six keys, and only six.**  AAP 0.6 is explicit that *"the configuration
surface stays at six keys"*.  In particular **no key describes the browser's
language or region**: the ``@UPGN-288`` outline asserts the French
``Veuillez renseigner ce champ.`` and neither repository shows how that is
arranged, so AAP 0.8 has the port carry the string verbatim and invent no key
for it - the assertion behaves exactly as it does today, passing under a
French-speaking browser and failing otherwise.  There is also
**no environment-variable layer** (AAP 0.4.1, ``--browser`` row: *"This is the
only override path; no environment layer is added"*), so this module never
consults the process environment.

Key names are literal and **case-sensitive**, exactly as written above.
``EmplTitle`` is mixed case and ``web.table.url`` is dotted - the dotted name
being one reason ``configparser`` is unusable and ``app/utils/properties.py``
implements the grammar by hand.

Precedence: behave userdata first, then the properties file
-----------------------------------------------------------
AAP 0.4.1, ``--browser`` row: the chosen browser is *"passed to each worker as
behave userdata (``-D browser=...``) and read by ``app/config.py``, whose
precedence is userdata first, then the properties file"*.

* :func:`set_userdata` installs that mapping for the current process.  It is
  called once, from ``features/environment.py``'s ``before_all`` hook, with
  ``context.config.userdata``.  ``None`` or an empty mapping clears the slot.
* Every accessor consults the installed userdata first and falls through to
  ``app.utils.properties`` only when the key is **absent** from it.  Presence,
  not truthiness, decides: userdata that maps a key to ``""`` shadows the file
  with ``""``, matching the absent-versus-empty distinction the reader itself
  draws.  A caller that means "no override" must omit the key rather than pass
  an empty value.
* **Calling :func:`set_userdata` is never required.**  The slot starts empty,
  so ``app/automation/driver.py`` - which has no behave context - and unit
  tests that import an accessor directly both take the file-only path
  untouched.
* Userdata keys are the same six literal names; the command line passes
  ``-D browser=chrome``, so the userdata key is exactly ``browser``.  No
  prefix and no namespace is invented.
* The slot is **process-local**, which is all that is needed: scenarios are
  sharded across a process pool (AAP 0.4.1), and each worker installs its own
  userdata from the ``-D browser=...`` it was invoked with.  There is no
  cross-process state to synchronize.

What this module deliberately does not do
-----------------------------------------
Each item is behaviour to preserve, not an omission:

* **No validation of any value.**  ``ConfigurationReader`` validates nothing,
  and ``Driver.java:29-42`` switches on ``browser`` with **no default branch**,
  so an unrecognised value yields a null driver and the scenario fails at first
  use - AAP 0.4.1: *"Any other value fails at first driver use, as today."*
  :func:`get_browser` therefore returns ``"safari"`` unchanged: no exception,
  no warning, no normalization.
* **No default for any key.**  A key the file does not define reads as
  ``None``, mirroring ``Properties.getProperty``'s ``null``
  (``ConfigurationReader:27-29``), so configuration problems surface at the
  point of use rather than at start-up.
* **No raising on a missing key or a missing file.**  The missing file is
  tolerated by ``ConfigurationReader:21-24`` and AAP 0.1.1 records that *"that
  tolerance is behaviour"*.  The single warning it produces is emitted by
  ``app/utils/properties.py``, not here.
* **No caching of its own.**  The one-time load and its cache live in
  ``app/utils/properties.py``; a second cache here would stop
  :func:`set_userdata` taking effect and would double the invalidation surface.
* **No file path literal.**  Only ``app/utils/properties.py`` names the
  properties file.
* **No command-line parsing.**  ``app/cli.py`` owns the options and reaches
  this module only through the userdata each worker is invoked with.

Import boundary (AAP 0.4.2)
---------------------------
This module imports ``app.utils.properties`` and the standard library, and
nothing else.  It must never import Flask, Selenium, behave, ``click``, or
anything from ``app.services``, ``app.reporting``, ``app.pages``, ``app.web``
or ``app.automation`` - ``app.automation`` depends on *this* module for the
``browser`` key, so importing it back would create a cycle.

Secret hygiene
--------------
``password`` is one of the six keys, so **no configured value is ever
logged**.  The one DEBUG record this module emits names recognised *keys* only.
The properties file itself is git-ignored; the committed template beside it -
the ``.example`` copy at the repository root - carries the six keys with empty
values, because no values exist at either revision.  This module names no file:
that string belongs to ``app/utils/properties.py`` alone, prose included, so a
grep for it here finds nothing.

What ``tests/test_config.py`` asserts
-------------------------------------
Stated here so the suite can be implemented faithfully (agent prompt, phase 7):

1. **Six keys, no more** - :data:`CONFIG_KEYS` holds exactly the six literal
   names above and the module exposes exactly six key accessors.  A source
   grep finds no reference to the process environment, to a language or region
   setting, or to any seventh key: those three tokens are deliberately absent
   from this file, prose included, so the grep can be a plain substring test.
2. **Missing key returns None** - with no properties file and no userdata,
   every accessor returns ``None`` and none raises.
3. **Missing file tolerated** - importing this module and calling every
   accessor with no properties file in the working directory neither raises nor
   exits; one warning is logged, by ``app/utils/properties.py``.
4. **File path** - with all six keys written into a properties file in the
   working directory, every accessor returns its value.
5. **Precedence** - file ``browser=firefox`` plus
   ``set_userdata({"browser": "chrome"})`` gives ``"chrome"``; a following
   ``set_userdata(None)`` gives ``"firefox"`` again; userdata does not affect a
   key it does not mention.
6. **No :func:`set_userdata` call required** - in a fresh process every
   accessor returns its file value without the slot ever being installed.
7. **No validation of ``browser``** - ``set_userdata({"browser": "safari"})``
   then :func:`get_browser` returns ``"safari"`` unchanged.
8. **Case sensitivity** - a file containing ``empltitle=x`` does not satisfy
   :func:`get_empl_title`; the key is ``EmplTitle``.
9. **Dotted key** - a file containing ``web.table.url=http://example.invalid``
   satisfies :func:`get_web_table_url`.
10. **Import boundary** - the module imports ``app.utils.properties`` and
    nothing from ``app.services``, ``app.reporting``, ``app.pages``,
    ``app.web`` or ``app.automation``, and no ``flask``, ``selenium`` or
    ``behave``.
11. **Branch coverage** - userdata hit, userdata miss with file hit, both miss,
    ``set_userdata(None)``, and the rejected non-configuration key of
    :func:`get_property`.
"""

import logging
from collections.abc import Mapping
from types import MappingProxyType

from app.utils import properties

__all__ = [
    "CONFIG_KEYS",
    "get_browser",
    "get_empl_title",
    "get_password",
    "get_property",
    "get_url",
    "get_username",
    "get_web_table_url",
    "set_userdata",
]

# Records propagate to the ``app`` package logger, where ``configure_logging()``
# installs the split that sends WARNING and above to stderr.  Only key names are
# ever logged - never a configured value, because ``password`` is one of them.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The six key names
#
# Private, because the accessors are the supported way to reach a key: AAP
# 0.4.2 gives this module four consumers, each reading only the keys its Java
# original read, and a public name constant would invite a fifth caller to
# assemble its own lookup.  :data:`CONFIG_KEYS` is public because
# ``tests/test_config.py`` and the committed properties template are checked
# against each other through it, rather than against a list hard-coded twice.
# ---------------------------------------------------------------------------
_KEY_BROWSER = "browser"
_KEY_WEB_TABLE_URL = "web.table.url"
_KEY_URL = "url"
_KEY_USERNAME = "username"
_KEY_PASSWORD = "password"
_KEY_EMPL_TITLE = "EmplTitle"

#: The six configuration keys, in the order AAP 0.4.1 tabulates them - which is
#: also the order the committed properties template declares them in, so the
#: template and this tuple can be compared element by element.  Exactly six
#: entries: nothing reads a seventh name, so adding one here would be a
#: fabricated requirement (AAP 0.6).
CONFIG_KEYS: tuple[str, ...] = (
    _KEY_BROWSER,
    _KEY_WEB_TABLE_URL,
    _KEY_URL,
    _KEY_USERNAME,
    _KEY_PASSWORD,
    _KEY_EMPL_TITLE,
)

# Membership test for :func:`get_property`.  A frozenset rather than a scan of
# the tuple, so the guard costs nothing on the hot path.
_CONFIG_KEY_SET = frozenset(CONFIG_KEYS)


# ---------------------------------------------------------------------------
# The process-local behave userdata slot
# ---------------------------------------------------------------------------

# The "no userdata installed" value.  A shared immutable empty mapping, so the
# uninstalled state costs no allocation and cannot be mutated into a surprise.
_NO_USERDATA: Mapping[str, str] = MappingProxyType({})

# The installed userdata.  Rebound wholesale by :func:`set_userdata` and never
# mutated in place; every value it is ever bound to is immutable.  That is what
# makes the slot safe without a lock: rebinding a module global is atomic, so a
# concurrent reader observes either the whole previous mapping or the whole new
# one, never a half-populated one.  Each accessor also binds it to a local
# before looking at it, so a rebind between a membership test and a lookup
# cannot make the two disagree.
_userdata: Mapping[str, str] = _NO_USERDATA


def set_userdata(mapping: Mapping[str, str] | None) -> None:
    r"""Install ``mapping`` as this process's behave userdata overrides.

    Called once per process, from ``features/environment.py``'s ``before_all``
    hook, with ``context.config.userdata`` - the mapping behave builds from the
    ``-D name=value`` arguments ``app/services/test_run_service.py`` passes to
    each worker.  ``app/cli.py``'s ``--browser`` option reaches this module by
    that route and no other; no environment layer exists (AAP 0.4.1).

    The mapping is **snapshotted**, not aliased: a copy is taken and wrapped in
    a read-only view, so later mutation of the caller's object - behave's
    userdata is a live ``dict`` subclass - cannot silently change what this
    module reports, and no caller can reach in and edit the installed slot.

    Calling this function is optional.  The slot starts empty and every
    accessor works without it, which is what lets ``app/automation/driver.py``
    read ``browser`` with no behave context in sight.

    Keys are used exactly as given: the userdata key for the browser override
    is the literal ``browser``, with no prefix and no namespace.  Keys outside
    :data:`CONFIG_KEYS` are stored but unreachable, since :func:`get_property`
    serves only the six.

    :param mapping: The userdata to install.  ``None`` or an empty mapping
        clears the slot, restoring the file-only path.
    :returns: ``None``.  Nothing is validated and nothing is raised: an
        unrecognised ``browser`` value must reach the driver and fail there,
        exactly as ``Driver.java:29-42``'s missing default branch arranges.
    """
    global _userdata

    if not mapping:
        # Covers both ``None`` and an empty mapping, which the API treats
        # identically: there is nothing to override with.
        _userdata = _NO_USERDATA
        logger.debug(
            "behave userdata cleared; configuration reads now come from the "
            "properties file alone",
        )
        return

    _userdata = MappingProxyType(dict(mapping))

    # Key names only.  Logging values here would print the configured password.
    recognized = [key for key in CONFIG_KEYS if key in _userdata]
    logger.debug(
        "behave userdata installed: %d entries, overriding configuration "
        "key(s) %s",
        len(_userdata),
        ", ".join(recognized) if recognized else "(none of the six)",
    )


# ---------------------------------------------------------------------------
# The read surface
# ---------------------------------------------------------------------------


def get_property(key: str) -> str | None:
    r"""Return the configured value of ``key``, or ``None`` if it is not set.

    The port of ``ConfigurationReader.getProperty``
    (``ConfigurationReader:27-29``) with the userdata layer of AAP 0.4.1 in
    front of it.  The six accessors below are the supported entry points; this
    function is their shared implementation and is exposed because it mirrors
    the Java method one for one.

    Precedence is **userdata first, then the properties file**.  Membership
    decides, so a key mapped to ``""`` in userdata yields ``""`` rather than
    falling through - see the module docstring.

    ``key`` must be one of :data:`CONFIG_KEYS`.  That guard is what stops this
    function becoming a back door for a seventh configuration key: the surface
    is fixed at six (AAP 0.6), and a caller reaching for anything else has made
    a programming error rather than hit a configuration problem.  Note the
    distinction the two failure modes get:

    * A key **outside** the six raises :class:`ValueError` immediately - it can
      never be satisfied, so failing loudly is the only useful answer.
    * A key **among** the six that is simply not configured returns ``None``,
      never raises, and lets the failure surface at the point of use.  That is
      the tolerance AAP 0.8 freezes as an observable contract.

    No validation, normalization or type coercion is applied to the value: it
    is returned exactly as userdata or the file supplied it.

    :param key: One of :data:`CONFIG_KEYS`, matched exactly and
        case-sensitively.
    :returns: The configured value, or ``None`` when neither the userdata slot
        nor the properties file supplies one.
    :raises ValueError: If ``key`` is not one of :data:`CONFIG_KEYS`.
    """
    if key not in _CONFIG_KEY_SET:
        raise ValueError(
            f"{key!r} is not a configuration key; this port reads exactly "
            f"{', '.join(CONFIG_KEYS)}",
        )

    # Bound once, so a concurrent :func:`set_userdata` cannot let the
    # membership test and the lookup below see two different mappings.
    userdata = _userdata
    if key in userdata:
        return userdata[key]

    # Falls through to the one-time-loaded, per-process cache in
    # ``app/utils/properties.py``, which returns ``None`` for an absent key and
    # tolerates a missing file.
    return properties.get_property(key)


def get_browser() -> str | None:
    r"""Return the ``browser`` key: which browser the driver constructs.

    Read in the reference implementation at ``Driver.java:27``, whose switch
    handles ``"chrome"`` and ``"firefox"`` and has no default branch.  Any
    other value - and ``None``, when neither userdata nor the file supplies
    one - is returned unchanged so that the failure lands at first driver use,
    exactly as it does today (AAP 0.4.1).  Nothing is validated here.

    :returns: The configured browser name, or ``None`` if it is not set.
    """
    return get_property(_KEY_BROWSER)


def get_web_table_url() -> str | None:
    r"""Return the ``web.table.url`` key: the address of the sign-in page.

    Read in the reference implementation at ``LoginSD.java:22``,
    ``Session.java:14`` and ``EmployeeStage.java:18`` - the login steps, the
    shared precondition step other features' backgrounds invoke, and the
    opening step of the Employee flow.  The dotted key name survives the
    properties grammar unchanged.

    :returns: The configured sign-in URL, or ``None`` if it is not set.
    """
    return get_property(_KEY_WEB_TABLE_URL)


def get_url() -> str | None:
    r"""Return the ``url`` key: the address of the Employee module.

    Read in the reference implementation at ``EmployeeStage.java:24``, ``:60``
    and ``:93``.  A separate key from ``web.table.url``: the two address
    different pages and are not interchangeable.

    :returns: The configured Employee module URL, or ``None`` if it is not set.
    """
    return get_property(_KEY_URL)


def get_username() -> str | None:
    r"""Return the ``username`` key: the shared-precondition sign-in name.

    Read in the reference implementation at ``Session.java:15``, the step other
    features' backgrounds invoke to sign in before exercising their own flow.

    :returns: The configured user name, or ``None`` if it is not set.
    """
    return get_property(_KEY_USERNAME)


def get_password() -> str | None:
    r"""Return the ``password`` key: the shared-precondition password.

    Read in the reference implementation at ``Session.java:16``.  The value is
    a credential for the system under test: it is returned to the caller and
    never logged, here or anywhere else in the port.

    :returns: The configured password, or ``None`` if it is not set.
    """
    return get_property(_KEY_PASSWORD)


def get_empl_title() -> str | None:
    r"""Return the ``EmplTitle`` key: the expected Employee page title.

    Read in the reference implementation at ``EmployeeStage.java:31``, where an
    explicit wait asserts the page title after navigating to the Employee
    module.  The key name is mixed case and is matched exactly: a file writing
    ``empltitle`` does not satisfy this accessor.

    :returns: The configured expected title, or ``None`` if it is not set.
    """
    return get_property(_KEY_EMPL_TITLE)
