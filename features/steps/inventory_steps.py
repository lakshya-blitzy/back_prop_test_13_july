r"""Inventory step definitions - the port of ``Inventory.java``.

Java anchor
-----------
``src/main/java/com/testinium/step_definitions/Inventory.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  63 lines: two fields - ``InventoryP inventory`` at
``:12`` and a **20-second** ``WebDriverWait`` at ``:13`` - and nine step
methods.  This module carries all nine, in the source's own order, and adds
nothing.

**The defining constraint of this file: it makes no truth check at all.**
``Inventory.java``'s import block is ``Then``, ``When``, ``InventoryP``,
``Driver``, ``ExpectedConditions`` and ``WebDriverWait`` - and nothing else.
There is **no** ``org.junit.Assert``, which is why four of the nine bodies call
a boolean-returning method and throw the answer away.  Read *Four calls whose
result is discarded* below before changing a line here: adding the "missing"
checks would change which scenarios pass, and AAP 0.2.2 is explicit that
beyond the nineteen deviations AAP 0.1.3 inventories, nothing in the source is
corrected.

The nine definitions
--------------------
Phrases are byte-exact copies of the Java annotations, and the Python function
names are the Java method names unchanged - the Cucumber JSON ``match.location``
carries a dotted Python path (AAP 0.1.3 deviation 8), so these names are part of
the report contract rather than free-form.  No phrase takes a parameter, so no
pattern here contains a substitution field.

===  =========  ==========================================  ===================
 #   Java line  Phrase                                      Body
===  =========  ==========================================  ===================
 1   ``:15``    Logged user clicks on Inventory Module      click
 2   ``:20``    User clicks on Product module               wait 20s, click
 3   ``:26``    User see the products                       title read, dropped
 4   ``:31``    User clicks create button                   click
 5   ``:36``    User clicks the save button                  wait 20s, click
 6   ``:42``    User should see the error                   displayed?, dropped
 7   ``:47``    User enters Product Name                    send ``"IBM"``
 8   ``:52``    User should see the title includes the       displayed?, dropped
                Product Name
 9   ``:57``    User sees the created Product               displayed?, dropped
===  =========  ==========================================  ===================

Definitions 2 and 5 **wait first and click second** (``Inventory.java:22-23``
and ``:38-39``).  Other step classes in the suite click first and wait
afterwards; there is no house ordering to impose, so each class keeps its own
and this one waits first at both of its two wait call sites.

Four calls whose result is discarded
------------------------------------
``Inventory.java:28``, ``:44``, ``:54`` and ``:59`` are bare expression
statements.  Each performs a real operation - a title read, or an element
lookup followed by ``isDisplayed()`` - and then ignores the boolean it
produced.  The consequence is precise, and it is the behaviour being preserved:

* When the element is **absent**, the lookup raises ``NoSuchElementException``
  and the step fails.  That is the only way these four steps can fail, and it
  is what makes the calls meaningful rather than dead.
* When the element **is** present but the condition is false - not displayed,
  or a page title that is not ``"Products - Odoo"`` - **the step passes.**

Each is therefore written here as a plain statement whose value is unused,
never wrapped in a truth check.  Together with ``Calendar.java:194``'s
``tagsCheckbox.isSelected()`` these are the suite's five result-discarded
calls, and this is the only one of the ten step modules that checks nothing.
The Python keyword that would make such a check fail is absent from this file
entirely - bodies, comments and prose alike - so a blunt ``grep`` over the
module is proof of the invariant on its own, which is how the unit suite reads
it.

Registration: ``@step`` only
----------------------------
Every definition registers with :func:`behave.step`, and the keyword-specific
decorators are neither imported nor used.  That is behavioural, not stylistic
(AAP 0.5.2, deviation 7): Cucumber-JVM matches a step by its text alone, so
``Given``, ``When``, ``Then`` and ``And`` are interchangeable at match time,
whereas behave resolves by *effective* step type - where an ``And`` inherits
the keyword of the step before it.  ``@step`` matches whatever keyword invoked
it and so reproduces text-only matching exactly.

``features/Inventory.feature`` shows why it matters, twice over.  Its
Background step at ``:9`` is ``Given User login to test other features``, whose
Java declaration is ``@When`` (``Session.java:12``); the baseline
``target/cucumber.json`` records that step as ``"keyword":"Given "`` against
``match.location`` ``Session.user_login_to_test_other_features()`` - direct
evidence that the JVM matched across types.  And within this feature the three
definitions Java declares ``@Then`` (#6, #8 and #9) are reached through ``And``
and ``Then`` chains whose effective type can be ``When``; uniform ``@step``
registration is what keeps them resolving regardless.

Import boundary (AAP 0.4.2)
---------------------------
The import list below is complete and closed: the Gherkin decorator, one wait
helper, one page class.  What is deliberately absent, and why:

* **No browser-library import of any kind**, not even a guarded one.
  ``app.automation`` is the only package in the port permitted to import it,
  and the unit suite checks this directory to prove no step module does.
* **No locator-strategy constant.**  Building a locator inline is
  ``login_steps.py``'s exception alone (``LoginSD.java:56``);
  ``InventoryP.java``'s eight locators live in
  :class:`~app.pages.inventory_page.InventoryPage`, and this module only reads
  its accessors.
* **No keyboard-key or action-chain helper.**  ``Inventory.java`` imports
  neither the key-constant class nor the action-chain class, so #7's
  ``sendKeys("IBM")`` (``Inventory.java:49``) is a plain literal send on a
  single element.
* **Nothing from the port's configuration module.**  ``Inventory.java`` reads
  no configuration property - no URL, no credential, no expected title - so no
  configuration key is reachable from this module.
* **Neither session-lifecycle function.**  ``features/environment.py`` owns
  the scenario lifecycle exclusively (AAP 0.3.3: no step or page ever creates
  or quits a driver), and this module never opens or closes a session.
* **Only ``wait_visible_element``** from the wait family: visibility of an
  element already resolved is the only wait shape this class uses, at
  ``Inventory.java:22`` and ``:38``.
* **No fixed delay.**  This class declares none - it is one of the five
  delay-free step classes, alongside login, logout, sales and session - so the
  seventeen fixed delays AAP 0.4.1 preserves elsewhere have no call site here.
* **No sibling page class.**  :class:`~app.pages.inventory_page.InventoryPage`
  has two consumers, but the sharing runs one way only: ``notes_steps.py``
  imports it because ``Notes.java:16-17`` declares ``InventoryP`` beside
  ``NotesP``, and nothing flows back here.

Binding per scenario, not per import
------------------------------------
``Inventory.java:12-13`` builds the page object and the wait as fields, at glue
construction.  Neither can be a module-level object in Python: this module is
imported once per worker process, and a page object or a wait built at import
time would bind whichever session that worker happened to hold first.  So

* the page object is built **inside** every step body, from the session
  ``features/environment.py`` publishes as ``context.driver``, through the
  :func:`_page` helper.  Construction is free and side-effect-free -
  ``BasePage.__init__`` stores the driver and touches neither the DOM nor the
  session - and the accessors re-resolve the element on every access, exactly
  as the ``PageFactory`` proxies they replace; and
* the wait is a **timeout argument, not an object**.
  :func:`~app.automation.wait_visible_element` declares no default, so this
  class's 20 seconds stays visible as a literal at both of its call sites.

The observable behaviour is unchanged: a page bound to the current session, and
this class's own timeout.

Feature context, all of it deliberate
-------------------------------------
``features/Inventory.feature`` carries **no feature-level tag**, so the default
``@Smoke`` filter (``CukesRunner.java:18``) does not select it - it is
reachable only by a negative expression such as ``not @Smoke`` or by naming the
file, which is also how a dry run has to be pointed at it.  Its header is
mis-titled ``Feature: Testinium app Inventory feature``, a title it shares with
``Contact.feature:1`` and which therefore produces a duplicate JSON ``id``.  AAP
0.6 records both and fixes neither.  The Odoo, Testinium and Upgenix
vocabularies stay exactly where they occur, ``"Products - Odoo"`` included: AAP
0.1.3 Conflict 8 forbids reconciling the three names.

Its four scenarios use these definitions in four different combinations, and
one phrase they use is **not** defined here: ``Then User should see the
dashboard`` (``Inventory.feature:16``) has no counterpart in
``Inventory.java``, so it belongs to the module that ports the class declaring
it.  The Background phrase belongs to ``session_steps.py`` for the same reason.
Redeclaring either here would make it ambiguous.

Nothing in this module prints, delays, catches an exception or reads
configuration.  ``TimeoutException``, ``NoSuchElementException`` and everything
else propagate uncaught, which is what the Java bodies do and what fails the
step.
"""

from behave import step

from app.automation import wait_visible_element
from app.pages import InventoryPage


def _page(context) -> InventoryPage:
    """Bind an :class:`~app.pages.inventory_page.InventoryPage` to this scenario.

    The stand-in for ``InventoryP inventory = new InventoryP()``
    (``Inventory.java:12``), moved from glue-construction time into each step
    body for the reason the module docstring gives: a module-level instance
    would bind the session of whichever worker imported this module first.

    :param context: behave's ``Context``.  Only ``context.driver`` is read -
        the session ``features/environment.py`` published in
        ``before_scenario``.
    :returns: A page object bound to that session.  Building it resolves
        nothing: ``BasePage.__init__`` stores the driver and stops, so no
        lookup happens until an accessor is read.

    A ``None`` driver is passed straight through, which is deliberate: an
    unrecognised ``browser`` property yields no session, because
    ``Driver.java:29-42`` has no default branch, and
    :attr:`BasePage.driver <app.pages.base_page.BasePage.driver>` then falls
    back to this worker's current session.  The failure surfaces at the first
    element lookup exactly as it does in Java; validating the session here
    would move it out of the step that needed it.
    """
    return InventoryPage(context.driver)


@step("Logged user clicks on Inventory Module")
def logged_user_clicks_on_inventory_module(context) -> None:
    """Click the Inventory entry in Odoo's top-level menu.

    ``Inventory.java:15-18``.  One statement, ``inventory.inventoryModule
    .click()`` at ``:17``, and deliberately **no wait in front of it**: the
    session's 10-second implicit wait (``Driver.java:34``) is what makes the
    lookup retry, and an explicit wait the source does not have would change
    this step's timing.

    The first step of all four scenarios in ``features/Inventory.feature``
    (lines 12, 19, 27 and 34), each reached after the Background has logged in.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When the menu entry is not in the DOM once
        the implicit wait has elapsed.  Propagated, which fails the step just
        as the Java body does.
    """
    _page(context).inventory_module.click()


@step("User clicks on Product module")
def user_clicks_on_product_module(context) -> None:
    """Wait for the Products menu entry to become visible, then click it.

    ``Inventory.java:20-24``.  Two statements, in the source's order - **wait
    first, click second**::

        wait.until(ExpectedConditions.visibilityOf(inventory.products));  // :22
        inventory.products.click();                                       // :23

    The timeout is the 20 seconds ``Inventory.java:13`` constructs, passed as a
    literal because the helper declares no default.

    ``page.products`` is read **twice**, once as the wait target and once for
    the click, and that repetition is the parity: the Java field is a
    ``PageFactory`` proxy that re-resolves on each use, so the element is
    located afresh for the click rather than reused from the wait's return
    value.  The accessors of
    :class:`~app.pages.inventory_page.InventoryPage` behave the same way.

    The feature's last scenario clicks this entry twice (lines 35 and 39),
    returning to the product list to look for what it saved.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises TimeoutException: When the entry is still not visible after 20
        seconds.  Propagated uncaught.
    :raises NoSuchElementException: When the entry is absent from the DOM at
        either lookup.
    """
    page = _page(context)
    wait_visible_element(page.products, 20)
    page.products.click()


@step("User see the products")
def user_see_the_products(context) -> None:
    """Compare the page title with ``"Products - Odoo"``, then drop the answer.

    ``Inventory.java:26-29``.  The whole body is one statement at ``:28``::

        Driver.getDriver().getTitle().equals("Products - Odoo");

    which computes a boolean and ignores it.  **This step therefore passes
    whatever the title is.**  It is reproduced exactly, as a bare comparison
    whose value is unused - not checked, and not deleted either, because the
    read really is performed and a session that cannot answer it still fails
    the step.  The starkest of this module's four result-discarded calls; the
    module docstring sets out why all four stay check-free.

    ``getTitle()`` is a method in Java and a property in Python, so the
    translation reads ``context.driver.title``.  The expected string is carried
    byte-exact, its Odoo vocabulary included (AAP 0.1.3 Conflict 8).

    Used once, by the first scenario (``Inventory.feature:14``).

    :param context: behave's ``Context``.  ``context.driver`` is read directly
        here rather than through :func:`_page`, mirroring the source's
        ``Driver.getDriver()`` - this is the one body that needs no page
        object.
    :returns: ``None``.
    :raises WebDriverException: When the session cannot report a title, for
        instance because it has already ended.  Propagated uncaught.
    """
    # A pointless-looking comparison, and the point is that it is pointless:
    # the source computes it and drops it. A linter flags the statement (ruff
    # B015, "useless comparison"), so the suppression below is here to stop
    # anyone "fixing" behaviour the port is required to preserve. Never turn
    # this into a truth check, a raise or a branch.
    context.driver.title == "Products - Odoo"  # noqa: B015


@step("User clicks create button")
def user_clicks_create_button(context) -> None:
    """Click the Kanban Create button on the Products list.

    ``Inventory.java:31-34``.  One statement, ``inventory.createBtn.click()``
    at ``:33``, with **no wait in front of it** - the source waits before the
    Products entry and before Save, and not here, so neither does this.  The
    locator is the hyphenated class ``o-kanban-button-new``
    (``InventoryP.java:20``), preserved by
    :class:`~app.pages.inventory_page.InventoryPage`.

    Used by all four scenarios (lines 15, 21, 29 and 36).

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When the button is not in the DOM once the
        implicit wait has elapsed.  Propagated uncaught.
    """
    _page(context).create_btn.click()


@step("User clicks the save button")
def user_clicks_the_save_button(context) -> None:
    """Wait for the product form's Save button to be visible, then click it.

    ``Inventory.java:36-40``.  The module's second and last wait call site,
    and again in the source's order - **wait first, click second**::

        wait.until(ExpectedConditions.visibilityOf(inventory.saveBtn));  // :38
        inventory.saveBtn.click();                                       // :39

    The same 20 seconds as definition 2, written out at this call site rather
    than shared through a module-level object, and ``page.save_btn`` read twice
    for the same re-resolution reason.

    Three scenarios use it: the two that save a named product (lines 23 and 38)
    and the one that saves the form deliberately blank (line 30) to raise the
    validation error definition 6 then looks for.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises TimeoutException: When the button is still not visible after 20
        seconds.  Propagated uncaught.
    :raises NoSuchElementException: When the button is absent from the DOM at
        either lookup.
    """
    page = _page(context)
    wait_visible_element(page.save_btn, 20)
    page.save_btn.click()


@step("User should see the error")
def user_should_see_the_error(context) -> None:
    """Ask Odoo's notification container whether it is displayed, and drop the answer.

    ``Inventory.java:42-45``.  The whole body is one statement at ``:44``::

        inventory.fieldError.isDisplayed();

    Java declares this method ``@Then``, and the JSON baseline shows the JVM
    matching a definition across step types, which is why registration here is
    ``@step`` like every other - within this feature the phrase is reached as a
    ``Then`` after a chain of ``And`` steps (``Inventory.feature:31``).

    Result-discarded, so **the step passes when the container exists but is
    hidden**, and fails only when it is absent altogether.  The lookup itself
    is the whole of it, and its answer stays unchecked (AAP 0.2.2).

    Used once, by the third scenario, which saves the product form with the
    name field left blank and expects the validation error.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When no notification container is in the
        DOM once the implicit wait has elapsed - the one failure mode this
        step has.  Propagated uncaught.
    """
    _page(context).field_error.is_displayed()


@step("User enters Product Name")
def user_enters_product_name(context) -> None:
    """Type the literal ``"IBM"`` into the product form's Name input.

    ``Inventory.java:47-50``.  One statement, ``inventory.productName
    .sendKeys("IBM")`` at ``:49``.  The value is hard-coded in the source even
    though the phrase reads like a parameterized one, so **this definition
    takes no argument** and the literal is carried byte-exact; parameterizing
    it would add a substitution field the Gherkin never supplies.

    A plain literal send on a single element: no key-constant class and no
    action chain is involved, which is why neither helper is imported.  The
    field is reached through the server-generated id
    ``o_field_input_479`` (``InventoryP.java:29``) - brittle by nature and
    preserved as written, with no fallback locator.

    Used by the second and fourth scenarios (lines 22 and 37).

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When no element carries that generated id
        once the implicit wait has elapsed - the expected outcome whenever Odoo
        has re-numbered the form.  Propagated uncaught, exactly as in Java.
    """
    _page(context).product_name.send_keys("IBM")


@step("User should see the title includes the Product Name")
def user_should_see_the_title_includes_the_product_name(context) -> None:
    """Ask the product-list entry whether it is displayed, and drop the answer.

    ``Inventory.java:52-55``.  The whole body is one statement at ``:54``::

        inventory.productsList.isDisplayed();

    Despite the phrase, no title is read and no name is compared: the source
    looks up ``//span[.='EY']`` (``InventoryP.java:32``) - test data baked into
    a selector, and not the ``"IBM"`` definition 7 types - and discards the
    boolean.  Both the phrase and the mismatch are preserved (AAP 0.8:
    *preserve, do not tidy*), so **the step passes when that span exists but is
    hidden** and fails only when it is absent.

    Used once, by the fourth scenario, after it saves a product and returns to
    the Products list.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When no matching span is in the DOM once
        the implicit wait has elapsed - the one failure mode this step has.
        Propagated uncaught.
    """
    _page(context).products_list.is_displayed()


@step("User sees the created Product")
def user_sees_the_created_product(context) -> None:
    """Ask the saved record's name field whether it is displayed, and drop the answer.

    ``Inventory.java:57-60``.  The whole body is one statement at ``:59``::

        inventory.createdProduct.isDisplayed();

    The last of the four result-discarded calls, and the last definition of the
    class - ``Inventory.java`` ends here, so this module defines no tenth step.
    **Passes when the field exists but is hidden**, fails only when it is
    absent.

    Used once, by the second scenario, as the closing step that looks like a
    verification and verifies nothing.

    :param context: behave's ``Context``; supplies ``context.driver``.
    :returns: ``None``.
    :raises NoSuchElementException: When the saved record's name field is not
        in the DOM once the implicit wait has elapsed - the one failure mode
        this step has.  Propagated uncaught.
    """
    _page(context).created_product.is_displayed()
