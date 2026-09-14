"""CRM step definitions - the behave port of the Java ``Crm`` glue class.

``Crm.feature:1`` carries the suite's only ``@Smoke`` tag - ``behave.ini``'s
``default_tags`` - so a bare ``run-tests`` executes these twelve definitions.

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
* **No exception handling anywhere.**  ``Integer.parseInt`` throws on
  non-numeric text and Python's ``int()`` raises ``ValueError``; a wait that
  expires raises ``TimeoutException``; a missing element raises
  ``NoSuchElementException``.  All of them propagate untouched, so the scenario
  fails where the Java scenario failed, and the engine records the failure.

The one thing deliberately *not* reproduced: the eight prints
-------------------------------------------------------------
``Crm.java:51-52``, ``:63-64``, ``:97-98`` and ``:126-127`` write a parsed
column total and three pipeline card titles, each beside its expected literal,
to standard output with ``System.out.println``.  This module writes none of
them, and routes none of them anywhere else either - not to a logger, not to an
attachment, not to a file.

Half of those values are read live from the system under test, and
``app/services/test_run_service.py`` relays every worker stdout line into the
parent logger and from there into the Jenkins console, so each line became a
durable record of live customer and pricing data in the worker and CI logs
(CWE-532/359; the security review's finding F04).  The statements are removed
outright rather than reduced to a value-free label, because the relay carries
*anything* on stdout: a fixed label would still add a line to every CI log
while diagnosing nothing.

Removing them costs no parity.  AAP 0.1.2's list of what the port must not
change covers the artifact paths and schemas, the ``@Smoke`` default, the six
configuration keys, the Gherkin text and Examples data, the nine explicit waits
and the seventeen fixed sleeps, the assertion subjects and message strings, and
the Jenkins stages and thresholds - diagnostic stdout appears nowhere in it.
AAP 0.4.1's per-module parity obligation enumerates what each step body must
reproduce - the navigation targets and their property sources, the locators,
the wait target and timeout, the keyboard keys and action-chain sequences, the
hard-coded literals and expected values, the assertion subject and message
text, and the no-ops where a Java body is empty - and diagnostic printing is
not enumerated there either.  Everything that *is* enumerated survives
untouched in all four bodies: the reads still happen at the same point, the
locals the source compares keep their literals and their operand order, and the
four assertions are unchanged.

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


def _page(context) -> CrmPage:
    return CrmPage(context.driver)


@step("User click on the crm dashboard")
def user_click_on_the_crm_dashboard(context) -> None:
    page = _page(context)

    page.crm_link.click()
    wait_visible_element(page.CRM_LINK, 2)


@step("User click on the pipeline button")
def user_click_on_the_pipeline_button(context) -> None:
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.CREATE_BUTTON, 2)


@step("User can create the new pipeline")
def user_can_create_the_new_pipeline(context) -> None:
    page = _page(context)

    press_keys(page.opportunity_title, "ENTER", text="test")
    page.customer.click()
    page.customer_id.click()
    page.expected_revenue.clear()
    press_keys(page.expected_revenue, "ENTER", text="8")
    page.priority.click()
    page.create_pipeline.click()
    wait_visible_element(page.CREATE_PIPELINE, 2)


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
    against the literal ``89`` (``:49``) and assert (``:54``).  Both numbers
    and the addition are the source's and are load-bearing.  ``:51-52``'s two
    prints are the one part of the body not reproduced; the module docstring
    records why.

    ``Assert.assertEquals(totalPrice, price)`` is the two-argument form, so the
    port asserts with no message and keeps the operand order: the baseline's
    ``expected:<8> but was:<89>`` shows the parsed total was JUnit's *expected*
    argument.
    """
    page = _page(context)

    total_price = int(page.total_price.text) + 8
    price = 89

    # Crm.java:51-52 print the parsed total and the expected figure, and this
    # port writes neither: the total is read live from the system under test,
    # and every worker stdout line is relayed into the parent logger and the
    # Jenkins console, which turned each line into a durable record of live
    # pricing data (CWE-532/359, finding F04). The statements are gone rather
    # than stripped of their values, because a fixed label would still add a
    # line to every CI log and diagnose nothing. No parity is lost: diagnostic
    # stdout is in neither AAP 0.1.2's frozen list nor AAP 0.4.1's per-step
    # enumeration (see the module docstring). Both locals stay - they are the
    # arithmetic and the expected value of :48-49, which that enumeration does
    # cover, and :54 compares them below in the source's operand order.
    assert total_price == price


@step("User can see new pipeline")
def user_can_see_new_pipeline(context) -> None:
    """Check the new pipeline card carries the title just entered.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises AssertionError: When the card's title is not ``"test"``.

    Ports ``Crm.java:58-68``: read the first card's title (``:60``), compare it
    against ``"test"`` (``:61``) - lower-case, and not the ``"Test2"`` that
    *User can verify the information* expects - and assert (``:66``).
    Two-argument ``assertEquals``, so the expected value is the left operand
    and there is no message.  ``:63-64``'s two prints are not reproduced; the
    module docstring records why.
    """
    page = _page(context)

    actual_name = page.find_title_test.text
    expected_name = "test"

    # Crm.java:63-64 print the card title read from the system under test
    # beside its expected literal; neither line is written here. The live title
    # is customer data, and the worker's stdout is relayed into the parent
    # logger and the Jenkins console, which made every run a durable record of
    # it (CWE-532/359, finding F04). Removed rather than made value-free, for
    # the reason the module docstring gives, and no parity is lost because
    # diagnostic stdout is in neither AAP 0.1.2's frozen list nor AAP 0.4.1's
    # per-step enumeration. The read above and the assertion below - which
    # both of those do cover - are untouched.
    assert expected_name == actual_name


# The phrase is one line, apostrophe and space-before-comma included, matching
# Crm.feature:18 byte-for-byte. Cucumber's {string} matches the quotes around a
# value and passes it unquoted, while behave's pattern carries those quotes
# literally around each named field; the literal is single-quoted because the
# phrase contains double quotes, which is why the apostrophe is escaped.
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
    neither waits at the end nor asserts: saving and verifying are the next two
    steps' work.  ``OPPORTUNITY_TITLE_EDIT`` is the name
    ``Crm.java:75`` uses even though ``INPUT_NAME`` carries the same selector,
    and it addresses the same field as ``OPPORTUNITY_TITLE`` by a different
    strategy - an XPath on the ``name`` attribute rather than ``By.NAME`` -
    which is the source's own duplication.
    """
    page = _page(context)

    page.button_pipeline.click()
    wait_visible_element(page.BUTTON_PIPELINE, 2)
    page.edit_button.click()
    page.opportunity_title_edit.clear()
    press_keys(page.opportunity_title_edit, "ENTER", text=opportunity)
    page.expected_revenue_edit.clear()
    press_keys(page.expected_revenue_edit, "ENTER", text=revenue)
    page.probability_edit.clear()
    press_keys(page.probability_edit, "ENTER", text=probability)


@step("User can save information")
def user_can_save_information(context) -> None:
    page = _page(context)

    wait_visible_element(page.PROBABILITY_EDIT, 2)
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
    and assert (``:100``).  ``:97-98``'s two prints are not reproduced; the
    module docstring records why.

    **The wait is on a different element from the one clicked.**  ``:91``
    clicks ``pipelineSideButton`` and ``:92`` waits on ``buttonPipeline``, the
    card the sidebar navigation is expected to reveal.  It reads like a
    copy-paste slip and it is preserved exactly; swapping in
    ``PIPELINE_SIDE_BUTTON`` would wait on the element already known to be
    present and stop waiting for the one the next read depends on.
    """
    page = _page(context)

    page.pipeline_side_button.click()
    wait_visible_element(page.BUTTON_PIPELINE, 2)

    actual_name = page.find_title_test.text
    expected_name = "Test2"

    # Crm.java:97-98 print the edited card's live title beside its expected
    # literal, and neither line is written here: the worker's stdout is relayed
    # into the parent logger and the Jenkins console, so printing the title
    # published live customer data into durable logs (CWE-532/359, finding
    # F04). Removed whole rather than made value-free (module docstring), and
    # parity is intact - diagnostic stdout is in neither AAP 0.1.2's frozen
    # list nor AAP 0.4.1's per-step enumeration, while the click, the wait, the
    # read, the literal and the assertion, which are enumerated, all stand.
    assert expected_name == actual_name


@step("User can drag and drop the pipeline")
def user_can_drag_and_drop_the_pipeline(context) -> None:
    """Drag the first kanban card into the second column.

    A fresh chain per call mirrors ``Crm.java:108``'s ``new Actions(...)``; a
    shared one would replay the previous scenario's queued actions.  Java's
    pause and sleep are milliseconds and Python's are seconds, so ``2000``
    becomes ``2`` in both; the trailing delay stays a delay, not a wait.
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
    compare against ``"test"`` (``:124``) and assert (``:129``).
    ``TEST_VERIFY`` reaches that title through a descendant step (``//div[2]``)
    where ``FIND_TITLE_TEST`` uses a child step (``/div[2]``); the difference is
    the source's.  ``:126-127``'s two prints are not reproduced; the module
    docstring records why.
    """
    page = _page(context)

    actual_name = page.test_verify.text
    expected_name = "test"

    # Crm.java:126-127 print the dragged card's live title beside its expected
    # literal; this port writes neither, because the worker's stdout reaches
    # the parent logger and the Jenkins console and made that live customer
    # value a durable log record (CWE-532/359, finding F04). Removed rather
    # than emptied of its values for the reason the module docstring gives, and
    # no parity is lost: diagnostic stdout is in neither AAP 0.1.2's frozen
    # list nor AAP 0.4.1's per-step enumeration, and the read, the literal and
    # the assertion those do cover are unchanged.
    assert expected_name == actual_name


@step("User can register new customer")
def user_can_register_new_customer(context) -> None:
    page = _page(context)

    page.customer_side_button.click()
    wait_visible_element(page.CUSTOMER_SIDE_BUTTON, 2)
    page.create_customer.click()
    wait_visible_element(page.CREATE_CUSTOMER, 2)
    press_keys(page.input_name, "ENTER", text="Test")
    page.create_customer_button.click()
    wait_visible_element(page.CREATE_CUSTOMER_BUTTON, 2)
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
    the only wait guarding the final click.  This step asserts nothing, exactly
    as the Java method does, and - like every body in this module now - writes
    nothing to standard output.
    """
    page = _page(context)

    page.name_customer.click()
    wait_visible_element(page.NAME_CUSTOMER, 2)
    page.print_button.click()
    wait_visible_element(page.DUE_PAYMENT_BUTTON, 2)
    page.due_payment_button.click()
