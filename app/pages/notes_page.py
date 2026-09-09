r"""Page object for the Notes module - the port of ``NotesP.java``.

Anchored on ``src/main/java/com/testinium/pages/NotesP.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by
AAP 0.2.1 and never modified.  That file is 43 lines: a constructor calling
``PageFactory.initElements(Driver.getDriver(), this)`` (``NotesP.java:10-12``)
and **ten** ``@FindBy`` fields (``NotesP.java:14-42``).  It declares **no
methods**, so neither does this module - it is ten locator constants and
nothing else.

Everything mechanical lives in the base class
---------------------------------------------
:class:`~app.pages.base_page.BasePage` is the Python stand-in for Java's
``PageFactory``/``@FindBy`` pair, which AAP goal G5 replaces with *"explicit
locator constants resolved lazily"*.  Declaring an upper-case constant here is
the whole of the work: ``BasePage.__init_subclass__`` installs a read-only
accessor under the lower-case name that re-runs ``find_element`` on **every**
access - never cached, matching the ``PageFactory`` proxy the reference relied
on - and publishes the inventory as :attr:`NotesPage.LOCATORS` in declaration
order.  So both access forms work off one declaration:

.. code-block:: python

    NotesPage.SAVE_BTN          # the ('xpath', "...") tuple, for wait_visible()
    page.save_btn.click()       # the live element, resolved right now

Consumed by ``features/steps/notes_steps.py``, the port of
``Notes.java``, which reaches all ten fields.  That class also declares
``InventoryP inventoryP`` alongside ``NotesP notesP`` (``Notes.java:16-17``)
and clicks ``inventoryP.saveBtn`` at ``Notes.java:65``, so the step module
imports :class:`~app.pages.inventory_page.InventoryPage` separately and the
two pages' locators are **not** merged here.

Source quirks preserved verbatim - do not "fix" these
-----------------------------------------------------
AAP 0.2.2 carries the source's inconsistencies over untouched, and AAP 0.8
states the rule as *"Preserve, do not tidy"*.  Five of them land in this file,
and each one looks like a mistake precisely because it is one - in the source,
where it is authoritative:

* **:attr:`~NotesPage.TABINDEX` has spaces around its ``=``** -
  ``//a[. = 'Create and Edit...']`` (``NotesP.java:14``).  ``SalesP.java:35``
  writes the same idea as ``//li[.='Create and Edit...']`` with **no** spaces
  and a different element, and ``app/pages/sales_page.py`` keeps that spelling
  just as literally.  Neither is normalized and the two are deliberately not
  factored into a shared constant: they are different selectors in different
  pages that happen to name the same Odoo affordance.  XPath treats the two as
  equivalent, so nothing functional turns on it - which is exactly why a
  future reader would be tempted to unify them, and why this note exists.
* **The name ``tabindex`` describes nothing about the element it locates** - it
  finds a "Create and Edit..." anchor, not a tab index.  ``NotesP.java:14-15``
  names it that, so the constant is :attr:`~NotesPage.TABINDEX` and the
  accessor is ``tabindex``.  Renaming it to something clearer would break the
  locator-agreement assertion in ``tests/test_steps_notes.py``, which checks
  this page against the ``@FindBy`` fields of the Java class field by field.
* **:attr:`~NotesPage.NEW_TABLE` and :attr:`~NotesPage.TODAY_TABLE` hard-code
  record ids** ``1193`` and ``1194`` (``NotesP.java:38`` and ``:41``) and index
  **different** children - ``[2]`` for the New column, ``[1]`` for Today.  The
  ids, the wrapping parentheses and the index positions are preserved exactly
  and the pair is not parameterized into a helper: the drag-and-drop step at
  ``Notes.java:76-80`` depends on these two specific columns of one specific
  Odoo record, and a parameterized version would be a capability the source
  does not have.
* **The abbreviations are meaningless, and they are the source's** - ``tagsN``
  becomes :attr:`~NotesPage.TAGS_N` (``NotesP.java:23-24``) and ``appK``
  becomes :attr:`~NotesPage.APP_K` (``NotesP.java:35-36``).
* **The two button classes disagree with each other about separators** -
  :attr:`~NotesPage.CREATING_NOTES` matches the hyphenated
  ``o-kanban-button-new`` (``NotesP.java:20``) while
  :attr:`~NotesPage.SAVE_BTN` matches the underscored ``o_form_button_save``
  (``NotesP.java:32``).  Both are verbatim; the inconsistency is Odoo's markup,
  not a transcription slip.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else"*, and that package is the only one permitted to import the
browser-automation library.  This module therefore imports exactly two names -
:data:`~app.automation.By` and :class:`~app.pages.base_page.BasePage` - and
nothing further:

* **No browser-library import of any kind**, not even under a
  ``typing.TYPE_CHECKING`` guard: a guarded import is still an import
  statement and would trip the grep-based boundary check the suite performs.
  This module is deliberately written so that grepping it for the automation
  library's name finds nothing at all, which is the same discipline
  ``app/pages/base_page.py`` keeps - hence "browser-automation library" in
  prose throughout, where an ordinary comment would simply name it.
* **Neither of the keyboard or action-chain helpers is imported**, even though
  ``Notes.java`` imports both ``Keys`` (``:9``) and ``Actions`` (``:10``).
  AAP 0.4.2 fixes both helpers' import sites as step modules - the keyboard
  helper in ``crm_steps``, ``notes_steps`` and ``sales_steps``, the
  action-chain helper in ``crm_steps`` and ``notes_steps`` - so the
  ``sendKeys(..., Keys.ENTER)`` at ``Notes.java:35`` and the
  ``clickAndHold(...).pause(...).release()`` chain at ``Notes.java:79`` are
  ``notes_steps.py``'s work, not this file's.  A page object reaches
  ``app.automation`` for the current driver and for nothing else.
* **No ``app.config``** - page objects never read configuration; the step
  modules do, which is where the Java classes read it.
* **No service, no reporting writer, no path helper, no web framework and no
  Gherkin engine.**

This module also never creates, quits or configures a driver, per AAP 0.3.3:
*"One owner for the lifecycle, so no step or page ever creates or quits a
driver."*  ``features/environment.py`` owns it.  Importing this module has no
side effects at all - it starts no browser, reads nothing and touches no
filesystem - which is what lets the unit suite import it on a machine with no
browser installed.

What is deliberately absent
---------------------------
``NotesP.java`` has no methods, so this module has no ``create_note()``, no
tag-entry helper, no navigation and no assertion helper; the timings the flow
needs - the 20-second ``WebDriverWait`` at ``Notes.java:19`` and the single
2-second ``Thread.sleep`` at ``Notes.java:23`` - are supplied at the
``notes_steps.py`` call sites, because AAP 0.4.1 preserves each fixed delay
*at the same call site* and each explicit wait with its own per-class timeout.
There are exactly ten locators: no eleventh, and none belonging to Inventory.

Feature context
---------------
``features/Notes.feature`` carries **no feature-level tag** and has three
scenarios - create a note, edit a note, and drag a card from the New column to
Today.  Its header reads "Feature: Testinium app login feature", which is
mis-titled in the source and preserved anyway; the consequence is that it
shares a JSON ``id`` with ``Login.feature``, which AAP 0.2.2 keeps rather than
disambiguates and which is why the HTTP report routes key on a feature's list
index instead of its ``id`` (AAP 0.3.1).  None of that is this file's concern.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["NotesPage"]


class NotesPage(BasePage):
    r"""The Notes module's ten elements, in ``NotesP.java`` declaration order.

    A pure declaration: no ``__init__``, no properties written by hand and no
    methods, because ``NotesP.java`` has none beyond the constructor whose job
    :class:`~app.pages.base_page.BasePage` already performs.  Each constant
    below yields a lower-case accessor that resolves the element on every
    access.

    ==========================  ===================  =========================
    Constant                    Accessor             ``NotesP.java``
    ==========================  ===================  =========================
    :attr:`TABINDEX`            ``tabindex``         ``:14-15``
    :attr:`NOTES_MODULE`        ``notes_module``     ``:17-18``
    :attr:`CREATING_NOTES`      ``creating_notes``   ``:20-21``
    :attr:`TAGS_N`              ``tags_n``           ``:23-24``
    :attr:`DESCRIPTION`         ``description``      ``:26-27``
    :attr:`CREATED_MESSAGE`     ``created_message``  ``:29-30``
    :attr:`SAVE_BTN`            ``save_btn``         ``:32-33``
    :attr:`APP_K`               ``app_k``            ``:35-36``
    :attr:`NEW_TABLE`           ``new_table``        ``:38-39``
    :attr:`TODAY_TABLE`         ``today_table``      ``:41-42``
    ==========================  ===================  =========================

    :attr:`PLURAL_LOCATORS` is not overridden: ``NotesP.java`` declares no
    ``List<WebElement>`` field, so all ten accessors are singular.  The
    reference's only plural field is ``SalesP.java:69``.
    """

    # -- Order below is ``NotesP.java``'s declaration order, and it is load- --
    # -- bearing: ``BasePage`` publishes ``LOCATORS`` in this order and      --
    # -- ``tests/test_pages.py`` compares it against the ``@FindBy`` order of --
    # -- the Java class.  Do not sort these alphabetically or by strategy.   --

    #: The "Create and Edit..." anchor offered by the tag autocomplete, clicked
    #: at ``Notes.java:36`` to commit a freshly typed tag.  Ported from
    #: ``NotesP.java:14-15``, whose field is named ``tabindex`` - a name that
    #: describes nothing about this anchor and is kept regardless.
    #:
    #: The spaces around the ``=`` are the source's and are preserved
    #: character-for-character; ``SalesP.java:35`` spells the same idea
    #: ``//li[.='Create and Edit...']``, with no spaces and a different
    #: element, and that page keeps its own spelling.  See the module
    #: docstring.
    TABINDEX = (By.XPATH, "//a[. = 'Create and Edit...']")

    #: The Notes entry in Odoo's main menu, clicked to enter the module
    #: (``Notes.java:24``, ``:66``, ``:72``) and asserted visible at
    #: ``Notes.java:71-73``.  Ported from ``NotesP.java:17-18`` - the one
    #: locator on this page that is not an XPath.
    NOTES_MODULE = (By.PARTIAL_LINK_TEXT, "Notes")

    #: The kanban "Create" button, waited on and clicked at
    #: ``Notes.java:29-30``.  Ported from ``NotesP.java:20-21``; the class
    #: ``o-kanban-button-new`` is **hyphenated** here while :attr:`SAVE_BTN`
    #: below is underscored, and both are verbatim.
    CREATING_NOTES = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new']",
    )

    #: The tag autocomplete input, which receives ``"New Tag"`` followed by
    #: ``Keys.ENTER`` at ``Notes.java:35``.  Ported from ``NotesP.java:23-24``,
    #: whose field is the meaningless abbreviation ``tagsN``.
    TAGS_N = (By.XPATH, "//input[@class='o_input ui-autocomplete-input']")

    #: The rich-text note body, typed into at ``Notes.java:41`` and cleared and
    #: retyped at ``Notes.java:63-64``.  Ported from ``NotesP.java:26-27``.
    DESCRIPTION = (By.XPATH, "//div[@class='note-editable panel-body']")

    #: The confirmation paragraph whose text is compared with ``"Note created"``
    #: at ``Notes.java:51-53``.  Ported from ``NotesP.java:29-30``; the
    #: predicate has **no** spaces around its ``=``, unlike :attr:`TABINDEX`.
    CREATED_MESSAGE = (By.XPATH, "//p[.='Note created']")

    #: The form's save button, waited on and clicked at ``Notes.java:46-47``
    #: and clicked again at ``Notes.java:42``.  Ported from
    #: ``NotesP.java:32-33``; the class ``o_form_button_save`` is
    #: **underscored** while :attr:`CREATING_NOTES` above is hyphenated.
    SAVE_BTN = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
    )

    #: The existing note card titled "BDD Approach Framework with Cucumber",
    #: clicked to open it for editing at ``Notes.java:58``.  Ported from
    #: ``NotesP.java:35-36``, whose field is the meaningless abbreviation
    #: ``appK``.
    APP_K = (By.XPATH, "//span[.='BDD Approach Framework with Cucumber']")

    #: The card grabbed in the New column by the drag-and-drop step
    #: (``Notes.java:79``, ``clickAndHold``).  Ported from
    #: ``NotesP.java:38-39``: record id ``1193`` and child index ``[2]``, both
    #: hard-coded in the source and both preserved rather than parameterized.
    NEW_TABLE = (By.XPATH, "(//div[@data-id='1193']/div)[2]")

    #: The Today column the card is dropped onto (``Notes.java:79``,
    #: ``moveToElement``), whose text is then compared with ``"Today"`` at
    #: ``Notes.java:84-87``.  Ported from ``NotesP.java:41-42``: a **different**
    #: record id, ``1194``, and a **different** child index, ``[1]`` - the
    #: asymmetry with :attr:`NEW_TABLE` is the source's and is deliberate here.
    TODAY_TABLE = (By.XPATH, "(//div[@data-id='1194']/div)[1]")
