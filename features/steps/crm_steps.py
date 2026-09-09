"""CRM step definitions - the behave port of the Java ``Crm`` glue class.

A one-to-one translation of
``src/main/java/com/testinium/step_definitions/Crm.java`` at reference commit
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which is the specification of
every step body below.  Twelve definitions, in the Java declaration order,
each carrying the line range it ports.

**This module is what a default run executes.**  ``@Smoke`` is declared exactly
once in the whole suite, at ``Crm.feature:1``, and it is the default tag filter
(``CukesRunner.java:18``, carried into ``behave.ini`` as ``default_tags``).  So
a bare ``run-tests`` selects the CRM feature alone, these twelve steps are the
ones CI runs on every build, and the committed reference artifacts
(``target/cucumber.json``, ``target/rerun.txt``) were produced from this very
feature.  A mistake here is a mistake in the default path.

The twelve definitions
----------------------
======  =============  =======================================================
Java    Declared as    Phrase
======  =============  =======================================================
``:21``   ``@When``    User click on the crm dashboard
``:27``   ``@And``     User click on the pipeline button
``:34``   ``@And``     User can create the new pipeline
``:46``   ``@And``     User can see the total price
``:58``   ``@Then``    User can see new pipeline
``:70``   ``@And``     User can change any user's information like ...
``:83``   ``@And``     User can save information
``:89``   ``@Then``    User can verify the information
``:106``  ``@And``     User can drag and drop the pipeline
``:121``  ``@Then``    User can see the new changes in progress
``:132``  ``@And``     User can register new customer
``:145``  ``@Then``    User can print the profile
======  =============  =======================================================

The feature's Background step (``Crm.feature:7``, *Given User login to test
other features*) is **not** declared here: it belongs to ``session_steps.py``,
the port of ``Session.java``, and every feature that needs a logged-in session
shares that one definition.

Registration: ``@step`` only, and that is behavioural
-----------------------------------------------------
Cucumber-JVM matches a step by its **text alone** - ``Given``, ``When``,
``Then`` and ``And`` are interchangeable at match time - whereas behave
resolves by the step's *effective* type, so a definition registered under one
type does not match a use under another.  Registering every definition with
``@step``, which matches whatever keyword invokes it, is what reproduces the
JVM's text-only matching (AAP 0.5.2, deviation 7).  Two measured facts make it
unavoidable in this module specifically:

* **Seven of these twelve are declared** ``@And`` - ``:27``, ``:34``, ``:46``,
  ``:70``, ``:83``, ``:106`` and ``:132``, seven of the suite's nine - and
  behave has no ``@and`` decorator at all.  There is nothing else to map them
  onto.
* The Background step this feature opens with is declared ``@When`` in
  ``Session.java:12`` yet invoked as ``Given`` here, and the baseline
  ``target/cucumber.json`` records it as ``"keyword": "Given "`` against
  ``match.location``
  ``com.testinium.step_definitions.Session.user_login_to_test_other_features()``
  - the JVM matching across step types, in this feature's own report.

``given``, ``when`` and ``then`` are therefore never imported or used here, and
``tests/test_steps_registration.py`` asserts exactly that of every module in
this directory.  The twelve phrases are safe under text-only matching: the
suite declares 91 step phrases with no duplicates among them, all twelve are
declared once, and each is used only in ``Crm.feature``.

Import boundary (AAP 0.4.2)
---------------------------
Four imports, and the list is closed: the ``@step`` decorator, the three
``app.automation`` helpers this class's body needs, ``CrmPage``, and the
standard-library sleep.  In particular this module imports **no**
``selenium`` - not ``Keys``, not ``ActionChains``, not ``WebDriverWait``, not
``expected_conditions`` and not ``By`` (only ``login_steps.py`` may import
``By``, for ``LoginSD.java:56``); the keyboard and action-chain helpers exist
precisely so it does not have to.  It imports nothing from ``app.config``,
because ``Crm.java`` reads no configuration key, and it never calls
``get_driver`` or ``quit_driver``: ``features/environment.py`` owns the
scenario lifecycle exclusively (AAP 0.3.3), so no step here creates or quits a
session.  No ``typing`` import either, which is why parameter types are
documented rather than annotated.

Nothing lives at module scope
-----------------------------
``Crm.java:16`` and ``:18`` build the page object and the ``WebDriverWait`` as
**fields**, at glue construction.  The Python equivalent of that - a
module-level ``CrmPage()`` - would bind whichever worker process imported the
module first, so both are built per call instead:

* the page comes from :func:`_page`, over the session
  ``features/environment.py`` publishes as ``context.driver``;
* the wait is not an object at all but a **timeout argument**, and
  ``Crm.java:18``'s 2 seconds is passed literally at each of the eleven wait
  call sites (``:24``, ``:30``, ``:43``, ``:73``, ``:85``, ``:92``, ``:135``,
  ``:137``, ``:140``, ``:148``, ``:150``).  AAP 0.4.1's "nine wait sites"
  counts the nine ``WebDriverWait`` *field* constructions across the nine step
  classes that declare one, not the call sites inside any one class; this class
  makes eleven calls on its single 2-second wait, and every one is reproduced;
* the action chain likewise comes from a fresh :func:`~app.automation.action_chain`
  call inside the one step that drags, mirroring ``new
  Actions(Driver.getDriver())`` at ``Crm.java:108``.

Elements are re-resolved on every access, exactly as the Java ``PageFactory``
proxies were: where a Java line touches ``crm.crmLink`` twice, this module
writes ``page.crm_link`` twice rather than caching it in a local, so the number
of lookups the browser sees is unchanged.

Two unit conversions, and only these two
----------------------------------------
Both are measured properties of the two APIs rather than choices:

* ``Actions.pause(long)`` takes **milliseconds**; ``ActionChains.pause(float)``
  takes **seconds**.  ``Crm.java:111`` and ``:113`` are ``.pause(2000)``, so
  the port writes ``.pause(2)`` - transcribed verbatim it would be 2000
  seconds, over half an hour per pause, and the scenario would hang.
* ``Thread.sleep(long)`` likewise takes milliseconds, so ``Crm.java:117``'s
  ``Thread.sleep(2000)`` becomes ``sleep(2)``.  It is one of the suite's
  seventeen fixed delays and the only one in this class, and it stays a fixed
  delay at its own call site: converting it to an explicit wait would change
  timing behaviour in a suite whose steps depend on Odoo's client-side
  rendering, and could change outcomes (AAP 0.4.1).

Preserved exactly as the source has it
--------------------------------------
Every item below looks like something to tidy and is deliberately kept, since
parity is the requirement:

* **Click, then wait on the element just clicked** - ``:23-24``, ``:29-30``,
  ``:134-135``, ``:136-137``, ``:139-140``, ``:147-148``.  Not reordered into
  wait-then-click.  ``:85-86`` is the one genuine wait-then-click, and it too
  is kept as written.
* **Two waits target a different element from the one just clicked** -
  ``:91-92`` clicks ``pipelineSideButton`` and waits on ``buttonPipeline``, and
  ``:149-150`` clicks ``printButton`` and waits on ``duePaymentButton``.
* **Four selectors are declared twice under different names** in ``CrmP.java``.
  Each call site uses the name its own Java line uses, even where a sibling
  name resolves to the identical selector: ``create_customer`` in ``:136``
  though it equals ``CREATE_BUTTON``, ``create_customer_button`` in ``:139``
  though it equals ``CREATE_PIPELINE``, ``progress_pipeline`` in ``:110``
  though it equals ``BUTTON_PIPELINE``, and ``input_name`` in ``:138`` though
  it equals ``OPPORTUNITY_TITLE_EDIT``.
* **Three different expected literals** - ``"test"`` at ``:61``, ``"Test2"`` at
  ``:95``, ``"test"`` again at ``:124``.  Not unified.
* **The arithmetic of** ``:48-49`` - the parsed total plus ``8``, compared
  against the literal ``89``.  Both numbers are load-bearing.
* **The eight prints** (``:51``, ``:52``, ``:63``, ``:64``, ``:97``, ``:98``,
  ``:126``, ``:127``) are observable behaviour, not debug noise, and their
  camelCase labels and the spaces around ``=`` are reproduced byte-for-byte.
  Only ``Crm.java`` and ``Sales.java`` print.  They are not routed through a
  logger, which would change both the destination and the format.  Each value
  is interpolated rather than concatenated, which is what Java's ``+`` on a
  non-string operand does anyway - it calls ``String.valueOf`` - so the line
  is identical and no operand type can turn a print into a ``TypeError``.
* **No exception handling anywhere.**  ``Integer.parseInt`` throws on
  non-numeric text and Python's ``int()`` raises ``ValueError``; a wait that
  expires raises ``TimeoutException``; a missing element raises
  ``NoSuchElementException``.  All of them propagate untouched, so the scenario
  fails where the Java scenario failed, and the engine records the failure.

Assertions carry the Java operand order and, like the Java calls, no message:
all four are the two-argument ``Assert.assertEquals`` form (AAP 0.5.2,
deviation 16 - Python cannot reproduce JUnit's ``expected:<...> but
was:<...>`` framing, so the assertion *subject* is the parity, not its
formatting).  The baseline artifact's ``expected:<8> but was:<89>`` for
``:54`` confirms which operand the source put first.

The Testinium / Upgenix / Odoo vocabulary of the phrases is left exactly as
written; AAP Conflict 8 resolves that disagreement as "no renaming".
"""

from time import sleep

from behave import step

from app.automation import action_chain, press_keys, wait_visible_element
from app.pages import CrmPage

#: The twelve step functions, in ``Crm.java`` declaration order. behave
#: discovers them through the decorator registry rather than through this
#: list, which is here so the module's surface is greppable and so a parity
#: test can enumerate it against the Java class's twelve methods.
__all__ = [
    "user_click_on_the_crm_dashboard",
    "user_click_on_the_pipeline_button",
    "user_can_create_the_new_pipeline",
    "user_can_see_the_total_price",
    "user_can_see_new_pipeline",
    "user_can_change_any_user_s_information_like_and",
    "user_can_save_information",
    "user_can_verify_the_information",
    "user_can_drag_and_drop_the_pipeline",
    "user_can_see_the_new_changes_in_progress",
    "user_can_register_new_customer",
    "user_can_print_the_profile",
]

# Every wait below is the one WebDriverWait built with a 2-second timeout at
# Crm.java:18, so that literal appears at each of the eleven call sites rather
# than being hidden behind a default or a module constant. wait_visible_element
# takes its timeout as a required parameter precisely so the per-class
# differences across the suite - 2s here, 3s, 4s and 20s elsewhere - stay
# visible where they apply. A named constant here would read as this module's
# own choice rather than as the field it ports, and would let a later edit
# change eleven call sites at once.


def _page(context) -> CrmPage:
    """Build a CRM page object over this scenario's session.

    :param context: behave's ``Context``.  ``context.driver`` is the session
        ``features/environment.py``'s ``before_scenario`` published for the
        scenario now running.
    :returns: A fresh :class:`~app.pages.crm_page.CrmPage` bound to that
        session.

    The port of the ``CrmP crm = new CrmP()`` field at ``Crm.java:16``, moved
    from module scope to a per-call helper: a module-level instance would bind
    whichever worker process imported this module first, and would outlive the
    session it captured, because ``after_scenario`` quits the driver and the
    next scenario gets a new one.

    Constructing a page object costs nothing observable - ``BasePage.__init__``
    stores the driver and does no more, locating no element and touching no
    session - so calling this once per step is not a lookup the Java code
    avoided.
    """
    return CrmPage(context.driver)


@step("User click on the crm dashboard")
def user_click_on_the_crm_dashboard(context) -> None:
    """Open the CRM module from the Odoo main menu.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:21-25``: click the CRM menu entry, then wait for it to be
    visible.  Click-then-wait is the source's order and is kept; the two
    accesses to the accessor are the source's two proxy accesses.

    The ``When`` step all four of the feature's scenarios open with, and the
    only definition here whose Java method was already named in snake_case.
    """
    page = _page(context)

    page.crm_link.click()
    wait_visible_element(page.crm_link, 2)


@step("User click on the pipeline button")
def user_click_on_the_pipeline_button(context) -> None:
    """Open the new-pipeline dialog with Odoo's Create button.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:27-32``: click the access-key ``c`` Create button, then
    wait on it.  ``CREATE_BUTTON`` is the name ``Crm.java:29`` uses, even
    though ``CREATE_CUSTOMER`` carries the identical selector.
    """
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.create_button, 2)


@step("User can create the new pipeline")
def user_can_create_the_new_pipeline(context) -> None:
    """Fill the new-pipeline dialog and confirm it.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:34-44``, in order: type ``"test"`` and Enter into the
    opportunity title (``:36``); open the customer autocomplete (``:37``) and
    pick the suggestion (``:38``); clear the expected revenue (``:39``) and
    type ``"8"`` and Enter into it (``:40``); set the priority (``:41``);
    confirm the dialog (``:42``) and wait on the confirm button (``:43``).

    Each ``sendKeys(literal + Keys.ENTER)`` is **one** keyboard call, not two:
    Java concatenates the literal and the key into a single argument, and
    :func:`~app.automation.press_keys` sends the text followed by the resolved
    keys in a single ``send_keys``, so the browser sees the same one call.
    """
    page = _page(context)

    press_keys(page.opportunity_title, "ENTER", text="test")
    page.customer.click()
    page.customer_id.click()
    page.expected_revenue.clear()
    press_keys(page.expected_revenue, "ENTER", text="8")
    page.priority.click()
    page.create_pipeline.click()
    wait_visible_element(page.create_pipeline, 2)


@step("User can see the total price")
def user_can_see_the_total_price(context) -> None:
    """Check the first column's total against the expected figure.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises ValueError: When the column total is not a decimal integer - the
        Python counterpart of ``Integer.parseInt`` throwing
        ``NumberFormatException``.  Deliberately not caught and given no
        fallback: the Java step fails there, so this one does too.
    :raises AssertionError: When the two figures differ, which is what the
        baseline artifact records for this step.

    Ports ``Crm.java:46-56``: parse the total, add ``8`` (``:48``), compare
    against the literal ``89`` (``:49``), print both figures (``:51-52``) and
    assert (``:54``).  Both numbers and the addition are the source's and are
    load-bearing.

    ``Assert.assertEquals(totalPrice, price)`` is the two-argument form, so the
    port asserts with no message and keeps the operand order: the baseline's
    ``expected:<8> but was:<89>`` shows the parsed total was JUnit's *expected*
    argument.
    """
    page = _page(context)

    total_price = int(page.total_price.text) + 8
    price = 89

    print(f"totalPrice = {total_price}")
    print(f"price = {price}")

    assert total_price == price


@step("User can see new pipeline")
def user_can_see_new_pipeline(context) -> None:
    """Check the new pipeline card carries the title just entered.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises AssertionError: When the card's title is not ``"test"``.

    Ports ``Crm.java:58-68``: read the first card's title (``:60``), compare it
    against ``"test"`` (``:61``) - lower-case, and not the ``"Test2"`` that
    *User can verify the information* expects - print both (``:63-64``) and
    assert (``:66``).  Two-argument ``assertEquals``, so the expected value is
    the left operand and there is no message.
    """
    page = _page(context)

    actual_name = page.find_title_test.text
    expected_name = "test"

    print(f"actualName = {actual_name}")
    print(f"expectedName = {expected_name}")

    assert expected_name == actual_name


# The phrase is kept on one line, apostrophe and space-before-comma included,
# so that it can be checked byte-for-byte against Crm.feature:18 by eye or by
# grep. Cucumber's {string} matches the quotes around the value and passes the
# value unquoted; behave's equivalent puts those quotes in the pattern
# literally around a named field. The Python literal is single-quoted because
# the phrase itself contains double quotes, which is why the apostrophe in
# "user's" is escaped. Neither the spacing nor the pattern may be widened: it
# resolves unambiguously against every step use in the suite as written.
@step('User can change any user\'s information like "{opportunity}" , "{revenue}" and "{probability}"')
def user_can_change_any_user_s_information_like_and(
    context,
    opportunity,
    revenue,
    probability,
) -> None:
    """Edit an existing pipeline's title, revenue and probability.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :param opportunity: The new opportunity title, from the Examples table -
        ``Test2`` at ``Crm.feature:24``.
    :param revenue: The new expected revenue - ``30`` in that row.
    :param probability: The new probability - ``2`` in that row.
    :returns: ``None``.

    Ports ``Crm.java:70-81``, in order: open the first pipeline card (``:72``)
    and wait on it (``:73``); enter edit mode (``:74``); then clear and retype
    each of the three fields - title (``:75-76``), expected revenue
    (``:77-78``) and probability (``:79-80``) - each value followed by Enter in
    a single keyboard call, as the Java concatenation does.

    The only definition here that takes parameters, and the only one that
    neither waits at the end, prints, nor asserts: saving and verifying are the
    next two steps' work.  ``OPPORTUNITY_TITLE_EDIT`` is the name
    ``Crm.java:75`` uses even though ``INPUT_NAME`` carries the same selector,
    and it addresses the same field as ``OPPORTUNITY_TITLE`` by a different
    strategy - an XPath on the ``name`` attribute rather than ``By.NAME`` -
    which is the source's own duplication.
    """
    page = _page(context)

    page.button_pipeline.click()
    wait_visible_element(page.button_pipeline, 2)
    page.edit_button.click()
    page.opportunity_title_edit.clear()
    press_keys(page.opportunity_title_edit, "ENTER", text=opportunity)
    page.expected_revenue_edit.clear()
    press_keys(page.expected_revenue_edit, "ENTER", text=revenue)
    page.probability_edit.clear()
    press_keys(page.probability_edit, "ENTER", text=probability)


@step("User can save information")
def user_can_save_information(context) -> None:
    """Save the edited pipeline with Odoo's Save button.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:83-87``: wait for the probability field the previous step
    left focused (``:85``), then click the access-key ``s`` Save button
    (``:86``).  This is the class's one genuine wait-then-click site - every
    other pairing clicks first - and the order is the source's.
    """
    page = _page(context)

    wait_visible_element(page.probability_edit, 2)
    page.save_edit.click()


@step("User can verify the information")
def user_can_verify_the_information(context) -> None:
    """Reopen the pipeline list and check the edited title took effect.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises AssertionError: When the first card's title is not ``"Test2"``.

    Ports ``Crm.java:89-104``: click the Pipeline entry in the CRM sidebar
    (``:91``), wait (``:92``), read the first card's title (``:94``), compare
    it against ``"Test2"`` (``:95``) - the Examples row's value, and
    capitalised where the other two comparisons expect lower-case ``"test"`` -
    print both (``:97-98``) and assert (``:100``).

    **The wait is on a different element from the one clicked.**  ``:91``
    clicks ``pipelineSideButton`` and ``:92`` waits on ``buttonPipeline``, the
    card the sidebar navigation is expected to reveal.  It reads like a
    copy-paste slip and it is preserved exactly; swapping in
    ``PIPELINE_SIDE_BUTTON`` would wait on the element already known to be
    present and stop waiting for the one the next read depends on.
    """
    page = _page(context)

    page.pipeline_side_button.click()
    wait_visible_element(page.button_pipeline, 2)

    actual_name = page.find_title_test.text
    expected_name = "Test2"

    print(f"actualName = {actual_name}")
    print(f"expectedName = {expected_name}")

    assert expected_name == actual_name


@step("User can drag and drop the pipeline")
def user_can_drag_and_drop_the_pipeline(context) -> None:
    """Drag the first kanban card into the second column.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:106-119``: build a fresh action chain (``:108``), then
    hold the first column's card, pause, move onto the second column's card,
    pause, release and perform (``:110-115``), and finally wait out a fixed
    delay (``:117``).

    **The two pauses are the module's one genuine unit conversion.**  Java's
    ``Actions.pause`` takes milliseconds, so ``.pause(2000)`` there is two
    seconds; Python's ``ActionChains.pause`` takes seconds, so the same two
    seconds is ``.pause(2)``.  Transcribed literally it would be 2000 seconds -
    over half an hour per pause - and the scenario would hang rather than fail.

    **The delay after** ``perform()`` **is separate and is not a pause.**
    ``Thread.sleep(2000)`` is likewise milliseconds and becomes ``sleep(2)``,
    kept as a fixed delay at the same call site rather than converted into an
    explicit wait: it is one of the seventeen fixed delays AAP 0.4.1 requires
    be preserved as-is, because the drop is animated and re-rendered
    client-side and a wait on some element would be a different behaviour, not
    a faster equivalent.

    ``PROGRESS_PIPELINE`` is the name ``Crm.java:110`` uses for the drag
    source, though ``BUTTON_PIPELINE`` carries the identical selector; the
    source names the same card once per role and both names are kept.  A new
    chain is built per call, mirroring ``new Actions(Driver.getDriver())``:
    a shared one would still hold the previous scenario's queued actions and
    replay them here.
    """
    page = _page(context)

    (
        action_chain()
        .click_and_hold(page.progress_pipeline)
        .pause(2)
        .move_to_element(page.progress_pipeline2)
        .pause(2)
        .release()
        .perform()
    )

    sleep(2)


@step("User can see the new changes in progress")
def user_can_see_the_new_changes_in_progress(context) -> None:
    """Check the dragged card now carries its title in the second column.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises AssertionError: When the second column's card title is not
        ``"test"``.

    Ports ``Crm.java:121-130``: read the second column's card title (``:123``),
    compare against ``"test"`` (``:124``), print both (``:126-127``) and assert
    (``:129``).  ``TEST_VERIFY`` reaches that title through a descendant step
    (``//div[2]``) where ``FIND_TITLE_TEST`` uses a child step (``/div[2]``);
    the difference is the source's.
    """
    page = _page(context)

    actual_name = page.test_verify.text
    expected_name = "test"

    print(f"actualName = {actual_name}")
    print(f"expectedName = {expected_name}")

    assert expected_name == actual_name


@step("User can register new customer")
def user_can_register_new_customer(context) -> None:
    """Create a customer from the CRM sidebar and search for it.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:132-143``, in order: open the Customers view (``:134``)
    and wait on the sidebar entry (``:135``); click Create (``:136``) and wait
    on it (``:137``); type ``"Test"`` and Enter into the name field (``:138``);
    confirm the dialog (``:139``) and wait on the confirm button (``:140``);
    then type ``"aa"`` and Enter into the search input (``:141``).

    Three of the module's eleven waits are here, and all three follow the click
    they wait on.  Two accessor names are the twins of names used earlier -
    ``CREATE_CUSTOMER`` shares its selector with ``CREATE_BUTTON`` and
    ``CREATE_CUSTOMER_BUTTON`` with ``CREATE_PIPELINE`` - and each call site
    uses the name its own Java line uses.  ``INPUT_NAME`` at ``:138`` is
    likewise the twin of ``OPPORTUNITY_TITLE_EDIT``.
    """
    page = _page(context)

    page.customer_side_button.click()
    wait_visible_element(page.customer_side_button, 2)
    page.create_customer.click()
    wait_visible_element(page.create_customer, 2)
    press_keys(page.input_name, "ENTER", text="Test")
    page.create_customer_button.click()
    wait_visible_element(page.create_customer_button, 2)
    press_keys(page.searching_text, "ENTER", text="aa")


@step("User can print the profile")
def user_can_print_the_profile(context) -> None:
    """Open the customer found by the search and print its profile.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.

    Ports ``Crm.java:145-153``: click the search result (``:147``) and wait on
    it (``:148``); open the print menu (``:149``); wait on the menu's Print
    section (``:150``) and click it (``:151``) - the feature's last
    interaction.

    **The second wait targets a different element from the one clicked.**
    ``:149`` clicks ``printButton`` and ``:150`` waits on
    ``duePaymentButton``, the entry that click is expected to reveal and which
    ``:151`` then clicks.  Unlike ``:92`` this one is even defensible, and
    either way it is preserved: waiting on ``PRINT_BUTTON`` instead would drop
    the only wait guarding the final click.  This step asserts nothing and
    prints nothing, exactly as the Java method does.
    """
    page = _page(context)

    page.name_customer.click()
    wait_visible_element(page.name_customer, 2)
    page.print_button.click()
    wait_visible_element(page.due_payment_button, 2)
    page.due_payment_button.click()
