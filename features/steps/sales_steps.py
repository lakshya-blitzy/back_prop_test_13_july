"""Sales step definitions - the port of ``Sales.java``.

The Java anchor
---------------
``src/main/java/com/testinium/step_definitions/Sales.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  Ninety-nine lines and seven step methods driving the Odoo
Sales module's customer flow - dashboard, Customers list, new-customer form,
save, search - over the twenty locators of ``SalesP.java``, ported to
:class:`app.pages.sales_page.SalesPage`.  Every function below cites the Java
lines it reproduces, in the order it reproduces them.

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
:func:`user_can_get_the_error` reproduce that exactly: two locals, two lines of
output, no comparison.  Both locals are kept although nothing reads them
afterwards, precisely as in Java, and a static-analysis tool flagging them is
the expected outcome rather than a reason to delete them.  What must not
disappear is either text read: ``:72`` reads the kanban heading and ``:91`` the
notification area, and those two reads are the whole of what makes these steps
able to fail at all.

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
  (``Session.java:12``).  The baseline ``target/cucumber.json`` records that
  step as ``"keyword":"Given "`` matched against
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

Standard output is behaviour
----------------------------
Six lines reach standard output, from ``Sales.java:34-35``, ``:74-75`` and
``:93-94``, and their labels keep the Java spelling and spacing - camelCase,
one space either side of the ``=``.  One of the six is asymmetric: ``:34``
writes ``actualTitle = `` while ``:35`` writes ``expected = ``, not
``expectedTitle = ``.  That asymmetry is the source's and is kept.  ``Crm.java``
and ``Sales.java`` are the only reference classes writing to standard output at
all - fourteen sites between them, of which six are here.

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
    """Bind a page object to the session this scenario is running against.

    The stand-in for ``Sales.java:15``'s ``SalesP salesp = new SalesP()``,
    moved out of module scope for the reason the module docstring gives, and
    called at the head of all seven bodies below.

    :param context: behave's ``Context``.  Only ``context.driver`` is read -
        the session ``features/environment.py`` publishes in
        ``before_scenario`` and clears in ``after_scenario``.
    :returns: A fresh :class:`~app.pages.sales_page.SalesPage` bound to that
        session.

    Construction is free of side effects: the page object stores the session
    and does nothing else, locating no element and touching no browser, so the
    cost of building one per step is a single attribute assignment.  Each
    locator accessor on the returned object re-resolves against the live DOM
    every time it is read, which is what ``PageFactory``'s proxies did for the
    Java field.
    """
    return SalesPage(context.driver)


@step("User click on the sales dashboard")
def user_click_on_the_sales_dashboard(context) -> None:
    """Open the Sales module from the Odoo apps dashboard.

    The port of ``Sales.java:19-23``, whose whole body is two statements:

    * ``:21`` clicks the ``Sales`` entry, the page's one partial-link-text
      locator,
    * ``:22`` waits up to 4 seconds for that same element to become visible.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    The order is the source's and stays that way - the click first, the wait
    second - so what is waited on is an element the click has already acted
    upon.  Both statements name the same Java field, so both re-resolve the
    locator here, one lookup each.
    """
    page = _page(context)

    page.sales_partial.click()
    wait_visible_element(page.sales_partial, 4)


@step("User click customers button")
def user_click_customers_button(context) -> None:
    """Open the Customers list and check the page title it produces.

    The port of ``Sales.java:25-38``: a click, a wait, two locals, two lines of
    standard output and the module's one check - which, exactly as in Java,
    weighs a constant against a doubly prefixed live title under names that are
    the wrong way round.

    * ``:28`` clicks the Customers menu entry and ``:29`` waits 4 seconds on
      it,
    * ``:31`` binds the literal ``"Customers - Odoo"`` to ``actualTitle``,
    * ``:32`` binds ``"Customers - "`` followed by the browser's own title to
      ``expectedTitle``,
    * ``:34-35`` write both out, the second under the label ``expected = ``,
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
    wait_visible_element(page.customers_button, 4)

    # Sales.java:31-32, inverted names and doubled prefix intact. The variable
    # called "actual" holds the constant; the one called "expected" holds the
    # live title with "Customers - " put in front of a title that already
    # begins with it.
    actual_title = "Customers - Odoo"
    expected_title = "Customers - " + context.driver.title

    # Sales.java:34-35. The second label reads "expected = ", not
    # "expectedTitle = "; the asymmetry is in the source.
    print(f"actualTitle = {actual_title}")
    print(f"expected = {expected_title}")

    # Sales.java:37. Operand order carried over from the JUnit call's
    # (message, expectedTitle, actualTitle), and the message reproduced without
    # the trailing space its Login and Logout counterparts carry.
    assert expected_title == actual_title, "The title is not same as the expected!"


@step("User can create the customer")
def user_can_create_the_customer(context) -> None:
    """Fill the new-customer form, creating a new state along the way.

    The port of ``Sales.java:40-52``, ten statements whose order matters
    because each one opens what the next one needs:

    * ``:42`` clicks the kanban ``Create`` button and ``:43`` waits 4 seconds
      on it,
    * ``:44`` types ``"Lucas"`` into the customer name,
    * ``:45`` types ``"1 boulevard auguste rodin 75000"`` into the address,
    * ``:46`` clicks the state autocomplete and ``:47`` its
      ``Create and Edit...`` entry, which opens the state sub-form,
    * ``:48`` types ``"Albania"`` as the state name and ``:49`` ``"78"`` as its
      code,
    * ``:50`` clicks the sub-form's country field and ``:51`` the country
      suggestion it offers.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    Four literal sends and no key among them - this class's one key send
    belongs to :func:`user_can_find_his_name_from_search_bar`.  The wait at
    ``:43`` is the only one the source has here: the eight statements after it
    run unguarded in Java, with nothing but the session's ten-second implicit
    wait (``Driver.java:34``) between them, and they run unguarded here.
    """
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.create_button, 4)
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
    """Save the state sub-form, save the customer, and return to the list.

    The port of ``Sales.java:54-63``: the click-then-wait pair three times
    over, on three different elements and never in the other order.

    * ``:56-57`` the state sub-form's save button,
    * ``:58-59`` the customer form's save button,
    * ``:60-61`` the Customers menu entry, which lands the browser back on the
      list that :func:`user_can_find_his_name_from_search_bar` then searches.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    The third pair repeats the pair of :func:`user_click_customers_button` on
    the same locator, minus that step's title check - so the two functions
    share a locator and nothing else, and neither delegates to the other.  Six
    statements, six lookups: each Java field reference resolves again, here as
    there.
    """
    page = _page(context)

    page.save_button.click()
    wait_visible_element(page.save_button, 4)
    page.create_customer.click()
    wait_visible_element(page.create_customer, 4)
    page.customers_button.click()
    wait_visible_element(page.customers_button, 4)


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
    * ``:72`` reads the kanban heading's text into ``expectedName``.
    * ``:74-75`` write both to standard output, and the body ends there.  No
      comparison is made; the module docstring records why none is added.

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
    wait_visible_element(page.search_bar, 4)

    # Sales.java:71-72. Both locals go to standard output below and are never
    # compared with one another - the source's behaviour, not an omission of
    # this port.
    actual_name = "Lucas"
    expected_name = page.name_check.text

    # Sales.java:74-75.
    print(f"actualName = {actual_name}")
    print(f"expectedName = {expected_name}")


@step("User can create new customer")
def user_can_create_new_customer(context) -> None:
    """Open a blank new-customer form and try to save it unfilled.

    The port of ``Sales.java:80-85``, the one definition the reference declares
    with ``@And``:

    * ``:82`` clicks the kanban ``Create`` button and ``:83`` waits 4 seconds
      on it,
    * ``:84`` clicks the form's save button on an untouched form, which is what
      provokes the validation notice :func:`user_can_get_the_error` reads next.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    ``:84`` is the class's only click with no wait after it, and the step ends
    on it; the session's ten-second implicit wait (``Driver.java:34``) is all
    that stands between it and the next step's read.  The first two statements
    repeat those of :func:`user_can_create_the_customer` on the same locator,
    and are written out again rather than shared, because that is how the two
    Java bodies are written.
    """
    page = _page(context)

    page.create_button.click()
    wait_visible_element(page.create_button, 4)
    page.create_customer.click()


@step("User can get the error")
def user_can_get_the_error(context) -> None:
    """Read the validation notice a blank customer form produces.

    The port of ``Sales.java:87-96``, the shortest body in the class and the
    second that compares nothing:

    * ``:90`` binds the literal ``"The following fields are invalid:"`` -
      trailing colon included - to ``actualWarning``,
    * ``:91`` reads the notification area's text into ``expectedWarning``,
    * ``:93-94`` write both to standard output, and the body ends.

    :param context: behave's ``Context``; supplies the session.
    :returns: ``None``.

    No click, no wait, no navigation and no comparison: the read at ``:91`` is
    the only thing this step does to the browser and the only way it can fail.
    Whatever the notification area actually says - the expected sentence, some
    other Odoo notice, or nothing at all - this step passes, because the two
    strings it builds are never weighed against each other.
    """
    page = _page(context)

    # Sales.java:90-91. Computed, written out, never compared - as in Java.
    actual_warning = "The following fields are invalid:"
    expected_warning = page.warning.text

    # Sales.java:93-94.
    print(f"actualWarning = {actual_warning}")
    print(f"expectedWarning = {expected_warning}")
