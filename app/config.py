r"""Named accessors for the six configuration keys the Selenium suite reads.

The Python port of the read surface of ``ConfigurationReader.java`` (AAP
0.4.1).  **This is not Flask configuration**: an application object also
carries a ``.config`` attribute, the two are unrelated, and nothing here
reads or writes ``flask_app.config``.

AAP 0.4.2 makes this module the only importer of ``app/utils/properties.py``,
which owns the file, its grammar, its one-time load and its missing-file
warning.  This module owns the six key names, the six accessors and the
userdata precedence: it performs no I/O, holds no cache, names no path, and
imports nothing beyond that reader and the standard library.

The six keys, and their read sites at reference commit ``47e9d697``
-------------------------------------------------------------------
* ``browser`` - :func:`get_browser` - ``Driver.java:27``
* ``web.table.url`` - :func:`get_web_table_url` - ``LoginSD.java:22``,
  ``Session.java:14``, ``EmployeeStage.java:18``; the sign-in page
* ``url`` - :func:`get_url` - ``EmployeeStage.java:24``, ``:60``, ``:93``; the
  Employee module, a different page from ``web.table.url``
* ``username``, ``password`` - :func:`get_username`, :func:`get_password` -
  ``Session.java:15-16``; the shared-precondition credentials
* ``EmplTitle`` - :func:`get_empl_title` - ``EmployeeStage.java:31``; the page
  title the Employee flow waits for and asserts

Names are literal and case-sensitive, and the surface stays at six (AAP 0.6):
no key describes the browser's language or region, and no
environment-variable layer exists (AAP 0.4.1).

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

* **No validation of the four non-URL values.**  ``ConfigurationReader``
  validates nothing, and ``Driver.java:29-42`` switches on ``browser`` with
  **no default branch**, so an unrecognised value yields a null driver and the
  scenario fails at first use - AAP 0.4.1: *"Any other value fails at first
  driver use, as today."*  :func:`get_browser` therefore returns ``"safari"``
  unchanged: no exception, no warning, no normalization.  ``username``,
  ``password`` and ``EmplTitle`` are likewise returned exactly as configured.
  The two URL keys are the single exception, and it is a security boundary
  rather than a tidy-up - see *The navigation policy* below.
* **No default for any key.**  A key the file does not define reads as
  ``None``, mirroring ``Properties.getProperty``'s ``null``
  (``ConfigurationReader:27-29``), so configuration problems surface at the
  point of use rather than at start-up.
* **No raising on a missing key or a missing file.**  The missing file is
  tolerated by ``ConfigurationReader:21-24`` and AAP 0.1.1 records that *"that
  tolerance is behaviour"*.  The single warning it produces is emitted by
  ``app/utils/properties.py``, not here.  A **malformed** file does reach a
  caller: ``java.util.Properties`` throws on a bad ``\uXXXX`` escape and
  ``ConfigurationReader`` catches only ``IOException``, so the reader raises
  :class:`ValueError` and every accessor here propagates it untouched.
  Nothing in this module catches it - swallowing it would restore a tolerance
  the source does not have - and no configured value or key appears in it,
  because the reader's message is fixed.  A URL key **set** to a value outside
  the navigation policy below is the only other case that raises, and it is
  raised by this module; an absent key is not that case.
* **No caching of its own.**  The one-time load and its cache live in
  ``app/utils/properties.py``; a second cache here would stop
  :func:`set_userdata` taking effect and would double the invalidation surface.
* **No file path literal.**  Only ``app/utils/properties.py`` names the
  properties file.
* **No command-line parsing.**  ``app/cli.py`` owns the options and reaches
  this module only through the userdata each worker is invoked with.

The navigation policy for the two URL keys
------------------------------------------
``web.table.url`` and ``url`` are the only two configured values that become a
**browser request**: five navigation sites hand them to ``driver.get`` -
``features/steps/session_steps.py`` (which then types the configured user name
and password into the page that answers), ``features/steps/login_steps.py``,
and ``features/steps/employee_steps.py`` at three separate sites.  Nothing in
the reference implementation stood between the properties file and that
request (``ConfigurationReader:27-29``, ``Session.java:14``,
``LoginSD.java:22-23``, ``EmployeeStage.java:18-19``, ``:24``, ``:60``,
``:93``), so a configured ``file:`` URL read a local file, a configured
``169.254.169.254`` URL read cloud instance metadata, and a configured
attacker origin received the sign-in credentials.

So a **present** value of either key is returned only if it satisfies the
policy :func:`_navigable_url` implements, and otherwise raises
:class:`ValueError` at the accessor - which is the navigation step, the point
of use where every other configuration failure in this port surfaces.  What
is rejected: a value over 2048 characters; whitespace, an ASCII control or any
Unicode ``Cc``/``Cf``/``Zl``/``Zp``/``Zs`` character; any scheme other than
``http`` and ``https``, which is what turns away ``file:``, ``data:``,
``javascript:``, ``about:``, ``view-source:``, ``ftp:`` and a value with no
scheme at all; user information in front of the host; a missing host; a
non-ASCII host, which must be supplied already punycoded; a port the parser
refuses; a host carrying a percent sign, which covers a percent-encoded name
and an IPv6 zone identifier alike; a host with an empty label, a leading dot
or a second trailing dot; a host written as a number that names no IPv4
address; an IP-literal host that is link-local (the ``169.254.169.254``
instance-metadata address and ``fe80::/10``), unspecified, multicast or
reserved; and the cloud-metadata host names, their subdomains included.

**The host is normalized before the metadata-name and IP-literal tests, never
for the caller.**  Each of those two is an exact-form comparison, and a
browser reaches one destination by several spellings: ``http://2852039166/``,
``http://0251.0376.0251.0376/``, ``http://0xa9fea9fe/``,
``http://169.254.43518/`` and ``http://169.254.169.254./`` all reach the
instance-metadata service that ``http://169.254.169.254/`` is turned away
from, and ``http://a.metadata.google.internal/`` reaches it by name.  So
:func:`_normalized_host` strips the DNS root dot, reads the legacy
``inet_aton`` numeric forms into a canonical address, and refuses the host
forms that cannot be normalized safely - and the accessor still returns the
configured string byte for byte, because the normalization informs the
*decision* and never the value the browser receives.  Nothing here resolves a
name: an accessor that made a DNS query would itself perform the outbound
request the policy exists to control, and AAP 0.4.2 gives this module no I/O.

Two properties of that policy are load-bearing:

* **A missing key still travels to the browser untouched.**  ``None`` is
  returned unvalidated, because AAP 0.4.1 fixes that *"a missing key returns
  null so failures surface at the point of use"* and AAP 0.8 freezes the
  tolerant load as an observable contract.  An absent key therefore reaches
  ``driver.get(None)`` and fails there exactly as it did before this policy
  existed.
* **Loopback and private addresses are permitted, deliberately.**  The system
  under test is an Odoo instance whose address neither repository supplies
  (AAP 0.2.2), and a QA instance routinely sits on ``localhost`` or on an
  internal network.  An origin allowlist would need a seventh properties key,
  which AAP 0.4.1 (*"with no additional keys"*) and AAP 0.6 (*"the
  configuration surface stays at six keys"*) both forbid, so the policy is
  the fixed, key-free one above: no allowlist, no process-environment read
  and no command-line option is involved in it.

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
logged**.  Every record this module emits, and every message it raises, carries
recognised *key names* and fixed reasons only: the DEBUG record of
:func:`set_userdata` lists key names, and a URL the navigation policy rejects
is reported by its key and the reason, with the value withheld - deliberately,
because that text reaches the console log and, through the failing step, the
published report artifacts.
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
12. **The navigation policy** - every rejection class listed under *The
    navigation policy* above raises :class:`ValueError` from
    :func:`get_web_table_url` and :func:`get_url`, by the properties-file path
    and by the userdata path alike; every accepted shape is returned unchanged,
    including a loopback and a private host; the message never contains the
    offending value; and ``None`` is still returned, without raising, for an
    absent key and for a missing file.
13. **Host normalization** - the alternative spellings of a blocked
    destination are rejected with it: the integer, dotted-octal, dotted-hex,
    two-part and three-part forms of ``169.254.169.254`` and of ``0.0.0.0``, a
    trailing-dot address, a subdomain and a trailing-dot form of a
    cloud-metadata name, a percent-encoded host, an IPv6 zone identifier, a
    numeric host that overflows and a doubled-dot host.  And the normalization
    turns nothing else away: a name whose labels look numeric but which is no
    address, a punycoded name, and the numeric forms of a loopback or private
    address - permitted because the canonical address is - are all returned
    unchanged.
"""

import ipaddress
import logging
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from urllib.parse import urlsplit

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

_CONFIG_KEY_SET = frozenset(CONFIG_KEYS)


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

    Called from ``features/environment.py``'s ``before_all`` hook with
    ``context.config.userdata`` - what behave builds from the ``-D name=value``
    arguments a worker is invoked with, and the route by which ``app/cli.py``'s
    ``--browser`` reaches this module.  AAP 0.4.1 makes it the only override
    path.  The slot is process-local, and calling this function is optional:
    it starts empty, so ``app/automation/driver.py``, which has no behave
    context, reads ``browser`` from the properties file alone.

    The mapping is snapshotted into a read-only view rather than aliased, so
    later mutation of behave's live userdata ``dict`` cannot change what this
    module reports.  Keys are used exactly as given - the browser override's
    key is the literal ``browser``, with no prefix - and keys outside
    :data:`CONFIG_KEYS` are stored but unreachable, since :func:`get_property`
    serves only the six.

    :param mapping: The userdata to install.  ``None`` or an empty mapping
        clears the slot, restoring the file-only path.
    :returns: ``None``.  Nothing is validated and nothing is raised: an
        unrecognised ``browser`` value must reach the driver and fail there,
        as ``Driver.java:29-42``'s missing default branch arranges.
    """
    global _userdata

    if not mapping:
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
# The navigation policy for the two URL keys
#
# Everything here is private and pure: every helper reads no file, consults
# nothing outside its arguments and holds no state - in particular nothing
# here resolves a name, so the decision costs no network round trip and the
# module keeps the no-I/O contract AAP 0.4.2 gives it.  The policy is
# therefore a function of the configured string alone and can be reasoned
# about - and tested - one rejection class at a time.  The names are private
# because the published surface is fixed at the nine entries of ``__all__``
# (AAP 0.6, and ``tests/test_config.py`` asserts the equality): a caller that
# wants a checked URL asks the accessor for it.
#
# See "The navigation policy for the two URL keys" in the module docstring for
# what is rejected and why loopback and private addresses are not.
# ---------------------------------------------------------------------------

# Ceiling on a configured URL, in characters.  Well above any address a QA
# instance needs and below the point at which a value stops being a URL and
# starts being a payload aimed at whatever parses it downstream.
_MAX_URL_CHARACTERS = 2048

# The only two schemes a configured value may carry into ``driver.get``.  Both
# URL keys address pages of a web application (AAP 0.2.2), so http and https
# are the whole of what either has ever needed; every other scheme a browser
# understands - ``file:``, ``data:``, ``javascript:``, ``about:``,
# ``view-source:``, ``ftp:`` - reads something other than that application, and
# an empty scheme is rejected by the same test.
_NAVIGABLE_SCHEMES = frozenset({"http", "https"})

# Unicode general categories rejected anywhere in a configured URL: control
# (``Cc``), format (``Cf``, which carries the bidirectional overrides and the
# zero-width joiners a spoofed host hides behind), line and paragraph
# separators (``Zl``, ``Zp``) and every space separator (``Zs``).  A URL
# carrying one of these is either a typo or an attempt to smuggle a second
# record into a log line or a second header into a request.
_NON_PRINTING_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})

# Host names that answer as a cloud instance-metadata service, which serves
# credentials to anything that can reach it.  The IP form of the same service
# is caught as a link-local address by :func:`_ip_host_rejection`; these are
# the names that resolve to it without looking like an address at all.  They
# are matched as *domains* rather than as exact labels by
# :func:`_is_metadata_host`, because each of the four also answers under a
# subdomain of itself.
_METADATA_HOST_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

# The four bytes of an IPv4 address, which is how many dotted parts the classic
# ``inet_aton`` grammar :func:`_legacy_ipv4_host` implements tops out at.
_IPV4_BYTES = 4

# How many significant digits one part of a numeric host may carry.  Eleven,
# because the largest 32-bit value is 37777777777 in octal - eleven digits -
# and shorter in the other two bases, so a longer run of digits cannot name an
# address however it is read.
_MAX_NUMERIC_HOST_PART_DIGITS = 11

# The digits each legacy numeric host part may be written in.  Membership tests
# rather than ``str.isdigit()``, which is true of the Arabic-Indic and other
# non-ASCII digits that ``int`` then accepts: a host is known ASCII by the time
# these are consulted, and stating the alphabet keeps that independent of it.
_DECIMAL_DIGITS = frozenset("0123456789")
_OCTAL_DIGITS = frozenset("01234567")
_HEXADECIMAL_DIGITS = frozenset("0123456789abcdef")

# One fixed reason string per rejection class.  Fixed, because the reason is
# reported to the caller and must describe the *class* of the problem without
# reproducing any part of the configured value (see "Secret hygiene").
_REASON_TOO_LONG = f"it is longer than {_MAX_URL_CHARACTERS} characters"
_REASON_NON_PRINTING = (
    "it contains whitespace, a control character or another non-printing "
    "character"
)
_REASON_UNPARSEABLE = "it is not a parseable URL"
_REASON_SCHEME = "its scheme is not http or https"
_REASON_USERINFO = "it carries user information in front of its host"
_REASON_NO_HOST = "it names no host"
_REASON_NON_ASCII_HOST = (
    "its host is not ASCII; an internationalized host must be supplied "
    "already punycoded"
)
_REASON_PORT = "its port is not a number between 0 and 65535"
_REASON_PERCENT_IN_HOST = (
    "its host contains a percent sign, which is neither a host name nor an "
    "address literal this policy accepts"
)
_REASON_MALFORMED_HOST = (
    "its host has an empty label, a leading dot or more than one trailing dot"
)
_REASON_NUMERIC_HOST = (
    "its host is written as a number that names no IPv4 address"
)
_REASON_METADATA_HOST = "its host is a cloud instance-metadata name"
_REASON_LINK_LOCAL = "its host is a link-local IP address"
_REASON_UNSPECIFIED = "its host is the unspecified IP address"
_REASON_MULTICAST = "its host is a multicast IP address"
_REASON_RESERVED = "its host is a reserved IP address"


def _numeric_host_part(part: str) -> tuple[bool, int | None]:
    """Read one dot-separated host ``part`` as a number.

    The grammar ``inet_aton`` accepts and every browser's URL parser inherits:
    hexadecimal on a ``0x`` prefix, octal on a leading zero, decimal
    otherwise.  Two facts come back rather than one, because the policy needs
    both and they are not the same question:

    * **Is the part a number at all?**  This is what decides whether the host
      is an address attempt or a registered name, and a browser draws the line
      in the same place: ``0xg`` is a name, while ``09`` is a number - one
      that happens to have no octal reading.
    * **What is its value?**  ``None`` when the part is a number the grammar
      cannot read: an all-digit part with an ``8`` or a ``9`` behind a leading
      zero (``09``, ``0129``), or one with more significant digits than a
      32-bit address can be written in.

    ``0`` on its own is decimal zero and ``0x`` on its own is hexadecimal
    zero, both because that is how the URL parser reads them - so
    ``http://0x/`` is a spelling of ``0.0.0.0``.

    :param part: One dot-separated part of a host, already lowercased by the
        parser and already known to be ASCII.
    :returns: ``(False, None)`` for an ordinary label, ``(True, None)`` for a
        number with no reading, and ``(True, value)`` otherwise.
    """
    lowered = part.casefold()

    if lowered.startswith("0x"):
        digits = lowered[2:]
        if not digits:
            return (True, 0)
        if not _HEXADECIMAL_DIGITS.issuperset(digits):
            # ``0xg`` and its kind: a label that merely opens like a number.
            return (False, None)
        return _numeric_host_value(digits, 16)

    if not lowered or not _DECIMAL_DIGITS.issuperset(lowered):
        return (False, None)

    if len(lowered) > 1 and lowered.startswith("0"):
        digits = lowered[1:]
        if not _OCTAL_DIGITS.issuperset(digits):
            # All digits, so a number - but ``09`` is not an octal one, and a
            # browser fails the whole host rather than resolving it by name.
            return (True, None)
        return _numeric_host_value(digits, 8)

    return _numeric_host_value(lowered, 10)


def _numeric_host_value(digits: str, base: int) -> tuple[bool, int | None]:
    """Convert ``digits`` in ``base``, refusing more digits than can fit.

    The length test is what keeps the conversion bounded: a 32-bit address is
    at most eleven octal digits, ten decimal ones or eight hexadecimal ones,
    so a longer run of *significant* digits cannot name one whatever the base
    - and leading zeros are discounted first, because ``0000000000000001`` is
    a long way of writing ``1``.  Refusing by length also keeps every
    conversion far below the interpreter's integer-string limit, so this
    module raises nothing of its own from a digit run.

    :param digits: The digits to convert, with no base prefix.
    :param base: 8, 10 or 16, as :func:`_numeric_host_part` determined.
    :returns: ``(True, value)``, or ``(True, None)`` when the digits are too
        many to name an address.
    """
    significant = digits.lstrip("0")
    if len(significant) > _MAX_NUMERIC_HOST_PART_DIGITS:
        return (True, None)

    return (True, int(digits, base))


def _legacy_ipv4_host(host: str) -> tuple[bool, ipaddress.IPv4Address | None]:
    """Interpret ``host`` as a legacy numeric IPv4 address.

    ``ipaddress`` accepts only the canonical four-part decimal form, so every
    other numeric form a browser resolves - ``http://2130706433/``,
    ``http://0177.0.0.1/``, ``http://0x7f000001/``, ``http://169.254.43518/``
    - is reported by it as "not an address" and, before this function existed,
    fell straight through the IP policy while the browser still reached the
    destination.  ``169.254.169.254`` is ``2852039166``, so that fall-through
    reached the instance-metadata service the policy exists to block.  This is
    the classic ``inet_aton`` interpretation, stated explicitly: one to four
    parts, each decimal, octal or hexadecimal, every part but the last a
    single byte, and the last part absorbing the bytes the missing parts would
    have carried.

    :param host: A host with no percent sign and no trailing root dot, already
        lowercased by the parser and already known to be ASCII.
    :returns: ``(False, None)`` when the host is a registered name - one part
        that is not a number at all makes the whole host one, which is what
        keeps ``2130706433.example.invalid``, ``09.example.invalid`` and every
        ``xn--`` name falling through untouched; ``(True, None)`` when every
        part is a number but they name no address, because a part has no
        reading, there are more than four of them, a leading part exceeds one
        byte or the last part overflows the bytes left for it; and
        ``(True, address)`` with the canonical address otherwise.
    """
    parts = host.split(".")

    values: list[int] = []
    unreadable = False
    for part in parts:
        is_number, value = _numeric_host_part(part)
        if not is_number:
            # One ordinary label makes the whole host a name, wherever it
            # sits, so the scan stops here: this is what keeps
            # ``2130706433.example.invalid`` and ``09.example.invalid`` out of
            # the address reading entirely.
            return (False, None)
        if value is None:
            # A number with no reading.  The scan continues rather than
            # returning, because a later part may yet be an ordinary label and
            # make the host a name after all.
            unreadable = True
        else:
            values.append(value)

    if unreadable or len(parts) > _IPV4_BYTES:
        return (True, None)

    *leading, last = values
    if any(value > 0xFF for value in leading):
        return (True, None)

    # The last part carries every byte the omitted parts would have: three of
    # them for ``http://2130706433/``, one for a full four-part host.
    if last >= 1 << (8 * (_IPV4_BYTES - len(leading))):
        return (True, None)

    packed = last
    for index, value in enumerate(leading):
        packed |= value << (8 * (_IPV4_BYTES - 1 - index))

    return (True, ipaddress.IPv4Address(packed))


def _normalized_host(host: str) -> tuple[str | None, str | None]:
    """Return ``host`` in the form the policy's host tests are written for.

    The one place a host is made comparable, and it runs before the
    instance-metadata and IP tests rather than beside them: each of those
    tests is an exact-form comparison, and a host the browser resolves to a
    blocked destination while reaching neither test is a bypass rather than an
    accepted value.  Three forms did exactly that - a percent-encoded name, a
    name carrying the DNS root dot, and every legacy numeric address form -
    and each is answered here.

    Exactly one element of the returned pair is ever set: a canonical host to
    carry on testing, or the fixed reason the host is already out of policy.

    **The normalization decides; it is never returned to the caller.**
    :func:`_navigable_url` hands the browser the configured string byte for
    byte, because that string is what the properties file holds and what the
    reference implementation navigated to.

    :param host: The host component of a parsed URL, already lowercased by the
        parser and already known to be ASCII.
    :returns: ``(canonical_host, None)`` when the host may be tested further,
        or ``(None, reason)`` when it is out of policy on its form alone.
    """
    if "%" in host:
        # Percent-encoding is not a legitimate authority form for either URL
        # key, and decoding it would only reproduce a name the tests below
        # would then have to see - ``%6d%65%74%61%64%61%74%61`` is
        # ``metadata``.  Refusing it is both simpler and stricter, and it
        # turns away an IPv6 zone identifier too: a link-scoped interface is
        # not where the system under test lives.
        return (None, _REASON_PERCENT_IN_HOST)

    if ":" in host:
        # An IPv6 literal, which the parser hands over without its brackets.
        # It has no DNS labels and no legacy numeric form, so neither the dot
        # analysis nor the ``inet_aton`` reading below applies to it; the
        # embedded-IPv4 forms it can carry are already resolved by
        # :func:`_embedded_addresses`.
        return (host, None)

    # Exactly one trailing dot is the DNS root label, which makes
    # ``169.254.169.254.`` and ``metadata.google.internal.`` the same
    # destinations as their dotless forms; a host that is nothing but that dot
    # names nothing.
    without_root = host[:-1] if host.endswith(".") else host
    if not without_root:
        return (None, _REASON_MALFORMED_HOST)

    if "" in without_root.split("."):
        # A doubled dot, a leading dot or a second trailing dot.  Refused
        # rather than interpreted: no browser resolves an empty label, and
        # guessing which dot was meant is how a normalizer starts admitting
        # hosts its own tests never saw.
        return (None, _REASON_MALFORMED_HOST)

    numeric, address = _legacy_ipv4_host(without_root)
    if numeric:
        if address is None:
            return (None, _REASON_NUMERIC_HOST)
        # Handed on in canonical dotted-decimal form, so the IP policy judges
        # ``http://2852039166/`` exactly as it judges
        # ``http://169.254.169.254/``.
        return (str(address), None)

    return (without_root, None)


def _is_metadata_host(host: str) -> bool:
    """Return whether ``host`` is in a cloud instance-metadata domain.

    The domain rather than the exact name: each of the four names in
    :data:`_METADATA_HOST_NAMES` also answers under a subdomain of itself, so
    ``a.metadata.google.internal`` reaches the same credential-serving
    endpoint that ``metadata.google.internal`` does.  A host is tested after
    :func:`_normalized_host` has removed the root dot, which is what makes
    ``metadata.google.internal.`` meet this test as well.

    :param host: A canonical host, as :func:`_normalized_host` returned it.
    :returns: ``True`` when the host is one of those names or sits inside one
        of them, ``False`` otherwise - including for every IP literal, none of
        which can match a name.
    """
    return any(
        host == name or host.endswith(f".{name}")
        for name in _METADATA_HOST_NAMES
    )


def _embedded_addresses(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[ipaddress.IPv4Address, ...]:
    """Return the IPv4 addresses an IPv6 literal carries inside it.

    An IPv6 literal can name an IPv4 destination three ways - as an
    IPv4-mapped address, as a 6to4 address and as a Teredo address - and a
    browser follows all three.  Each embedded address therefore has to face
    the same predicates as the literal itself, or ``2002:a9fe:a9fe::`` would
    reach the instance-metadata service that ``169.254.169.254`` is turned
    away from.  The standard library resolves the mapped form for
    ``is_link_local`` already; the other two it leaves to the caller.

    :param address: The parsed host address, of either family.
    :returns: The embedded IPv4 addresses, in a fixed order, or an empty tuple
        for an IPv4 literal and for an IPv6 literal that embeds nothing.
    """
    embedded: list[ipaddress.IPv4Address] = []

    # ``getattr`` rather than a family test: the three properties exist only on
    # the IPv6 class, and asking for them by name keeps this helper working for
    # either family without branching on the type.
    for name in ("ipv4_mapped", "sixtofour"):
        candidate = getattr(address, name, None)
        if candidate is not None:
            embedded.append(candidate)

    # ``teredo`` is a (server, client) pair; the client half is the tunnelled
    # destination, and the server half is an address the host talks to, so
    # both are policed.
    teredo = getattr(address, "teredo", None)
    if teredo is not None:
        embedded.extend(teredo)

    return tuple(embedded)


def _ip_host_rejection(host: str) -> str | None:
    """Return why an IP-literal ``host`` is out of policy, or ``None``.

    A host that is not an IP literal is out of this function's remit and
    yields ``None``: a registered name is resolved by the browser, and no
    resolution happens here - deliberately, since resolving a name to decide
    whether to navigate to it would itself be the outbound request the policy
    exists to control, and its answer could change before the browser asked.

    **Loopback is permitted before anything else is tested**, and that order
    is deliberate: the standard library reports ``::1`` as a reserved address
    as well as a loopback one, and a QA Odoo instance on ``localhost`` is the
    ordinary case (AAP 0.2.2).  Private and shared-address-space hosts fall
    through to ``None`` for the same reason - an internal QA network is
    ordinary - while the instance-metadata address is not a loopback address
    and so meets the link-local test that turns it away.

    :param host: A canonical host, as :func:`_normalized_host` returned it -
        which is what brings the legacy numeric forms of an address here in a
        shape ``ipaddress`` can read, so ``http://2852039166/`` is judged as
        the ``169.254.169.254`` it is.
    :returns: The fixed reason this host is out of policy, or ``None`` when it
        is not an IP literal at all or is one the policy permits.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A registered name, an IPvFuture literal or anything else that is not
        # an address: nothing for this function to decide.
        return None

    for candidate in (address, *_embedded_addresses(address)):
        if candidate.is_loopback:
            continue
        if candidate.is_link_local:
            return _REASON_LINK_LOCAL
        if candidate.is_unspecified:
            return _REASON_UNSPECIFIED
        if candidate.is_multicast:
            return _REASON_MULTICAST
        if candidate.is_reserved:
            return _REASON_RESERVED

    return None


def _url_rejection(value: str) -> str | None:
    """Return why ``value`` is not a navigable URL, or ``None`` if it is.

    The whole of the policy, as one ordered sequence of tests over a present
    value.  The order is not incidental: the character test runs before the
    value is parsed, because the parser silently drops tabs and newlines from
    a URL, so a value inspected only after parsing would be judged on a string
    the configuration file does not contain.

    :param value: A configured URL, exactly as userdata or the properties file
        supplied it.  Never ``None`` - that case never reaches here.
    :returns: The fixed reason it is out of policy, or ``None`` when every test
        passes and the value may be navigated to.
    """
    if len(value) > _MAX_URL_CHARACTERS:
        return _REASON_TOO_LONG

    for character in value:
        # ``isspace()`` and the category test overlap and neither subsumes the
        # other: the first catches the ASCII whitespace that is categorised
        # ``Cc``, the second catches the zero-width and bidirectional
        # formatting characters that are not whitespace at all.
        if (
            character.isspace()
            or unicodedata.category(character) in _NON_PRINTING_CATEGORIES
        ):
            return _REASON_NON_PRINTING

    try:
        parts = urlsplit(value)
    except ValueError:
        # An unterminated IPv6 literal is the reachable case.  The parser's own
        # message is not reused, because it can quote the offending text.
        return _REASON_UNPARSEABLE

    # The parser lowercases the scheme; ``casefold`` states the intent anyway,
    # so a change in that behaviour cannot silently admit ``FILE:``.
    if parts.scheme.casefold() not in _NAVIGABLE_SCHEMES:
        return _REASON_SCHEME

    # Presence, not truthiness: ``http://@host/`` parses to an empty user name,
    # which is still user information in front of the host.  The ``netloc``
    # test is the backstop for any authority shape whose userinfo the parser
    # does not split out.
    if (
        parts.username is not None
        or parts.password is not None
        or "@" in parts.netloc
    ):
        return _REASON_USERINFO

    host = parts.hostname
    if not host:
        return _REASON_NO_HOST

    if not host.isascii():
        # No implicit IDNA encoding: which of the several encodings a browser
        # would apply is not this module's decision to make on the caller's
        # behalf, and a host that looks like one name and resolves as another
        # is exactly what this policy is for.
        return _REASON_NON_ASCII_HOST

    try:
        # ``SplitResult.port`` parses lazily, so this access *is* the parse and
        # an out-of-range or non-numeric port raises here rather than at
        # ``urlsplit``.  The result is discarded because the policy has no
        # interest in which port was configured, only in whether the authority
        # names a usable one; the parser's own message is not reused because it
        # quotes the offending text.
        _ = parts.port
    except ValueError:
        return _REASON_PORT

    # The host is made comparable once, here, and every test below is written
    # against the canonical form: the instance-metadata and IP tests are
    # exact-form comparisons, so a host the browser resolves to a blocked
    # destination while matching neither of them would otherwise be accepted.
    canonical, host_rejection = _normalized_host(host)
    if canonical is None:
        return host_rejection

    if _is_metadata_host(canonical):
        return _REASON_METADATA_HOST

    return _ip_host_rejection(canonical)


def _navigable_url(key: str, value: str | None) -> str | None:
    """Return ``value`` for ``key`` if the navigation policy permits it.

    The one gate the two URL accessors return through.

    :param key: The configuration key the value came from - one of
        :data:`CONFIG_KEYS`, and the only part of the situation the raised
        message is allowed to name.
    :param value: The configured value, or ``None``.
    :returns: ``None`` unchanged when ``value`` is ``None``, and otherwise the
        value exactly as configured: no normalization, no trailing-slash
        adjustment and no re-encoding, because the browser must receive the
        string the file holds.
    :raises ValueError: When a present value is out of policy.  Raised at the
        accessor, which is the navigation step - the point of use where AAP
        0.4.1 has every other configuration failure surface - so the browser
        is never asked for the destination and, at
        ``features/steps/session_steps.py``, the configured credentials are
        never typed.
    """
    if value is None:
        # The AAP-frozen tolerance: a missing key, or a missing properties
        # file, reaches ``driver.get`` untouched and fails there (AAP 0.4.1,
        # AAP 0.8).  Validating it would replace that failure with an earlier
        # one and change an observable contract.
        return None

    rejection = _url_rejection(value)
    if rejection is None:
        return value

    # Logged as well as raised: the raise reaches the report artifact through
    # the failing step, while this record is what a CI console shows the
    # engineer who wrote the properties file.  Both carry the key and the
    # reason; neither carries the value.
    logger.warning(
        "the configured %r URL is not navigable: %s; the value is withheld "
        "because configured values are credential-bearing",
        key,
        rejection,
    )
    raise ValueError(
        f"the configured {key!r} URL is not navigable: {rejection}; the value "
        f"is withheld because configured values are credential-bearing"
    )



# ---------------------------------------------------------------------------
# The read surface
# ---------------------------------------------------------------------------


def get_property(key: str) -> str | None:
    r"""Return the configured value of ``key``, or ``None`` if it is not set.

    The port of ``ConfigurationReader.getProperty``
    (``ConfigurationReader.java:27-29``) with the userdata layer of AAP 0.4.1
    in front of it: the slot installed by :func:`set_userdata` is consulted
    first and the properties file only when the key is *absent* from it, so a
    key mapped to ``""`` in userdata yields ``""`` rather than falling through.
    The six accessors below are the supported entry points; this one is their
    shared implementation, exposed because it mirrors the Java method.

    The three outcomes differ deliberately.  A key outside the six can never be
    satisfied, so it raises before the reader is consulted rather than becoming
    a back door for a seventh key (AAP 0.6).  A key among the six that nothing
    configures - including every key of a missing file - returns ``None``, so
    the failure surfaces at the point of use.  A malformed file is the one
    fault that reaches a caller, and only on a file-backed read; no
    validation, normalization or coercion is applied to a value.

    :param key: One of :data:`CONFIG_KEYS`, matched case-sensitively.
    :returns: The configured value, or ``None`` when neither the userdata slot
        nor the properties file supplies one.
    :raises ValueError: If ``key`` is not one of :data:`CONFIG_KEYS`, or if the
        properties file holds a malformed ``\uXXXX`` escape - the second case
        propagated from ``app/utils/properties.py`` with its own fixed message.
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

    The returned value has satisfied the navigation policy described in the
    module docstring, so the three steps that hand it to ``driver.get`` can do
    so unguarded, as their Java originals do.  This is the sharper of the two
    URL keys: ``features/steps/session_steps.py`` types the configured user
    name and password into whatever the page that answers presents, so a
    destination outside the policy would receive the credentials for the
    system under test.

    :returns: The configured sign-in URL, or ``None`` if it is not set.
    :raises ValueError: When the key is set to a value the navigation policy
        rejects.  The message names this key and the reason and withholds the
        value.
    """
    return _navigable_url(_KEY_WEB_TABLE_URL, get_property(_KEY_WEB_TABLE_URL))


def get_url() -> str | None:
    r"""Return the ``url`` key: the address of the Employee module.

    Read in the reference implementation at ``EmployeeStage.java:24``, ``:60``
    and ``:93``.  A separate key from ``web.table.url``: the two address
    different pages and are not interchangeable.

    The returned value has satisfied the navigation policy described in the
    module docstring.  The key is read afresh at each of the three Employee
    navigation sites, so it is checked afresh at each of them too - a
    properties file edited mid-run is judged by what it holds at the read, not
    at the first read.

    :returns: The configured Employee module URL, or ``None`` if it is not set.
    :raises ValueError: When the key is set to a value the navigation policy
        rejects.  The message names this key and the reason and withholds the
        value.
    """
    return _navigable_url(_KEY_URL, get_property(_KEY_URL))


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
