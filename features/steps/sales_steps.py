"""Sales step definitions - the port of ``Sales.java``.

Three of the seven bodies are wrong in ways a reviewer reaches for, and AAP 0.8
keeps every one of them.  ``Sales.java:66-77`` and ``:87-96`` each build an
actual and an expected string, print both and end, so neither step can fail on
a mismatch - only on a lookup or an expiring wait.  ``:31-37`` weighs the
constant ``"Customers - Odoo"`` against the live title with ``"Customers - "``
prefixed to it again, under names the wrong way round, so the module's one
check fails on the very page it exists to confirm.

The seven definitions, in Java declaration order
------------------------------------------------
``Sales.java:19``
    ``User click on the sales dashboard`` ->
    :func:`user_click_on_the_sales_dashboard`
``Sales.java:25``
    ``User click customers button`` -> :func:`user_click_customers_button`,
    the only body in the module that checks anything
``Sales.java:40``
    ``User can create the customer`` -> :func:`user_can_create_the_customer`
``Sales.java:54``
    ``User can save the customer`` -> :func:`user_can_save_the_customer`
``Sales.java:66``
    ``User can find his name "<name>" from search bar`` ->
    :func:`user_can_find_his_name_from_search_bar`, the module's only
    parameterized phrase and its only keyboard send
``Sales.java:80``
    ``User can create new customer`` -> :func:`user_can_create_new_customer`
``Sales.java:87``
    ``User can get the error`` -> :func:`user_can_get_the_error`

Sixteen of the page's twenty locators are reached by those seven bodies.  The
other four - the plural customer-card locator, the second save button, the
Customers link and the kanban detail span - are declared on the page object and
referenced by no step class in the reference at all.  They stay unreferenced
here; a step invented for one of them would be an addition the request never
made.

Three source defects, carried over deliberately
-----------------------------------------------
AAP 0.2.2 is explicit that beyond the nineteen inventoried deviations nothing in
the source is corrected, and AAP 0.8 puts it as *"Preserve, do not tidy."*  This
module is where that bites hardest: three of its seven bodies are wrong in ways
a reviewer reaches for, and every one of those repairs would change which
scenarios pass.

**The last two steps compare nothing.**  ``Sales.java:66-77`` and ``:87-96``
each build an *actual* string and an *expected* string, write both to standard
output, and end.  The comparison their naming plainly intends is absent from the
Java body, so neither step can fail on a value mismatch - the only ways either
fails are an element that cannot be located and a wait that expires.
:func:`user_can_find_his_name_from_search_bar` and
:func:`user_can_get_the_error` reproduce that exactly: no comparison, and
therefore no way for a page value that contradicts the literal to fail them.
What must not disappear is either text read: ``:72`` reads the kanban heading
and ``:91`` the notification area, and those two reads are the whole of what
makes these steps able to fail at all - so each is performed here through
:func:`_read_element_text`, which states in one place why a read whose value
goes nowhere is load-bearing.  The two *literals* those bodies bind - ``:71``'s
``"Lucas"`` and ``:90``'s ``"The following fields are invalid:"`` - do not
disappear either: AAP 0.4.1 puts "the same hard-coded literals and expected
values" in the per-module parity obligation, and a value the Java body binds
and never compares is still a value the Java body carries.  Each is bound here
at the position its Java statement occupies, ahead of the read that follows it,
and consumed by nothing, because nothing in the Java body consumes it.  What
went with the prints is only the *output*; see "The six prints are not
reproduced" below.

**The Customers-title check has its names the wrong way round, and a doubled
prefix.**  ``Sales.java:31-32`` binds the constant ``"Customers - Odoo"`` to a
variable called ``actualTitle`` and the live browser title to one called
``expectedTitle``, and it puts ``"Customers - "`` in front of that live title
although an Odoo Customers page already reports exactly ``Customers - Odoo``.
The check at ``:37`` therefore weighs ``"Customers - Odoo"`` against
``"Customers - Customers - Odoo"`` and fails on the very page it exists to
confirm.  :func:`user_click_customers_button` keeps the inverted names, the
doubled prefix and the JUnit operand order, so that failure is reproduced
rather than repaired.

**That check's message carries no trailing space.**  ``Sales.java:37`` reads
``"The title is not same as the expected!"``.  The same sentence ends in a
space in ``LoginSD.java:46`` and ``LogOutSD.java:27`` and does not in
``Calendar.java:46``; the difference is real, it is per module, and this
module's form is reproduced byte-for-byte.  It is the only check in the file.

Registration: one decorator, and the choice is behavioural
----------------------------------------------------------
Every definition below is registered with behave's type-agnostic decorator, and
the module names none of the three keyword-specific ones (AAP 0.5.2,
deviation 7).  Cucumber-JVM matches a step by its text alone, whereas behave
resolves by effective step type, and this module leans on the difference twice:

* ``Sales.java:80`` declares the sixth definition with ``@And``, an annotation
  behave has no counterpart decorator for at all - there is nothing else to map
  it onto.  ``Sales.java`` is one of only three reference classes declaring a
  step that way.
* ``Sales.feature:10`` invokes the shared precondition as ``Given User login to
  test other features`` while its Java declaration is ``@When``
  (``Session.java:12``).  The baseline JSON report records that step as
  ``"keyword":"Given "`` matched against
  ``Session.user_login_to_test_other_features()`` - the JVM crossing step types
  in the reference run itself.  That phrase belongs to
  ``features/steps/session_steps.py`` and is deliberately not re-declared here.

Import boundary (AAP 0.4.2)
---------------------------
The three imports below are this module's complete and closed list.  The
browser-automation library is never named here, ``app/automation`` being the
only package in the port permitted to touch it, and nothing else is reached
either:

* no chained-pointer-input helper, since ``Sales.java`` uses no ``Actions``;
  AAP 0.4.2 fixes that import site as the CRM and Notes step modules alone,
* no locator-strategy re-export, which only the Login step module needs, for
  ``LoginSD.java:56``,
* nothing from the configuration accessors, because ``Sales.java`` reads no
  property whatever,
* no session lifecycle function, because ``features/environment.py`` owns
  creating and disposing of the browser session exclusively (AAP 0.3.3) - no
  step opens or closes one,
* no timed pause, because this class contains none; the reference's seventeen
  fixed delays all sit in five other step classes.

Of the wait family only the visibility wait is taken, because
``ExpectedConditions.visibilityOf`` is the only condition this class uses, at
all eight of its wait statements.

Per-scenario binding, and the 4-second timeout
----------------------------------------------
``Sales.java:15,17`` builds its page object and its explicit-wait object as
instance fields, at glue construction.  Neither may be a module-level value in
Python: a worker process imports this module once, and a value built at import
time would bind whatever session happened to exist at that moment for every
scenario that worker later runs.  So :func:`_page` builds a page object per
call from ``context.driver``, the session ``before_scenario`` publishes, and the
wait becomes a timeout argument supplied at each call site.

That timeout is ``4`` seconds, from ``Sales.java:17``, and it is the only 4 in
the suite - the reference fixes 2 seconds in ``Calendar`` and ``Crm``, 3 in
``LoginSD``, ``LogOutSD`` and ``EmployeeStage``, 20 in ``Contacts``,
``Inventory`` and ``Notes``, and 4 here alone.  It is written as a literal at
every one of the eight call sites rather than hidden behind a module constant,
so it stays exactly as visible as the Java field was.

The six prints are not reproduced
---------------------------------
``Sales.java:34-35``, ``:74-75`` and ``:93-94`` write six lines to standard
output - the Customers page title, a customer's name read from the kanban
heading, the notification area's text, and the literals each is paired with.
This module writes none of them, and sends them nowhere else either: not to a
logger, not to an attachment and not to a file.

Three of those six values are read live from the system under test, and
``app/services/test_run_service.py`` relays every worker stdout line into the
parent logger and from there into the Jenkins console, so each line became a
durable record of live customer data and PII (CWE-532/359; the security
review's finding F04).  The statements are removed outright rather than reduced
to value-free labels, because the relay carries *anything* on stdout: a fixed
label would still add a line to every CI log while diagnosing nothing.

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
not enumerated there either.

What the removal does *not* touch: every click, every one of the eight waits
and its 4-second timeout, the single keyboard call, both text reads, the one
assertion of ``:37`` with its operand order and its message, the two inverted
names and doubled prefix that assertion depends on, and every hard-coded value
the six prints happened to carry.  ``actual_title`` at ``:31`` stays, because
``:37`` compares it; the literals of ``:71`` and ``:90`` stay because AAP 0.4.1
counts a body's hard-coded literals as parity whether or not anything reads
them - only the two ``System.out.println`` pairs that happened to read them are
gone.

**How those two are bound, and why not some other way.**  Each is written
``_ = "<literal>"`` at the statement position its Java line occupies.  The
discard target is chosen over the Java form - a named local assigned once and
never read - because ``pyflakes`` reports that as F841 and this tree carries
none; ``_`` states in the code what the Java body leaves to inference, that
nothing consumes the value.  It is chosen over a bare literal expression
statement for the reason :func:`_read_element_text` gives about a bare
``page.name_check.text``: an expression statement gives a reader no sign that
it is deliberate.  And the literal sits in the body rather than in a
module-level constant, for two measured reasons: every step module of this port
is held to an exact module-scope inventory and an exact import surface by
``tests/test_steps_registration.py`` (AAP 0.4.2), so a module constant would
put ``sales_steps`` into a census that names five other modules, and a
``Final`` annotation on it would need a ``typing`` import this layer does not
take - ``crm_steps`` records the same convention.  The body position is also
the more faithful of the two: ``Sales.java`` binds each literal *inside* the
method, immediately before the read it precedes.

Feature context
---------------
``features/Sales.feature`` carries **no feature-level tag**, so the default
``@Smoke`` filter (``CukesRunner.java:18``) does not select it; reaching it
means naming the file or using a negative tag expression.  Its title begins
``.... app Sales feature`` - four literal dots, which AAP 0.6 flags as an edge
case for the JSON scenario-``id`` slug rule and which is preserved like every
other line of Gherkin.  One Background carrying the shared precondition, two
plain scenarios, and one Scenario Outline whose single ``Examples: Employee's
name`` row supplies ``Lucas`` to the parameterized phrase - the value the first
scenario also passes to it as a literal.

The Odoo, Testinium and Upgenix vocabularies all occur across the reference and
AAP Conflict 8 forbids reconciling them, so every literal below is written as
the source writes it.  Nothing here catches an exception either: an expiring
wait, a missing element or a stale one travels straight out and fails the step,
exactly as it does in the unhandled Java body.
"""

from behave import step

from app.automation import press_keys, wait_visible_element
from app.pages import SalesPage


def _page(context) -> SalesPage:
    return SalesPage(context.driver)


def _read_element_text(element) -> str:
    """Dereference *element* and read its text, for the read itself.

    :param element: A resolved element, handed over by a page accessor - which
        re-resolves its locator on every access, as ``PageFactory``'s proxies
        did.
    :returns: The element's text.  Both call sites discard it, exactly as the
        two Java bodies discard the locals they bind it to; it is returned
        rather than swallowed so that this function reads as the value-
        producing expression it ports and stays usable if a later step needs
        the value.

    The ``getText()`` half of ``Sales.java:72`` and ``:91``.  Those two reads
    are the *only* way their steps can fail - neither body compares anything -
    so the read has to happen at its own position in the body even now that its
    value feeds nothing: a missing kanban heading or notification area raises
    ``NoSuchElementException`` here, which is the failure the Java step had, and
    dropping the read would turn two steps that can fail into two that cannot.

    Reading through this named call is what keeps that intent legible once the
    prints of ``:74-75`` and ``:93-94`` are gone (module docstring).  The
    alternatives both read as mistakes: a local assigned and never used is what
    the Java bodies have and what any reader would delete, and a bare
    ``page.name_check.text`` statement gives no sign that the dereference is
    the point.
    """
    return element.text


@step("User click on the sales dashboard")
def user_click_on_the_sales_dashboard(context) -> None:
    page = _page(context)

    page.sales_partial.click()
    wait_visible_element(page.SALES_PARTIAL, 4)


@step("User click customers button")
def user_click_customers_button(context) -> None:
    """Open the Customers list and check the page title it produces.

    The port of ``Sales.java:25-38``: a click, a wait, two locals and the
    module's one check - which, exactly as in Java, weighs a constant against a
    doubly prefixed live title under names that are the wrong way round.

    * ``:28`` clicks the Customers menu entry and ``:29`` waits 4 seconds on
      it,
    * ``:31`` binds the literal ``"Customers - Odoo"`` to ``actualTitle``,
    * ``:32`` binds ``"Customers - "`` followed by the browser's own title to
      ``expectedTitle``,
    * ``:34-35`` write both out, the second under the label ``expected = ``,
      and are the one part of the body this port does not reproduce; the module
      docstring records why,
    * ``:37`` compares them with the JUnit helper, whose arguments are the
      message, ``expectedTitle`` and ``actualTitle`` in that order.

    :param context: behave's ``Context``; supplies the session, and its
        ``title`` for the live half of the comparison.
    :returns: ``None``.
    :raises AssertionError: Whenever the two strings differ, which on a real
        Odoo Customers page is always - see the module docstring on why that
        outcome is the faithful one and must not be repaired.
    """
    page = _page(context)

    page.customers_button.click()
    wait_visible_element(page.CUSTOMERS_BUTTON, 4)

    # Sales.java:31-32, inverted names and doubled prefix intact. The variable
    # called "actual" holds the constant; the one called "expected" holds the
    # live title with "Customers - " put in front of a title that already
    # begins with it.
    actual_title = "Customers - Odoo"
    expected_title = "Customers - " + context.driver.title

    # Sales.java:34-35 print both strings - the second under the label
    # "expected = ", an asymmetry of the source - and this port writes neither.
    # The live browser title is data read from the system under test, and every
    # worker stdout line is relayed into the parent logger and the Jenkins
    # console, so each run left that title in durable worker and CI logs
    # (CWE-532/359, finding F04). The statements are removed rather than
    # stripped of their values, because the relay carries anything on stdout
    # and a fixed label would add a CI log line that diagnoses nothing. No
    # parity is lost: diagnostic stdout is in neither AAP 0.1.2's frozen list
    # nor AAP 0.4.1's per-step enumeration (module docstring). Both locals stay
    # - :37 below compares them, with the source's inverted names and doubled
    # prefix intact.

    assert expected_title == actual_title, "The title is not same as the expected!"


@step("User can create the customer")
def user_can_create_the_customer(context) -> None:
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.CREATE_BUTTON, 4)
    page.customer_name.send_keys("Lucas")
    page.address.send_keys("1 boulevard auguste rodin 75000")
    page.state_options.click()
    page.create_and_edit_state.click()
    page.state_name.send_keys("Albania")
    page.state_code.send_keys("78")
    page.country_state_button.click()
    page.country_selection.click()


@step("User can save the customer")
def user_can_save_the_customer(context) -> None:
    page = _page(context)

    page.save_button.click()
    wait_visible_element(page.SAVE_BUTTON, 4)
    page.create_customer.click()
    wait_visible_element(page.CREATE_CUSTOMER, 4)
    page.customers_button.click()
    wait_visible_element(page.CUSTOMERS_BUTTON, 4)


@step('User can find his name "{name}" from search bar')
def user_can_find_his_name_from_search_bar(context, name) -> None:
    """Search the Customers list for a name and read the kanban heading back.

    The port of ``Sales.java:66-77``, and the module's two special cases at
    once - its only parameter and its only keyboard send:

    * ``:68`` concatenates the parameter with the Enter key and hands the pair
      to a single ``sendKeys`` call on the search bar.  The keyboard helper
      reproduces that as one call carrying the literal first and the named key
      second, which is the same character stream the Java form produces and the
      same number of browser calls: one.
    * ``:69`` waits 4 seconds on the search bar.
    * ``:71`` binds the literal ``"Lucas"`` to ``actualName`` - a constant even
      under the outline, whose single row happens to supply that same value.
      The literal is bound here at that same position, ahead of the read that
      follows it, and consumed by nothing, because nothing in the Java body
      consumes it either.
    * ``:72`` reads the kanban heading's text, which **is** reproduced, through
      :func:`_read_element_text`: no comparison is made here, so that read is
      the step's only failure mode besides the wait.
    * ``:74-75`` write both values to standard output, and the body ends there.
      Neither line is reproduced; the module docstring records why.

    :param context: behave's ``Context``; supplies the session.
    :param name: The customer name to search for, taken from the phrase.
    :returns: ``None``.

    Cucumber's ``{string}`` consumes the quotation marks around the value and
    hands over the text between them, so the behave pattern carries those marks
    literally around a named field.  ``Sales.feature`` invokes the phrase twice
    - with the literal ``"Lucas"`` at ``:17`` and through the outline's
    ``<name>`` substitution at ``:28`` - and both arrive here with *name*
    holding ``Lucas``.

    A value the heading does not match cannot fail this step, since nothing
    compares the two.  A heading that is absent can, and does: the read at
    ``:72`` is what gives this step its only failure mode besides the wait.
    """
    page = _page(context)

    press_keys(page.search_bar, "ENTER", text=name)
    wait_visible_element(page.SEARCH_BAR, 4)

    # Sales.java:71, actualName = "Lucas", bound at its own position and read
    # by nothing. AAP 0.4.1's per-module obligation names "the same hard-coded
    # literals and expected values", so the literal stays even though the
    # print that was its only reader does not: no comparison exists in
    # Sales.java:66-77 and this port adds none. The discard target is what
    # makes the non-consumption deliberate rather than accidental; a named
    # local would be a pyflakes F841 and this module carries none.
    _ = "Lucas"

    # Sales.java:72. The read stays exactly where it was, because it is this
    # step's only failure mode besides the wait: nothing here compares
    # anything, so a missing heading raising NoSuchElementException is all that
    # can fail, and the read survives the print's removal for that reason.
    _read_element_text(page.name_check)

    # Sales.java:74-75 print the two strings this body builds, and neither line
    # is written here: one of them is a customer's name read live from the
    # kanban heading, and the worker's stdout is relayed into the parent logger
    # and the Jenkins console, so printing it put PII into durable worker and
    # CI logs (CWE-532/359, finding F04). Removed outright rather than made
    # value-free - the relay carries anything on stdout - and no parity is
    # lost: diagnostic stdout is in neither AAP 0.1.2's frozen list nor AAP
    # 0.4.1's per-step enumeration (module docstring). Only the two writes go;
    # both values they wrote are still built above.


@step("User can create new customer")
def user_can_create_new_customer(context) -> None:
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.CREATE_BUTTON, 4)
    page.create_customer.click()


@step("User can get the error")
def user_can_get_the_error(context) -> None:
    """Read the validation notice a blank customer form produces.

    The port of ``Sales.java:87-96``, the shortest body in the class and the
    second that compares nothing:

    * ``:90`` binds the literal ``"The following fields are invalid:"`` -
      trailing colon included - to ``actualWarning``.  The literal is bound
      here at that same position, ahead of the read that follows it, and
      consumed by nothing, because nothing in the Java body consumes it
      either,
    * ``:91`` reads the notification area's text, reproduced through
      :func:`_read_element_text` because that read is the whole of what this
      step can fail on,
    * ``:93-94`` write both values to standard output, and the body ends.
      Neither line is reproduced; the module docstring records why.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    No click, no wait, no navigation and no comparison: the read at ``:91`` is
    the only thing this step does to the browser and the only way it can fail.
    Whatever the notification area actually says - the expected sentence, some
    other Odoo notice, or nothing at all - this step passes, because it weighs
    nothing against anything.
    """
    page = _page(context)

    # Sales.java:90, actualWarning = "The following fields are invalid:", the
    # first statement of the Java body and so bound here ahead of the read of
    # :91, in the source's statement order. AAP 0.4.1 enumerates "the same
    # hard-coded literals and expected values" per module and this is one of
    # them; nothing consumes it because Sales.java:87-96 weighs it against
    # nothing, and the discard target says so where a named local would only be
    # a pyflakes F841. The trailing colon is the source's and is part of the
    # literal.
    _ = "The following fields are invalid:"

    # Sales.java:91. The read stays: it is the only thing this step does to the
    # browser and its only way to fail, which is why it survives the print that
    # consumed it.
    _read_element_text(page.warning)

    # Sales.java:93-94 print both strings this body builds, and neither line is
    # written here: the notification text is read live from the system under
    # test, and the worker's stdout is relayed into the parent logger and the
    # Jenkins console, so each run left that value in durable worker and CI
    # logs (CWE-532/359, finding F04). Removed outright rather than made
    # value-free, because the relay carries anything on stdout, and no parity
    # is lost - diagnostic stdout is in neither AAP 0.1.2's frozen list nor AAP
    # 0.4.1's per-step enumeration (module docstring). As in the search step,
    # only the writing goes; both values it wrote are still built above.
