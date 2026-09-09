"""Calendar page object - the port of ``CalendarP.java``.

Java anchor
-----------
``src/main/java/com/testinium/pages/CalendarP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  That file is 76 lines: a constructor that calls
``PageFactory.initElements(Driver.getDriver(), this)`` (``CalendarP.java:9-11``)
and then **twenty-one** ``@FindBy`` fields at lines 13-74.  It declares **no
methods beyond the constructor**, so neither does this module: it is a
declarative inventory of twenty-one locator constants and nothing else.

Every accessor and the ordered :attr:`~app.pages.base_page.BasePage.LOCATORS`
inventory arrive from :class:`~app.pages.base_page.BasePage`, whose
``__init_subclass__`` installs one read-only lower-case property per upper-case
constant, re-resolving the element on **every** access.  That is the
``PageFactory`` proxy semantics AAP 0.1.1 goal G5 and AAP 0.3.3 require, and it
is why nothing here defines ``__init__``, a property or a method.  So
``CalendarPage.DAY`` yields the locator tuple and ``page.day`` yields the live
element.

.. warning::

   **:attr:`CalendarPage.MONTH_AND_YEAR_CALENDAR` is whitespace-sensitive and
   must never be reformatted.**  Its XPath (``CalendarP.java:34``) carries a
   leading space and an internal *double* space inside the ``@class`` value::

       //td[@class=' ui-datepicker-days-cell-over  ui-datepicker-current-day ui-datepicker-today']
                    ^ one leading space            ^^ two spaces here

   An XPath ``@class='...'`` comparison is an **exact string match**, not a
   token match, so collapsing either run of whitespace changes which element
   matches - or matches nothing at all.  It is written on a single physical
   line for that reason: no line wrapping, no implicit string concatenation
   (which can swallow a space at a join), and no formatter that normalizes
   whitespace inside string literals.  If an editor rewraps this file, verify
   the literal afterwards - ``tests/test_pages.py`` asserts it character by
   character, including that the value after ``@class='`` begins with a space
   and that ``"  "`` still occurs in it.

   The stake is not cosmetic: ``Calendar.java:54-55`` and ``:110-111`` read
   this element's ``data-month`` and ``data-year`` attributes, so a drifted
   selector fails at *run* time inside four assertions in
   ``features/steps/calendar_steps.py`` rather than at collection time.

Source quirks preserved verbatim (AAP 0.8, *"Preserve, do not tidy"*)
--------------------------------------------------------------------
AAP 0.2.2 keeps the source's inconsistencies outside the deviation inventory,
so each of these is reproduced rather than corrected:

* **Two duplicated selectors, kept as four constants.**  :attr:`GET_NOTE` and
  :attr:`SELECT_NOTE` share one XPath (``CalendarP.java:52`` and ``:73``);
  :attr:`CREATE_BUTTON` and :attr:`EDIT_BUTTON` share another (``:49`` and
  ``:58``).  They are not aliased, merged or collapsed - the Java class
  declares four distinct fields and the parity test compares names, so four
  names exist here with two duplicated values.
* **A misspelling is kept.**  The field at ``CalendarP.java:65`` is
  ``createdModele``, so the constant is :attr:`CREATED_MODELE` and the
  accessor ``created_modele``.  "Modal" is plainly what was meant; renaming it
  would break the name comparison and is not this port's business.
* **Brittle selectors are kept brittle.**  :attr:`DATE_BOX` hard-codes the
  positional index ``[29]`` and :attr:`EDIT_TEXT` and :attr:`TAGS_CHECKBOX`
  hard-code the Odoo-generated ids ``o_field_input_46`` and
  ``o_field_input_59``.  All three are fragile by nature and all three are
  reproduced exactly.
* **Mixed locator strategies are kept mixed.**  :attr:`CALENDAR_MODULE` and
  :attr:`CREATED_NOTE` use ``By.CLASS_NAME`` with a single class name, while
  :attr:`MONTH_AND_YEAR_CALENDAR` matches a multi-class value through XPath;
  :attr:`SUMMARY_BOX` is the XPath ``//input[@name='name']`` rather than
  ``By.NAME``.  Both choices are the source's and are not unified.
* **Odoo vocabulary is kept.**  :attr:`TITLE` is ``//title[.='Meetings -
  Odoo']``, spaces around the hyphen included.  AAP Conflict 8 resolves the
  "Testinium", "Upgenix" and "Odoo" vocabularies as *"preserved exactly where
  they occur"* with *"no renaming"*.  It is also an unusual locator - an XPath
  against ``//title``, an element that is not visible - and that is preserved
  too.

What this module deliberately does not contain
----------------------------------------------
* **No date arithmetic and no ``data-month``/``data-year`` parsing helper.**
  ``Calendar.java:54-55`` and ``:110-111`` parse those attributes inline in
  the step, ``Integer.parseInt`` with ``+ 1`` on the month, and
  ``features/steps/calendar_steps.py`` does the same at the same call sites.
  Hoisting it here would move behaviour out of the module whose per-module
  parity test (AAP 0.4.1) checks for it.
* **No view-switching helper** over :attr:`DAY`, :attr:`WEEK` and
  :attr:`MONTH`, and no click, wait or assertion wrapper of any kind -
  ``CalendarP.java`` has no methods.
* **No waits and no fixed delays.**  ``Calendar.java:15`` builds a **2-second**
  ``WebDriverWait`` and the class holds two ``Thread.sleep(3000)`` calls
  (``Calendar.java:99`` and ``:155``); per AAP 0.4.1 the timeout is supplied at
  each call site from ``app/automation/waits.py`` and the two fixed delays are
  reproduced at their own call sites in ``features/steps/calendar_steps.py`` -
  never here.
* **No twenty-second locator.**  Exactly twenty-one, in Java declaration
  order.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  This module imports two names and no others: :data:`By`, which
``app/automation`` re-exports as the single authorized re-export from the
browser-automation library so that locator construction needs no such import
outside that package, and :class:`BasePage`.  It never names that library -
not even under ``typing.TYPE_CHECKING``, because a guarded import is an
import-statement all the same and would trip the grep-based boundary check the
suite performs - and it imports no ``app.config``, no service, no reporting writer,
no path helper, no web framework and no Gherkin engine.  It never creates,
configures or quits a driver: AAP 0.3.3 gives that lifecycle a single owner in
``features/environment.py``.

Importing this module has no side effects at all: no browser starts, no driver
binary is provisioned, no ``configuration.properties`` is read and no
filesystem path is touched, which is what lets the unit suite import it on a
machine with no browser installed.

Feature context
---------------
``features/Calendar.feature`` carries ``@Calendar`` at line 1 and holds one
Scenario Outline plus three scenarios over the Odoo Calendar module.  Its step
module, ``features/steps/calendar_steps.py``, ports ``Calendar.java``, the
largest step class in the suite at 204 lines, and is this page object's only
consumer.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["CalendarPage"]


class CalendarPage(BasePage):
    """Locator inventory of the Odoo Calendar module.

    The port of ``CalendarP.java``'s twenty-one ``@FindBy`` fields, declared in
    Java source order so that
    :attr:`~app.pages.base_page.BasePage.LOCATORS` - which
    ``BasePage.__init_subclass__`` builds in declaration order - enumerates
    them in the same order as ``CalendarP.java:13-74``.

    Each constant is a ``(By.X, "value")`` pair; the matching lower-case
    accessor is installed by :class:`~app.pages.base_page.BasePage` and
    re-resolves its element on every access.  ``PLURAL_LOCATORS`` is
    deliberately not overridden: ``CalendarP.java`` declares no
    ``List<WebElement>`` field, so every one of the twenty-one resolves to a
    single element.

    .. code-block:: python

        page = CalendarPage()                  # touches nothing
        page.calendar_button.click()           # one find_element, right now
        wait_visible(CalendarPage.DAY, 2)      # the tuple, 2s per Calendar.java:15
    """

    # -- Dashboard, module container and the three view buttons -------------

    #: ``CalendarP.java:13`` - the expected page title, matched as an XPath
    #: against ``//title``.  "Meetings - Odoo" is kept verbatim per AAP
    #: Conflict 8; ``Calendar.java:44-46`` compares the same string against
    #: ``getTitle()``.
    TITLE = (By.XPATH, "//title[.='Meetings - Odoo']")

    #: ``CalendarP.java:16`` - the Calendar entry in the Odoo main menu.
    CALENDAR_BUTTON = (By.PARTIAL_LINK_TEXT, "Calendar")

    #: ``CalendarP.java:19`` - the calendar module container.  Single class
    #: name via ``By.CLASS_NAME``, as declared.
    CALENDAR_MODULE = (By.CLASS_NAME, "o_calendar_container")

    #: ``CalendarP.java:22`` - the Day view button.
    DAY = (By.XPATH, "//button[.='Day']")

    #: ``CalendarP.java:25`` - the Week view button.
    WEEK = (By.XPATH, "//button[.='Week']")

    #: ``CalendarP.java:28`` - the Month view button.
    MONTH = (By.XPATH, "//button[.='Month']")

    # -- Date readouts ------------------------------------------------------

    #: ``CalendarP.java:31`` - the highlighted day in the mini datepicker.
    DAY_CALENDAR = (By.CLASS_NAME, "ui-state-highlight")

    # WHITESPACE-SENSITIVE - see the module docstring's warning before editing.
    # The @class value carries a leading space and a double space between
    # "cell-over" and "current-day"; @class= is an exact string match, so both
    # runs are load-bearing. One physical line, no concatenation, no rewrap.
    #: ``CalendarP.java:34`` - today's datepicker cell, whose ``data-month``
    #: and ``data-year`` attributes ``Calendar.java:54-55`` and ``:110-111``
    #: parse inline.
    MONTH_AND_YEAR_CALENDAR = (By.XPATH, "//td[@class=' ui-datepicker-days-cell-over  ui-datepicker-current-day ui-datepicker-today']")

    #: ``CalendarP.java:37`` - the control-panel breadcrumb showing the date
    #: range currently displayed.
    DATE_ACTUAL = (By.XPATH, "//div[@class='o_control_panel']/ol/li")

    #: ``CalendarP.java:40`` - a specific cell of the month grid, addressed by
    #: the hard-coded positional index ``[29]``.  Preserved as declared.
    DATE_BOX = (By.XPATH, "(//td[@class='fc-widget-content'])[29]")

    # -- Meeting creation dialog --------------------------------------------

    #: ``CalendarP.java:43`` - the new-meeting modal header.
    CREATE_NOTE = (By.XPATH, "//div[@class='modal-header']")

    #: ``CalendarP.java:46`` - the meeting summary input, located by XPath on
    #: ``@name`` rather than by ``By.NAME``.  Strategy kept as declared.
    SUMMARY_BOX = (By.XPATH, "//input[@name='name']")

    #: ``CalendarP.java:49`` - the modal's primary button, used to create.
    #: Shares its selector with :attr:`EDIT_BUTTON`; both names are kept.
    CREATE_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``CalendarP.java:52`` - the created meeting's name field.  Shares its
    #: selector with :attr:`SELECT_NOTE`; both names are kept.
    GET_NOTE = (By.XPATH, "//div[@class='o_field_name o_field_type_char']")

    #: ``CalendarP.java:55`` - the created meeting's name, by single class
    #: name.  Preserved alongside the XPath forms above rather than unified.
    CREATED_NOTE = (By.CLASS_NAME, "o_field_name")

    # -- Editing and saving -------------------------------------------------

    #: ``CalendarP.java:58`` - the modal's primary button, used to edit.
    #: Same selector as :attr:`CREATE_BUTTON`, as the source declares it.
    EDIT_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``CalendarP.java:61`` - the editable summary input, by the
    #: Odoo-generated id ``o_field_input_46``.  Brittle by nature, preserved.
    EDIT_TEXT = (By.ID, "o_field_input_46")

    #: ``CalendarP.java:64`` - the open modal's body.  The source field is
    #: ``createdModele``; the misspelling is kept, so this is ``CREATED_MODELE``
    #: with the accessor ``created_modele`` and not "created_modal".
    CREATED_MODELE = (By.XPATH, "//div[@class='modal-content']")

    #: ``CalendarP.java:67`` - the tags checkbox, by the Odoo-generated id
    #: ``o_field_input_59``.  ``Calendar.java:194`` calls ``isSelected()`` on
    #: it and discards the result, which the step module reproduces.
    TAGS_CHECKBOX = (By.ID, "o_field_input_59")

    #: ``CalendarP.java:70`` - the Save control, matched on its span text.
    SAVE_BUTTON = (By.XPATH, "//span[.='Save']")

    #: ``CalendarP.java:73`` - the meeting to open from the calendar grid.
    #: Same selector as :attr:`GET_NOTE`; the source declares both fields and
    #: both are kept.
    SELECT_NOTE = (By.XPATH, "//div[@class='o_field_name o_field_type_char']")
