"""Calendar page object - the port of ``CalendarP.java``.

The twenty-one ``@FindBy`` fields of ``CalendarP.java:13-74``, at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in
Java declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order.  The Java class declares no
method beyond its constructor, so this module declares constants only.
``features/steps/calendar_steps.py`` is its only consumer.

.. warning::

   **:attr:`CalendarPage.MONTH_AND_YEAR_CALENDAR` is whitespace-sensitive and
   must never be reformatted.**  Its XPath (``CalendarP.java:34``) carries a
   leading space and an internal *double* space inside the ``@class`` value,
   and ``@class='...'`` is an exact string match, not a token match, so
   collapsing either run changes which element matches - or matches nothing. It
   is written on one physical line for that reason: no wrapping, no implicit
   concatenation. ``Calendar.java:54-55`` and ``:110-111`` read this element's
   ``data-month`` and ``data-year``, so a drifted selector fails at run time.

Source quirks preserved verbatim (AAP 0.2.2, AAP 0.8's *"Preserve, do not
tidy"*): two selectors are declared twice under different names -
:attr:`GET_NOTE`/:attr:`SELECT_NOTE` (``:52``, ``:73``) and
:attr:`CREATE_BUTTON`/:attr:`EDIT_BUTTON` (``:49``, ``:58``) - and are kept as
four constants, neither aliased nor merged; ``createdModele`` (``:65``) keeps
its misspelling as :attr:`CREATED_MODELE`; :attr:`DATE_BOX` keeps the
positional index ``[29]`` and :attr:`EDIT_TEXT` and :attr:`TAGS_CHECKBOX` the
generated ids ``o_field_input_46`` and ``o_field_input_59``; strategies stay
mixed, ``By.CLASS_NAME`` beside multi-class XPaths and :attr:`SUMMARY_BOX` an
XPath on ``@name``; and :attr:`TITLE` keeps Odoo's own ``Meetings - Odoo``
wording (AAP Conflict 8).

Per AAP 0.4.2 the imports are :data:`By` and :class:`BasePage` alone - no
browser library even under ``typing.TYPE_CHECKING``, no configuration. The
2-second wait (``Calendar.java:15``) and the two 3-second delays (``:99``,
``:155``) stay at their step call sites per AAP 0.4.1, and importing this
module has no side effects: it never creates or quits a driver (AAP 0.3.3).
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

    #: ``CalendarP.java:13``'s ``title``, ``Meetings - Odoo`` kept verbatim.
    TITLE = (By.XPATH, "//title[.='Meetings - Odoo']")

    #: ``CalendarP.java:16``'s ``calendarButton``.
    CALENDAR_BUTTON = (By.PARTIAL_LINK_TEXT, "Calendar")

    #: ``CalendarP.java:19``'s ``calendarModule``, by class name as declared.
    CALENDAR_MODULE = (By.CLASS_NAME, "o_calendar_container")

    #: ``CalendarP.java:22``'s ``day``.
    DAY = (By.XPATH, "//button[.='Day']")

    #: ``CalendarP.java:25``'s ``week``.
    WEEK = (By.XPATH, "//button[.='Week']")

    #: ``CalendarP.java:28``'s ``month``.
    MONTH = (By.XPATH, "//button[.='Month']")

    #: ``CalendarP.java:31``'s ``dayCalendar``.
    DAY_CALENDAR = (By.CLASS_NAME, "ui-state-highlight")

    # WHITESPACE-SENSITIVE - see the module docstring's warning before editing.
    # The @class value carries a leading space and a double space between
    # "cell-over" and "current-day"; @class= is an exact string match, so both
    # runs are load-bearing. One physical line, no concatenation, no rewrap.
    #: ``CalendarP.java:34`` - today's datepicker cell, whose ``data-month``
    #: and ``data-year`` attributes ``Calendar.java:54-55`` and ``:110-111``
    #: parse inline.
    MONTH_AND_YEAR_CALENDAR = (By.XPATH, "//td[@class=' ui-datepicker-days-cell-over  ui-datepicker-current-day ui-datepicker-today']")

    #: ``CalendarP.java:37``'s ``dateActual``.
    DATE_ACTUAL = (By.XPATH, "//div[@class='o_control_panel']/ol/li")

    #: ``CalendarP.java:40``'s ``dateBox``, positional index ``[29]`` as
    #: declared.
    DATE_BOX = (By.XPATH, "(//td[@class='fc-widget-content'])[29]")

    #: ``CalendarP.java:43``'s ``createNote``.
    CREATE_NOTE = (By.XPATH, "//div[@class='modal-header']")

    #: ``CalendarP.java:46``'s ``summaryBox``, an XPath on ``@name`` rather
    #: than ``By.NAME``, as declared.
    SUMMARY_BOX = (By.XPATH, "//input[@name='name']")

    #: ``CalendarP.java:49`` - the modal's primary button, used to create.
    #: Shares its selector with :attr:`EDIT_BUTTON`; both names are kept.
    CREATE_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``CalendarP.java:52`` - the created meeting's name field.  Shares its
    #: selector with :attr:`SELECT_NOTE`; both names are kept.
    GET_NOTE = (By.XPATH, "//div[@class='o_field_name o_field_type_char']")

    #: ``CalendarP.java:55``'s ``createdNote``, by class name as declared.
    CREATED_NOTE = (By.CLASS_NAME, "o_field_name")

    #: ``CalendarP.java:58``'s ``editButton``, :attr:`CREATE_BUTTON`'s selector
    #: declared a second time; both names are kept.
    EDIT_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``CalendarP.java:61``'s ``editText``, the generated id
    #: ``o_field_input_46``.
    EDIT_TEXT = (By.ID, "o_field_input_46")

    #: ``CalendarP.java:64`` - the open modal's body.  The source field is
    #: ``createdModele``; the misspelling is kept, so this is ``CREATED_MODELE``
    #: with the accessor ``created_modele`` and not "created_modal".
    CREATED_MODELE = (By.XPATH, "//div[@class='modal-content']")

    #: ``CalendarP.java:67`` - the tags checkbox, by the Odoo-generated id
    #: ``o_field_input_59``.  ``Calendar.java:194`` calls ``isSelected()`` on
    #: it and discards the result, which the step module reproduces.
    TAGS_CHECKBOX = (By.ID, "o_field_input_59")

    #: ``CalendarP.java:70``'s ``saveButton``.
    SAVE_BUTTON = (By.XPATH, "//span[.='Save']")

    #: ``CalendarP.java:73`` - the meeting to open from the calendar grid.
    #: Same selector as :attr:`GET_NOTE`; the source declares both fields and
    #: both are kept.
    SELECT_NOTE = (By.XPATH, "//div[@class='o_field_name o_field_type_char']")
