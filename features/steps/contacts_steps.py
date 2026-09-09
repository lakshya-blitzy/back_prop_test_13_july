r"""Contacts step module - the port of ``Contacts.java``.

The Java anchor is
``src/main/java/com/testinium/step_definitions/Contacts.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  That class is 105 lines and declares **fourteen**
step definitions - the most of any of the ten step classes - over exactly two
fields: a ``ContactsP`` page object (``Contacts.java:13``) and a
``WebDriverWait`` constructed with a **20-second** timeout
(``Contacts.java:15``).  This module is its whole content and nothing besides:
fourteen ``@step`` definitions, the three waits that timeout serves, the five
fixed delays, and the one assertion.

AAP 0.4.1 pairs this module with ``features/Contact.feature`` and
:mod:`app.pages.contacts_page`.  The Gherkin phrases below are byte-exact
copies of the annotation strings in the Java class, and the Python function
names are the Java method names verbatim - which is also what a step's
``match.location`` reports in ``target/cucumber.json`` as a dotted Python path
(AAP 0.1.3, deviation 8).

The fourteen definitions, in Java declaration order
---------------------------------------------------
Every ``wait.until(ExpectedConditions.visibilityOf(x))`` in the source becomes
one ``wait_visible_element(<element>, 20)`` call:

``:17``  #1 ``User is at Contact dashboard``
    3-second delay, **then** ``contact_module.click()``.
``:23``  #2 ``User clicks the create button``
    3-second delay, **then** ``create_contact.click()``.
``:29``  #3 ``User enters name "<name>"``
    wait on ``name_input``, ``clear()``, ``send_keys(name)``.
``:36``  #4 ``User enters "<street name>"``
    ``street_input.send_keys(street_name)``.
``:41``  #5 ``User enters "<phone number>" and "<email>"``
    ``phone_no_input.send_keys(phone_no)`` then
    ``email_input.send_keys(e_mail)``, in that order.
``:47``  #6 ``User sees the created new contact details at dashboard``
    ``contact_module.click()``, wait on ``contact_module``, ``ok_btn.click()``.
``:54``  #7 ``User clicks list section and choose the profile``
    ``call_list.click()``, 3-second delay, ``new_contact.click()``.
``:61``  #8 ``User clicks Action to choose delete button``
    ``action_input.click()``, 3-second delay, ``delete_input.click()``.
``:67``  #9 ``User clicks for editing button``
    ``edit_btn.click()``.
``:72``  #10 ``User sees deleted profile``
    read ``delete_input.text`` and assert it equals ``"Deleted"`` - the **only**
    assertion in the class.
``:79``  #11 ``User selects the profile``
    ``first_user.click()``, then wait on ``edit_title`` - a *different*
    element from the one clicked.
``:85``  #12 ``User sees the updated contact details at dashboard``
    ``contact_module.click()``, and that is the entire body.
``:95``  #13 ``User clicks the print button and then select due payments``
    ``print_input.click()``, **then** a 3-second delay.
``:101``  #14 ``User can see the downloaded file``
    ``due_payment.click()``.

Two phrases used by ``features/Contact.feature`` are deliberately **not**
declared here, because the source declares them elsewhere and a second
declaration would make them ambiguous:
``User login to test other features`` (``Session.java:12``, so
``session_steps.py``) at ``Contact.feature:5``, and ``User clicks save
button`` (``Notes.java:44``, so ``notes_steps.py``) at ``Contact.feature:13``
and ``:32``.

``Contacts.java:90-93`` holds a **fully commented-out** definition,
``User clicks and goes directly to the profile`` with an empty body, matched by
the two commented-out step lines at ``Contact.feature:22-23``.  It is not one
of the fourteen and is not ported: AAP 0.2.2 preserves the source's
inconsistencies rather than tidying them, so there is no fifteenth definition
here and no fifteenth is to be added.

Why every definition registers with ``@step``
---------------------------------------------
AAP 0.5.2, deviation 7, and behavioural rather than stylistic.  Cucumber-JVM
matches a step by its **text alone** - ``Given``, ``When``, ``Then`` and
``And`` are interchangeable at match time - whereas behave resolves by the
*effective* step type, so a definition registered under one type does not
match a use under another.  ``@step`` matches whatever keyword invoked the
step and therefore reproduces text-only matching exactly.

This module is the second of the suite's two cross-type call sites, which is
what makes the rule concrete here rather than theoretical: **definition #1
carries the ``When`` annotation at ``Contacts.java:17`` and is invoked as
``Given`` at ``Contact.feature:6``**, inside the feature's own ``Background``.
Bound to its declaring keyword it would go undefined, and every scenario in
the file would fail before reaching its first step.  Nothing in the source
relies on keyword disambiguation, because under Cucumber-JVM it cannot.

The three ``User enters ...`` phrases, and the field type they need
------------------------------------------------------------------
This is the one place in the port where the naive translation is *measurably*
broken, and it is why the ``CukeStr`` registration below exists.

Cucumber's ``{string}`` compiles to ``"([^"\\]*(\\.[^"\\]*)*)"``, whose body
cannot match a bare double quote.  behave's default ``parse`` matcher renders a
plain ``{field}`` as a non-greedy group that *can* span quotes, and because the
match is anchored to the whole step text it backtracks across them.  So
definition #4's pattern happily consumes a #5 step use, capturing
``+99999999999" and "abcd@info.com`` into one field.

Measured here with ``behave`` 1.3.3 and ``parse`` 1.22.1, against the real
outline data of ``Contact.feature:12`` and ``:31``:

======================================  ==============  ====================
Step text                               plain ``{f}``   ``{f:CukeStr}``
======================================  ==============  ====================
``User enters "+99999999999" and        2 - AMBIGUOUS   1 - #5, correct
"abcd@info.com"``                       (#4 and #5)
``User enters "Haussman"``              1 - #4          1 - #4
``User enters name "&Dustin"``          1 - #3          1 - #3
======================================  ==============  ====================

Left unfixed, that ships as a non-deterministic ``AmbiguousStep`` error, or as
first-registered-wins according to module load order.  With the field type it
is 0 ambiguous and 0 unresolved.

Three properties of the fix, each load-bearing:

* **The registration runs at module scope, above the decorators.**  behave's
  parse matcher compiles each pattern **at decoration time**, so registering
  later - in ``before_all``, say - is too late and silently changes nothing.
* **It stays in this module.**  The AAP 0.3.1 target tree lists exactly ten
  modules in this folder and no helpers, and the other five parameter-bearing
  modules (``login``, ``crm``, ``calendar``, ``employee``, ``sales``) are
  unambiguous with plain fields and are not to be touched.
* **The captured values are the unquoted strings**, exactly as Cucumber passes
  them, because the literal quotes live in the pattern and the converter is
  the identity.

The five fixed delays are preserved at their call sites
------------------------------------------------------
``Contacts.java:19``, ``:25``, ``:57``, ``:64`` and ``:98`` are each
``Thread.sleep(3000)`` - five of the suite's seventeen, the largest count
after ``EmployeeStage.java``.  AAP 0.4.1 requires them reproduced as fixed
delays at the same call sites and **never** converted into explicit waits,
because converting them would change timing behaviour in a suite whose steps
depend on Odoo's client-side rendering and could therefore change outcomes.
Replacing them is a follow-up the user can request; it is not part of this
port.

Their position within each body is part of the contract: the delay comes
**first** in #1 and #2, sits **between the two clicks** in #7 and #8, and
comes **last** in #13.

A 20-second explicit-wait timeout coexisting with five 3-second fixed sleeps
looks redundant.  It is source behaviour, and both stay.

The page object and the timeout bind per scenario
-------------------------------------------------
``Contacts.java`` builds its page object and its wait as **fields, at glue
construction**.  Constructing either at module scope in Python would bind
whichever worker process imported the module first, so both move inside the
step bodies (AAP 0.4.2, which calls this out as the one semantic difference
preserved deliberately):

* The page comes from :func:`_page`, which reads the session
  ``features/environment.py`` publishes as ``context.driver``.  Binding it is
  free - ``BasePage.__init__`` stores the argument and does nothing else, so
  construction locates no element and touches neither DOM nor driver.  That is
  why binding it as the first statement of a body does not displace a delay
  that has to precede a click: no browser operation happens until an accessor
  is read.
* The wait becomes a **timeout argument, not an object**.
  :func:`~app.automation.waits.wait_visible_element` declares ``timeout`` as a
  required parameter with no default, so this class's ``20`` stays visible at
  all three of its call sites, and the literal is written out at each one
  rather than hoisted into a constant.  With the helper's optional ``driver``
  argument left at its default, it resolves this worker's own session - which
  is precisely what ``new WebDriverWait(Driver.getDriver(), 20)`` did.

Import boundary (AAP 0.4.2)
---------------------------
The list below the docstring is complete and closed, and the boundary it keeps
is checked **textually** over every module in this folder by
``tests/test_steps_registration.py``.  So the browser-automation library is
not named here at all - not in an import, and not in prose either, exactly as
:mod:`app.pages.contacts_page` explains of itself.  :mod:`app.automation` is
the only package permitted to reach that library, and of the surface it
re-exports this module takes the one wait helper below and nothing else; the
locator-strategy constant it also re-exports is imported by ``login_steps``
alone, for the port of ``LoginSD.java:56``.

Nothing comes from :mod:`app.automation.interactions` either.
``Contacts.java`` is the one step class among the ten that imports **neither**
the keyboard-key class **nor** the action-builder class, and AAP 0.4.1 names
it for exactly that.  The five ``sendKeys`` calls here are plain sends on a
located element and need no helper - reaching for a keyboard helper here means
a ``sendKeys`` has been misread.  From the wait family only
``wait_visible_element`` is taken, because every wait in this class applies
``ExpectedConditions.visibilityOf`` to an element rather than to a locator.

Nothing comes from the configuration module, since ``Contacts.java`` reads no
configuration property; and nothing from the session lifecycle, since
AAP 0.3.3 gives that a single owner in ``features/environment.py`` and no step
here ever creates or quits a driver.  The Inventory page object is not
imported despite ``Contact.feature:1`` reading ``Feature: Testinium app
Inventory feature`` - the header is mis-titled in the source, AAP 0.2.2
declines to correct it, and ``Contacts.java`` uses ``ContactsP`` only.

What this module deliberately does not contain
----------------------------------------------
* **No ``print``.**  Only ``Crm.java`` and ``Sales.java`` print.
* **No exception handling.**  A ``TimeoutException`` from a wait, and anything
  else, propagates uncaught and fails the step exactly as it fails in Java.
* **No second assertion, and none added to #12.**  Despite reading "sees the
  updated contact details", #12 asserts nothing at all - its body is one
  click.  #10's is the only assertion in the class, and it is the two-argument
  ``Assert.assertEquals`` with no message, so it becomes a bare ``assert``
  keeping the Java operand order and local names (AAP 0.5.2, deviation 16:
  the assertion subject and message are parity, their formatting is not).
* **No extra wait**, no explicit wait substituted for any of the five delays,
  and no timeout other than ``20``.
* **No module-level page instance, driver reference or wait object.**  The
  ``CukeStr`` registration is the only thing that happens at module scope.
* **No ``__all__``.**  behave consumes this module for the registration side
  effect of its decorators; the function names are not an import surface, and
  declaring one would imply they were.
* **No ``__init__.py`` or ``conftest.py`` alongside**, no shared helper module,
  no configuration key, no logging, no retry and no defensive guard - the
  enterprise-standard bar for this port forbids speculative additions, and
  ``register_type`` is behave's own documented mechanism for the problem
  above, so the fix meets that bar rather than bending it.

The ``context`` parameter is left unannotated throughout.  Annotating it would
need a name from ``typing``, which is outside the closed import list above,
and the alternative - ``object`` - would be actively wrong, since every body
reads ``context.driver`` off it.

This module's verifying test is ``tests/test_steps_contacts.py``, owned by the
``tests/`` package: it enumerates ``Contacts.java``'s methods so that an
omission here fails rather than passes silently, and it asserts each delay at
its call site.  No test file belongs beside this one.
"""

from time import sleep

from behave import register_type, step
from parse import with_pattern

from app.automation import wait_visible_element
from app.pages import ContactsPage


@with_pattern(r'[^"]*')
def parse_cuke_str(text: str) -> str:
    r"""Return a quoted step argument unchanged - the identity converter.

    The stand-in for Cucumber's ``{string}``, and the whole of this module's
    fix for the ambiguity the docstring measures.  ``with_pattern`` attaches
    the regex ``[^"]*`` to this function, which is what
    :func:`behave.register_type` then installs under the name ``CukeStr``; the
    literal quotes that delimit the argument stay in the step pattern itself,
    so what arrives here - and what a step body receives - is already the
    unquoted text.

    ``[^"]*`` differs from Cucumber's ``([^"\\]*(\\.[^"\\]*)*)`` in one
    respect only: it does not admit a backslash-escaped quote *inside* an
    argument.  No step use in any of the ten feature files contains one, so
    across this suite the two patterns accept exactly the same inputs while
    ``[^"]*`` additionally refuses to span the closing quote - which is the
    property that separates definitions #4 and #5.  Both accept the empty
    string, as Cucumber's does.

    :param text: The argument text the matcher captured, quotes excluded.
    :returns: ``text`` itself, unconverted - Cucumber passes ``{string}``
        through as a string and so does this.
    """
    return text


# Module scope, and above every decorator below, because behave's parse
# matcher compiles a step pattern at DECORATION time: a registration that ran
# later - in environment.py's before_all, for instance - would leave the three
# patterns already compiled with a plain field and change nothing at all.
# Moving this statement below the decorators is expected to break the dry run,
# and that failure is the proof the ordering matters.
register_type(CukeStr=parse_cuke_str)


def _page(context) -> ContactsPage:
    """Bind a :class:`~app.pages.contacts_page.ContactsPage` to this scenario.

    The stand-in for ``ContactsP contactP = new ContactsP()``
    (``Contacts.java:13``), moved from module scope into the step bodies so it
    binds the session of the scenario being run rather than of whichever worker
    process imported this module first.

    ``context.driver`` is published by ``features/environment.py``'s
    ``before_scenario`` and is the only session any step here touches.
    Construction is free: the page object stores the driver and locates
    nothing, so calling this costs no browser round trip and can sit at the top
    of a body whose first observable act must be a delay.

    :param context: behave's ``Context`` for the running scenario.
    :returns: A page object bound to that scenario's session.
    """
    return ContactsPage(context.driver)


# ---------------------------------------------------------------------------
# The fourteen definitions, in the declaration order of Contacts.java, every
# one registered with @step so that the keyword a feature invokes it under is
# irrelevant - as it is under Cucumber-JVM. Definition #1 depends on that
# directly: it carries the When annotation in Java and is invoked as Given by
# Contact.feature's own Background.
# ---------------------------------------------------------------------------


@step("User is at Contact dashboard")
def user_is_at_contact_dashboard(context) -> None:
    """Open the Contacts module - ``Contacts.java:17-21``.

    The delay comes **first**, before the click (``Contacts.java:19-20``).

    Invoked as ``Given`` at ``Contact.feature:6`` though it carries the
    ``When`` annotation at ``Contacts.java:17``; ``@step`` is what makes that
    resolve.
    """
    page = _page(context)
    sleep(3)
    page.contact_module.click()


@step("User clicks the create button")
def user_clicks_the_create_button(context) -> None:
    """Press Odoo's Create access key - ``Contacts.java:23-27``.

    The delay comes **first**, before the click (``Contacts.java:25-26``).
    """
    page = _page(context)
    sleep(3)
    page.create_contact.click()


@step('User enters name "{name:CukeStr}"')
def user_enters_name(context, name: str) -> None:
    """Fill the contact's name - ``Contacts.java:29-34``.

    Waits for the field, clears it, then types.  The Java parameter is
    literally named ``string`` (``Contacts.java:30``); the phrase is the
    contract, not the parameter name, so this one is readable and matches its
    pattern field.
    """
    page = _page(context)
    wait_visible_element(page.name_input, 20)
    page.name_input.clear()
    page.name_input.send_keys(name)


@step('User enters "{street_name:CukeStr}"')
def user_enters(context, street_name: str) -> None:
    """Fill the contact's street - ``Contacts.java:36-39``.

    One send, with no wait and no clear.  This is the pattern that would
    swallow definition #5's step text without the ``CukeStr`` field type.
    """
    _page(context).street_input.send_keys(street_name)


@step('User enters "{phone_no:CukeStr}" and "{e_mail:CukeStr}"')
def user_enters_and(context, phone_no: str, e_mail: str) -> None:
    """Fill phone then e-mail - ``Contacts.java:41-45``.

    The order is the source's: ``phoneNo`` into ``phoneNoInput`` first
    (``:43``), then ``eMail`` into ``emailInput`` (``:44``).  The two must not
    be transposed - both fields accept any text, so a swap fails silently at
    the browser and only surfaces as wrong data on the saved contact.
    """
    page = _page(context)
    page.phone_no_input.send_keys(phone_no)
    page.email_input.send_keys(e_mail)


@step("User sees the created new contact details at dashboard")
def user_sees_the_created_new_contact_details_at_dashboard(context) -> None:
    """Return to the list and confirm - ``Contacts.java:47-52``.

    Clicks ``contact_module``, **then** waits on that same element, then
    clicks ``ok_btn`` (``Contacts.java:49-51``).  Clicking before waiting is
    source behaviour and the order is not to be swapped.  Despite the phrase,
    the source asserts nothing here.
    """
    page = _page(context)
    page.contact_module.click()
    wait_visible_element(page.contact_module, 20)
    page.ok_btn.click()


@step("User clicks list section and choose the profile")
def user_clicks_list_section_and_choose_the_profile(context) -> None:
    """Switch to the list view and select a row - ``Contacts.java:54-59``.

    The delay sits **between** the two clicks (``Contacts.java:56-58``).
    ``new_contact`` is the positional ``[12]`` checkbox of
    ``ContactsP.java:38``, preserved by AAP 0.8.
    """
    page = _page(context)
    page.call_list.click()
    sleep(3)
    page.new_contact.click()


@step("User clicks Action to choose delete button")
def user_clicks_action_to_choose_delete_button(context) -> None:
    """Open the Action dropdown and delete - ``Contacts.java:61-66``.

    The delay sits **between** the two clicks (``Contacts.java:63-65``).
    ``action_input`` is the positional ``[2]`` sidebar div of
    ``ContactsP.java:41``.
    """
    page = _page(context)
    page.action_input.click()
    sleep(3)
    page.delete_input.click()


@step("User clicks for editing button")
def user_clicks_for_editing_button(context) -> None:
    """Enter edit mode on the open record - ``Contacts.java:67-70``."""
    _page(context).edit_btn.click()


@step("User sees deleted profile")
def user_sees_deleted_profile(context) -> None:
    """Assert the delete entry now reads ``"Deleted"`` - ``Contacts.java:72-77``.

    The **only** assertion in this class.  ``Assert.assertEquals(actualMsg,
    expectedMsg)`` at ``:76`` is the two-argument form with no message, so this
    is a bare ``assert`` keeping the Java operand order and both local names
    (AAP 0.5.2, deviation 16).  Java's ``getText()`` is a method; the Python
    binding exposes it as the ``text`` property.

    ``delete_input`` is read here after being clicked by definition #8, so the
    one locator of ``ContactsP.java:44`` serves both an action and this check.
    """
    page = _page(context)
    actual_msg = page.delete_input.text
    expected_msg = "Deleted"
    assert actual_msg == expected_msg


@step("User selects the profile")
def user_selects_the_profile(context) -> None:
    """Open the first kanban record - ``Contacts.java:79-83``.

    Clicks ``first_user`` (``:81``) and then waits on **``edit_title``**
    (``:82``) - a *different* element from the one clicked, which is how the
    source establishes that the record's form has actually opened.  Preserved
    as written: the wait target is not ``first_user``.

    ``first_user`` is the positional ``[1]`` kanban record of
    ``ContactsP.java:47``.
    """
    page = _page(context)
    page.first_user.click()
    wait_visible_element(page.edit_title, 20)


@step("User sees the updated contact details at dashboard")
def user_sees_the_updated_contact_details_at_dashboard(context) -> None:
    """Return to the Contacts list - ``Contacts.java:85-88``.

    One click, and that is the **entire** body (``Contacts.java:87``).  The
    phrase says "sees the updated contact details" and the source checks
    nothing whatever; no assertion is to be added here.
    """
    _page(context).contact_module.click()


@step("User clicks the print button and then select due payments")
def user_clicks_the_print_button_and_then_select_due_payments(context) -> None:
    """Open the print dropdown - ``Contacts.java:95-99``.

    The delay comes **last**, after the click (``Contacts.java:97-98``).

    This definition follows the commented-out block at ``Contacts.java:90-93``,
    which is not ported.
    """
    page = _page(context)
    page.print_input.click()
    sleep(3)


@step("User can see the downloaded file")
def user_can_see_the_downloaded_file(context) -> None:
    """Choose Due Payments from that dropdown - ``Contacts.java:101-104``.

    ``due_payment`` and ``print_input`` share a base XPath, this one selecting
    the dropdown's ``button`` child (``ContactsP.java:60``).  Despite the
    phrase, the source verifies no download.
    """
    _page(context).due_payment.click()

