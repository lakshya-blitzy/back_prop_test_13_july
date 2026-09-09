r"""Keyboard and action-chain helpers - the port's only home for ``Keys`` and ``Actions``.

The Python port of the ``org.openqa.selenium.Keys`` and
``org.openqa.selenium.interactions.Actions`` usage of exactly three Java step
classes - ``Crm.java``, ``Notes.java`` and ``Sales.java`` - at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds as
REFERENCE and this port never modifies.  ``pom.xml:36-40`` supplied those two
Java types through ``selenium-java`` 3.141.59; ``selenium`` 4.48.0 (AAP 0.5.1,
exact pin) supplies :class:`~selenium.webdriver.common.keys.Keys` and
:class:`~selenium.webdriver.common.action_chains.ActionChains` here.

AAP 0.5.2 makes this module their sole home in the whole port - both are
*"reached only through ``app/automation/interactions.py``"* - and AAP 0.4.2
makes ``app/automation`` the only package that imports ``selenium`` at all.
Two public helpers, and nothing else:

* :func:`press_keys` supplies the key constants a step needs by *name*.
* :func:`action_chain` hands back a fresh builder per use site.

Who imports what, and why this set must not grow
------------------------------------------------
AAP 0.4.2 fixes the import sites precisely - *"``press_keys`` in
``crm_steps``, ``notes_steps`` and ``sales_steps``; ``action_chain`` in
``crm_steps`` and ``notes_steps``"* - and that table is the whole demand on
this module:

============================================  =============================
Consumer                                      Imports from here
============================================  =============================
``features/steps/crm_steps.py``               ``press_keys``,
                                              ``action_chain``
``features/steps/notes_steps.py``             ``press_keys``,
                                              ``action_chain``
``features/steps/sales_steps.py``             ``press_keys`` only
``features/steps/contacts_steps.py``          **nothing**
the other six step modules                    **nothing**
============================================  =============================

That asymmetry is measured behaviour, not a convention: ``Crm.java:9-10`` and
``Notes.java:9-10`` import both Java types, ``Sales.java:9`` imports ``Keys``
alone, and ``Contacts.java`` imports neither - so ``contacts_steps.py``
importing anything from here would be a divergence.

**Do not generalize.**  There is no ``scroll_to``, ``drag_and_drop``,
``hover``, ``double_click``, ``right_click``, ``select_all`` or
``clear_and_type``, and no convenience wrapper that no call site invokes.
Every unused helper would be dead code that also drags down the 80 % coverage
gate AAP 0.5.1 sets for this package.  Should a Java call site ever turn out
to need an operation these two cannot express, the instruction is to add
*one* helper for exactly that operation, name it ``<verb>_<object>``, export
it from the barrel and record why - nothing speculative.

Every ``Keys`` and ``Actions`` site in the source, and what covers it
--------------------------------------------------------------------
``Keys.ENTER`` is the only member the suite ever names, at nine call sites in
two shapes, and both shapes are one :func:`press_keys` call:

``Notes.java:35`` - ``notesP.tagsN.sendKeys("New Tag", Keys.ENTER)``
    ports to ``press_keys(page.tags_n, "ENTER", text="New Tag")``.
``Sales.java:68`` - ``salesp.searchBar.sendKeys(name + Keys.ENTER)``
    ports to ``press_keys(page.search_bar, "ENTER", text=name)``.
``Crm.java:36``, ``:40``, ``:76``, ``:78``, ``:80``, ``:138``, ``:141``
    the same call, each carrying that site's own literal - ``"test"``,
    ``"8"``, the outline's opportunity, revenue and probability values,
    ``"Test"`` and ``"aa"``.

The two Java shapes differ only on the page, not on the wire: selenium's
``keys_to_typing`` flattens every argument into one character list, so the
argument pair ``(literal, Keys.ENTER)`` and the concatenated string
``literal + Keys.ENTER`` produce an identical character stream.  That
measurement is why one call carrying the whole sequence is the faithful port
of both, and why :func:`press_keys` never splits them across two calls.

``Actions`` appears twice - ``Crm.java:108-115`` and ``Notes.java:78-79`` -
each building ``clickAndHold(...).pause(2000).moveToElement(...)
.pause(2000).release().perform()`` over ``new Actions(Driver.getDriver())``.
Those five operations are ``ActionChains``' own methods, so :func:`action_chain`
supplies the builder and the step body reproduces the chain verbatim.

Two notes for the step modules, because both are parity traps
-------------------------------------------------------------
* **There is no ``build()``.**  Java 8 ``Actions`` code often ends
  ``.build().perform()``; the Python ``ActionChains`` has no separate build
  step - measured: ``hasattr(ActionChains, "build")`` is ``False`` - and
  ``.perform()`` alone is the equivalent.  Do not go looking for it.
* **``pause`` changed units.**  Java's ``Actions.pause(2000)`` is
  *milliseconds*; ``ActionChains.pause(seconds)`` is *seconds*.  The two
  ``pause(2000)`` calls in each Java chain port to ``pause(2)``, and a literal
  transcription would stall a scenario for over half an hour.

Why ``Keys`` is deliberately not exported, which is the reason this module exists
--------------------------------------------------------------------------------
AAP 0.4.1 lists the barrel's export surface as ``get_driver``,
``quit_driver``, the wait helpers, ``press_keys``, ``action_chain`` and
``By`` - no ``Keys``.  What makes that workable: typing literal text never
needed a Selenium import, since a literal string handed to an element's own
``send_keys`` method names no Selenium symbol at the call site.  Only
*naming* a key constant did.  So this module keeps the
import and hands out the constants by name, and :func:`press_keys` exists for
precisely that.  If a later change tempts anyone to re-export ``Keys``, this
paragraph is the argument against it.

Why returning an ``ActionChains`` is not a leak, since it looks like one
-----------------------------------------------------------------------
AAP 0.4.2's invariant is textual and import-level: *"``selenium`` is imported
only inside ``app/automation``; ``tests/test_steps_registration.py`` asserts
no module under ``features/steps/`` imports it."*  A step module that receives
an ``ActionChains`` instance from :func:`action_chain` holds a Selenium object
without importing Selenium, and the AAP names ``action_chain`` in the export
list precisely so that works.  Wrapping the chain in a facade would break the
one thing the per-module parity tests check - that a ported step body
reproduces its Java chain verbatim, in the same order - so it is not wrapped.

What this module deliberately does not do
-----------------------------------------
* **It adds no interaction of its own.**  No ``clear()``, no click before
  typing, no wait for visibility and no retry.  Those are call-site decisions
  the Java step bodies make explicitly, and the per-module parity tests assert
  *"the same keyboard keys or action-chain sequence"*; a helper that inserted
  a step of its own would break that by construction.
* **It never validates a driver.**  ``app/automation/driver.py``'s
  :func:`~app.automation.driver.get_driver` returns ``None`` when the
  configured browser name matches neither branch it constructs, and AAP 0.4.1
  rules that such a run *"fails at first driver use, as today"*.  So a
  ``None`` session is passed straight through to the interaction that needs
  it, and the failure surfaces there rather than here.
* **It owns no state.**  No cached element, no cached chain and no module
  global beyond the key-name lookup table below, which is derived from
  ``Keys`` itself and never mutated.  The one ``ActionChains`` rule that
  matters is stated at :func:`action_chain`: a fresh builder every call.
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

#: The valid names, sorted, ready to be quoted into the error message a
#: misuse raises.  Built once for the same reason as :data:`_KEY_NAMES`: the
#: message stays correct if the pinned Selenium ever changes the member set.
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

    The port of the suite's nine ``Keys`` call sites, which occur in two Java
    shapes that this one signature covers:

    .. code-block:: java

        // Notes.java:35 - literal and key as two arguments
        notesP.tagsN.sendKeys("New Tag", Keys.ENTER);
        // Sales.java:68 - literal and key concatenated into one argument
        salesp.searchBar.sendKeys(name + Keys.ENTER);

    .. code-block:: python

        press_keys(page.tags_n, "ENTER", text="New Tag")
        press_keys(page.search_bar, "ENTER", text=name)

    Both Java shapes reach the browser as one character stream, because
    selenium's ``keys_to_typing`` flattens every argument into a single list -
    so the port makes exactly **one** keyboard call carrying
    ``(text, *keys)``.  Splitting it in two would change the call granularity
    the browser sees for no gain.

    :param target: The element to type into - an already-resolved
        ``WebElement``, or a ``(By.X, "value")`` locator pair this helper
        resolves with ``find_element``.
    :param key_names: ``Keys`` member names as strings, in the order they are
        to be typed.  Resolved case-insensitively; see :func:`_resolve_key`.
        Literal characters do **not** belong here - ``"a"`` names no member
        and is rejected - they belong in *text*.
    :param text: A literal string typed *before* the resolved keys, inside the
        same call.  Keyword-only, and optional: omit it to send keys alone.
    :param driver: Keyword-only test seam.  ``None`` - the value every step
        module uses, none of them passing this - takes the session from
        :func:`~app.automation.driver.get_driver`, and then only when *target*
        is a locator pair.
    :returns: ``None``, because Java's ``sendKeys`` returns void.
    :raises ValueError: When no *text* and no *key_names* are supplied, which
        is a misuse rather than a request to type nothing, or when a name in
        *key_names* is not a ``Keys`` member.  Neither case touches the
        browser: both are raised before *target* is resolved.

    Order is preserved exactly as passed - never sorted, deduplicated or
    reordered - since ``Keys.CONTROL`` before a character is not the same
    input as the reverse.  And this helper adds nothing of its own: no
    ``clear()``, no click first, no wait for visibility and no retry, because
    the Java step bodies make each of those decisions explicitly at their own
    call sites and the parity tests compare the resulting sequence.
    """
    # Both misuse checks run first, so a malformed call costs no element
    # lookup, starts no browser interaction and leaves no partial input in the
    # page.  An empty call is rejected rather than turned into a no-op
    # keyboard call, which would silently pass a broken step.
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

    # The single keyboard call of this module, and the whole of its effect.
    element.send_keys(*sequence)


def action_chain(driver: object | None = None) -> ActionChains:
    """Return a fresh action builder bound to this worker's session.

    The port of ``new Actions(Driver.getDriver())`` - ``Crm.java:108`` and
    ``Notes.java:78`` - which both classes evaluate anew inside the step
    method that uses it.  The caller chains and performs:

    .. code-block:: python

        (
            action_chain()
            .click_and_hold(page.progress_pipeline)
            .pause(2)
            .move_to_element(page.progress_pipeline_2)
            .pause(2)
            .release()
            .perform()
        )

    :param driver: Keyword-friendly test seam. ``None`` - the value every step
        module uses - takes the session from
        :func:`~app.automation.driver.get_driver`.
    :returns: A new :class:`~selenium.webdriver.common.action_chains.ActionChains`
        over the resolved session, with nothing queued on it.

    **A new builder every call, never a cached one.**  An ``ActionChains``
    accumulates queued actions until ``perform()`` flushes them, so a shared
    instance would replay an earlier step's actions inside a later step - a
    failure that would look like a flaky browser rather than a bug here.
    Returning a fresh object per call is also exactly what the Java code does,
    a builder per use site.

    ``perform()`` is deliberately **not** called here: the ported step bodies
    must be free to reproduce their Java chain verbatim, in the same order,
    and that chain ends in the caller.  The Python builder has no ``build()``
    step either, so ``.perform()`` alone stands in for Java's
    ``.build().perform()``.  Mind the units while transcribing - Java's
    ``pause`` takes milliseconds, this one takes seconds.
    """
    # May be None when the configured browser name matches neither branch
    # driver.py constructs; it is passed through untouched so the failure
    # surfaces at the caller's first interaction, as it does in the source.
    active_driver = get_driver() if driver is None else driver

    return ActionChains(active_driver)
