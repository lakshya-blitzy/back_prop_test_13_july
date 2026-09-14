r"""Keyboard and action-chain helpers - the port's only home for ``Keys`` and ``Actions``.

``Keys`` and ``ActionChains`` are *"reached only through
``app/automation/interactions.py``"* (AAP 0.5.2), and AAP 0.4.2 makes
``app/automation`` the only package importing ``selenium``.  :func:`press_keys`
supplies key constants by *name*; :func:`action_chain` hands back a builder.

AAP 0.4.2 fixes the consumers exactly: ``press_keys`` in ``crm_steps``,
``notes_steps`` and ``sales_steps``; ``action_chain`` in ``crm_steps`` and
``notes_steps``.  The asymmetry is measured - ``Crm.java:9-10`` and
``Notes.java:9-10`` import both Java types, ``Sales.java:9`` imports ``Keys``
alone, ``Contacts.java`` neither - so ``contacts_steps`` importing from here
would be a divergence, and no helper no call site invokes is added.

``Keys.ENTER`` is the only member the suite names, at nine call sites in two
shapes: ``sendKeys("New Tag", Keys.ENTER)`` (``Notes.java:35``),
``sendKeys(name + Keys.ENTER)`` (``Sales.java:68``) and the same call carrying
each site's own literal at ``Crm.java:36``, ``:40``, ``:76``, ``:78``, ``:80``,
``:138`` and ``:141``.  Selenium's ``keys_to_typing`` flattens both shapes into
one character stream, so :func:`press_keys` makes a single keyboard call.

``Actions`` appears twice, ``Crm.java:108-115`` and ``Notes.java:78-79``, each
building ``clickAndHold(...).pause(2000).moveToElement(...).pause(2000)
.release().perform()``.  Those five operations are ``ActionChains``' own
methods, so :func:`action_chain` supplies the builder and the step body
reproduces the chain - minding that Python has no ``build()`` and that its
``pause`` counts seconds where Java's counts milliseconds.

``Keys`` itself is deliberately not re-exported: AAP 0.4.1's barrel surface
names ``press_keys`` and ``action_chain`` instead, and typing a literal never
needed a Selenium import - only *naming* a key constant did.  Handing out an
``ActionChains`` *instance* breaks nothing, the AAP 0.4.2 invariant being
import-level and textual as ``tests/test_steps_registration.py`` checks it; the
*class* is what must not leave, and a facade would break step-body parity.

This module adds no interaction of its own - no ``clear()``, click, wait or
retry, each being a call-site decision the Java bodies make - never validates a
driver, ``None`` passing straight through per AAP 0.4.1, and owns no state
beyond the key-name table below, derived from ``Keys`` and never mutated.
"""

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from .driver import get_driver

__all__ = ["press_keys", "action_chain"]


#: Every public member name of :class:`~selenium.webdriver.common.keys.Keys`,
#: derived from the class itself exactly once at import time rather than
#: hard-coded, so this module cannot drift from the pinned ``selenium``
#: 4.48.0 it is written against (73 names there, every value a ``str``).
#: :func:`_resolve_key` checks membership here before calling ``getattr``, so
#: no attribute that merely happens to exist on the class - and nothing
#: inherited from ``object`` - can be reached through the helper's key
#: argument.
_KEY_NAMES: frozenset[str] = frozenset(
    name for name in vars(Keys) if not name.startswith("_")
)

_KEY_NAMES_LISTED: str = ", ".join(sorted(_KEY_NAMES))


def _resolve_key(key_name: object) -> str:
    """Turn one ``Keys`` member *name* into the character it stands for.

    :param key_name: A member name of
        :class:`~selenium.webdriver.common.keys.Keys` - ``"ENTER"``,
        ``"TAB"``, ``"ARROW_DOWN"``, ``"ESCAPE"``, ``"CONTROL"``,
        ``"BACK_SPACE"`` and so on.  Matching is case-insensitive: the name is
        upper-cased before lookup, so ``"enter"`` and ``"ENTER"`` both
        resolve.
    :returns: The single-character string that member holds.
    :raises ValueError: When *key_name* is not a string, or names no member of
        that class.  The message quotes the offending value and lists the
        valid names, because the only way to reach it is a mistake in a step
        module and the fix is a spelling correction.

    The case-insensitive lookup here is a *helper-API* convenience and is not
    the same thing as the browser-name matching in
    ``app/automation/driver.py``, which compares exactly on purpose because a
    Java ``switch`` over a ``String`` does.  Do not let the one migrate into
    the other.  Raising is likewise safe: this is internal-API misuse, not the
    browser-value validation that AAP 0.4.1 forbids the driver from
    performing.
    """
    if not isinstance(key_name, str):
        raise ValueError(
            f"press_keys() expects Keys member names as strings, got "
            f"{key_name!r} of type {type(key_name).__name__}. "
            f"Literal characters go through the keyword-only text= parameter. "
            f"Valid key names: {_KEY_NAMES_LISTED}."
        )

    resolved_name = key_name.upper()
    if resolved_name not in _KEY_NAMES:
        raise ValueError(
            f"Unknown Keys member name {key_name!r}. "
            f"Literal characters go through the keyword-only text= parameter. "
            f"Valid key names: {_KEY_NAMES_LISTED}."
        )

    return getattr(Keys, resolved_name)


def _resolve_target(target: object, driver: object | None) -> object:
    """Return the element to type into, looking it up only if it has to.

    :param target: Either an already-resolved Selenium ``WebElement``, which
        is returned untouched, or a ``(By.X, "value")`` locator pair.
    :param driver: The session a locator pair is resolved against, or ``None``
        to take this worker's session from
        :func:`~app.automation.driver.get_driver`.
    :returns: The element the caller's single keyboard call is made on.

    The locator case is detected *structurally* - a ``tuple`` or ``list`` of
    length two - deliberately, rather than by importing ``WebElement`` for an
    ``isinstance`` check: a ``WebElement`` is neither, the check needs no
    import, and it keeps this module's import list at the three entries AAP
    0.4.2 allows.  Nothing else is inspected, so a caller that passes an
    element gets it back without this module ever touching a session, which is
    what keeps :func:`get_driver` out of the common path.
    """
    if isinstance(target, (tuple, list)) and len(target) == 2:
        # get_driver() is called here and nowhere earlier: a locator is the
        # only reason this module needs a session at all.  It is read through
        # the module global so a test can substitute it, and it may hand back
        # None - which is passed straight on, per the module docstring.
        active_driver = get_driver() if driver is None else driver
        return active_driver.find_element(*target)

    return target


def press_keys(
    target: object,
    *key_names: str,
    text: str | None = None,
    driver: object | None = None,
) -> None:
    """Type an optional literal followed by named keys, in one keyboard call.

    The port of the suite's nine ``Keys`` call sites, whose two Java shapes this
    signature covers: ``sendKeys("New Tag", Keys.ENTER)`` (``Notes.java:35``)
    and ``sendKeys(name + Keys.ENTER)`` (``Sales.java:68``) both become
    ``press_keys(page.tags_n, "ENTER", text="New Tag")``.  Both reach the
    browser as one character stream - selenium's ``keys_to_typing`` flattens
    every argument into one list - so this makes exactly **one** keyboard call
    carrying ``(text, *keys)``.

    :param target: The element to type into - an already-resolved
        ``WebElement``, or a ``(By.X, "value")`` locator pair this helper
        resolves with ``find_element``.
    :param key_names: ``Keys`` member names as strings, in the order they are
        typed and never reordered, resolved case-insensitively.  Literal
        characters name no member and are rejected; they belong in *text*.
    :param text: A literal typed *before* the keys, inside the same call.
        Keyword-only; an empty string is a supplied literal, not a missing one.
    :param driver: Keyword-only test seam, consulted only when *target* is a
        locator pair; ``None`` takes this worker's session.
    :returns: ``None``, because Java's ``sendKeys`` returns void.
    :raises ValueError: When neither *text* nor *key_names* is supplied, which
        is a misuse rather than a request to type nothing, or when a name is no
        ``Keys`` member.  Neither case touches the browser.
    """
    if text is None and not key_names:
        raise ValueError(
            "press_keys() needs something to type: pass at least one Keys "
            "member name, the keyword-only text=, or both."
        )

    resolved_keys = tuple(_resolve_key(key_name) for key_name in key_names)

    # (text, *keys) when a literal was supplied, the keys alone otherwise.
    # An empty string is a supplied literal, not a missing one, so the check
    # above is against None rather than falsiness.
    sequence = (text, *resolved_keys) if text is not None else resolved_keys

    element = _resolve_target(target, driver)

    element.send_keys(*sequence)


def action_chain(driver: object | None = None) -> ActionChains:
    """Return a fresh action builder bound to this worker's session.

    The port of ``new Actions(Driver.getDriver())`` (``Crm.java:108``,
    ``Notes.java:78``), which both classes evaluate anew inside the step method
    that uses it.  The caller chains and performs; two traps while transcribing
    a Java chain: the Python builder has no ``build()`` step, so ``.perform()``
    alone stands in for ``.build().perform()``, and its ``pause`` counts
    *seconds* where Java's counts milliseconds, so ``pause(2000)`` becomes
    ``pause(2)``.

    **A new builder every call, never a cached one.**  An ``ActionChains``
    accumulates queued actions until ``perform()`` flushes them, so a shared
    instance would replay an earlier step's actions inside a later one - a
    failure that would look like a flaky browser rather than a bug here.  A
    builder per use site is also what the Java code does, and ``perform()`` is
    deliberately not called here: the ported step bodies reproduce their Java
    chain verbatim, and that chain ends in the caller.

    :param driver: Keyword-friendly test seam.  ``None`` - what every step
        module passes - takes the session from
        :func:`~app.automation.driver.get_driver`.
    :returns: A new ``ActionChains`` over the resolved session, with nothing
        queued on it.
    """
    # May be None when the configured browser name matches neither branch
    # driver.py constructs; it is passed through untouched so the failure
    # surfaces at the caller's first interaction, as it does in the source.
    active_driver = get_driver() if driver is None else driver

    return ActionChains(active_driver)
