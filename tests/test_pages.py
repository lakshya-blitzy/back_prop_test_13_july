r"""Tests for ``app/pages`` - the ten page objects and all 131 Java locators.

What this module is the gate for
--------------------------------
AAP 0.4.1 gives each of the ten page modules one job - reproduce the
``@FindBy`` inventory of the Java class it ports - and AAP 0.8 fixes how:
*"Preserve, do not tidy."*  A locator is therefore not merely "a selector that
works": it is a specific strategy and a specific string of bytes, in a specific
position in its class, under a specific name, duplicates and dead declarations
included.  This module is where that claim is measured.

The authority is the Java source, not the Python constants
----------------------------------------------------------
The expected inventory below was built by reading the ten reference classes at
``/opt/reference/Upgenix-QA/src/main/java/com/testinium/pages/*.java``, pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  Every row cites the ``@FindBy`` line it came from,
and :data:`JAVA_PAGES` is **committed here as data** rather than derived from
``app/pages`` at run time.  That distinction is the whole value of the module:
a test that read the Python constants and compared them with themselves would
pass just as happily against a wrong selector, a dropped field or a re-ordered
class body.

Measured from that source, and asserted below:

=================  ============  ========  =====================================
Java class         Python class  @FindBy   Notes
=================  ============  ========  =====================================
``CalendarP``      CalendarPage        21  two duplicate-selector pairs
``ContactsP``      ContactsPage        16
``CrmP``           CrmPage             28  four duplicate-selector pairs
``EmployeeP``      EmployeePage        15  plus ``login()``, the only method
``InventoryP``     InventoryPage        8
``LoginP``         LoginPage            7  ``bulletPass`` duplicates ``inputPassword``
``LogOutP``        LogOutPage           3
``NotesP``         NotesPage           10
``SalesP``         SalesPage           20  ``allCustomers`` is the only ``List<WebElement>``
``SessionP``       SessionPage          3
=================  ============  ========  =====================================

131 fields in total, over five strategies - ``xpath`` 96, ``partial link
text`` 12, ``name`` 8, ``id`` 8, ``class name`` 7 - and 112 *distinct*
``(strategy, selector)`` pairs, because thirteen selectors are declared more
than once.  Not one of those repetitions may be collapsed: seven of them are
two names for one selector *within a single class*, which a well-meaning
de-duplication would silently merge, changing which field a step drives.

How each expectation is kept honest
-----------------------------------
* **The table checks itself first.**  Per-class counts, the total, the strategy
  histogram and the ascending ``@FindBy`` line numbers are asserted against
  figures declared separately from the rows, so a transcription slip in the
  table fails here rather than being asserted as truth against the code.
* **Every locator is resolved, not just read.**  Each of the 131 constants is
  driven through its accessor against the ordered ``StubDriver`` recorder, so
  the assertion covers the strategy and selector *as the driver receives
  them* - which is what a scenario depends on.
* **Every quirk has its own named test.**  A leading space, an internal double
  space, an unescaped ``&``, literal double quotes inside an XPath, an
  absolute DOM path: each is stated as a separate expectation with the Java
  line that justifies it, so a "cleanup" that normalises one of them fails
  with a message that says which quirk was lost and why it existed.
* **Source guarantees are checked through the syntax tree**, and each helper is
  paired with a negative test that feeds it source violating the guarantee.
  No production file is modified by this suite, temporarily or otherwise.

Boundaries of this module
-------------------------
The lazy-resolution mechanism itself - inert construction, no caching, naming,
``LOCATORS`` immutability, inheritance, the driver seam - belongs to
``tests/test_base_page.py`` and is not re-asserted here.  Behavioural parity
for the step definitions that drive these pages belongs to the ten
``tests/test_steps_<area>.py`` modules; a correct locator inventory is a
precondition of that parity, never a substitute for it.

selenium is never imported: strategies are compared against
``app.automation.By``, the re-export AAP 0.4.2 authorises for exactly this
purpose, whose members are plain strings (``By.XPATH == "xpath"``).  Nothing
here launches a browser, opens a socket, sleeps or writes into ``target/``.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NamedTuple

import pytest

import app.pages as pages
from app import config
from app.automation import By
from app.pages.base_page import BasePage

# ``tests/`` is on ``sys.path`` under pytest's prepend import mode (there is
# deliberately no ``tests/__init__.py``), so the suite's shared constants come
# from the conftest that owns them rather than being restated here.
from conftest import ELEMENT_PREFIX, REPO_ROOT, StubDriver

# =========================================================================== #
# Strategy names
#
# Spelled as module constants so the table below reads like the Java it came
# from, and asserted against ``app.automation.By`` in the first test: the Java
# annotation attribute maps onto the Selenium strategy *value*, and these five
# are the only ones the reference uses.
#   id -> "id"   name -> "name"   xpath -> "xpath"
#   className -> "class name"     partialLinkText -> "partial link text"
# =========================================================================== #

ID: Final[str] = "id"
NAME: Final[str] = "name"
XPATH: Final[str] = "xpath"
CLASS_NAME: Final[str] = "class name"
PARTIAL_LINK_TEXT: Final[str] = "partial link text"

#: The five strategy names above, paired with the ``By`` member each one is.
AUTHORIZED_STRATEGIES: Final[tuple[tuple[str, str], ...]] = (
    (ID, By.ID),
    (NAME, By.NAME),
    (XPATH, By.XPATH),
    (CLASS_NAME, By.CLASS_NAME),
    (PARTIAL_LINK_TEXT, By.PARTIAL_LINK_TEXT),
)

#: Strategies ``By`` defines that the reference never uses.  Listed so that
#: "five strategies" is asserted as a closed set rather than implied.
UNUSED_STRATEGIES: Final[tuple[str, ...]] = (
    By.LINK_TEXT,
    By.TAG_NAME,
    By.CSS_SELECTOR,
)


class JavaField(NamedTuple):
    """One ``@FindBy`` field of a reference page class.

    :param java_line: Line of the ``@FindBy`` annotation in its ``.java``
        file, so every expectation below cites its own source.
    :param java_field: The Java field name, in ``camelCase``.
    :param constant: The Python constant it becomes, in ``UPPER_SNAKE``.
    :param strategy: The Selenium strategy value the annotation attribute maps
        onto.
    :param selector: The selector, byte for byte as the Java string literal
        denotes it - escapes resolved, so ``\"`` is a double-quote character.
    :param plural: ``True`` for the one field declared ``List<WebElement>``.
    """

    java_line: int
    java_field: str
    constant: str
    strategy: str
    selector: str
    plural: bool = False


class JavaPage(NamedTuple):
    """One reference page class and the Python class that ports it.

    :param export: Name in ``app.pages.__all__``.
    :param module: Module under ``app/pages/`` that defines it, without the
        ``.py`` suffix.
    :param java_class: The Java class ported.
    :param java_lines: The ``@FindBy`` span in that file, as a citation.
    :param fields: Its fields, in Java declaration order.
    """

    export: str
    module: str
    java_class: str
    java_lines: str
    fields: tuple[JavaField, ...]


# =========================================================================== #
# The Java-derived inventory
#
# Read out of the ten reference classes, in barrel order (which is module-name
# order, and therefore also ``app.pages.__all__``'s order).  Within a class the
# rows are in ``@FindBy`` declaration order, because that order is part of the
# contract: ``BasePage.LOCATORS`` is built from the class body and nothing
# sorts it, so a re-ordered page body is a real difference and is asserted as
# one.
#
# Two rows carry selectors that Java escapes and Python does not need to:
# ``EmployeeP.java:56`` writes ``"//*[@id=\"o_field_input_678\"]"`` and
# ``SalesP.java:74`` writes ``"//div[@class=\"oe_kanban_details\"]//span"``.
# The selectors *contain literal double quotes*; they are written here in
# single-quoted Python strings so that the bytes survive unaltered, and a
# backslash in either would be a different selector.
# =========================================================================== #

JAVA_PAGES: Final[tuple[JavaPage, ...]] = (
    JavaPage(
        export="CalendarPage",
        module="calendar_page",
        java_class="CalendarP",
        java_lines="CalendarP.java:13-74",
        fields=(
            JavaField(13, "title", "TITLE", XPATH, "//title[.='Meetings - Odoo']"),
            JavaField(16, "calendarButton", "CALENDAR_BUTTON", PARTIAL_LINK_TEXT, "Calendar"),
            JavaField(
                19,
                "calendarModule",
                "CALENDAR_MODULE",
                CLASS_NAME,
                "o_calendar_container",
            ),
            JavaField(22, "day", "DAY", XPATH, "//button[.='Day']"),
            JavaField(25, "week", "WEEK", XPATH, "//button[.='Week']"),
            JavaField(28, "month", "MONTH", XPATH, "//button[.='Month']"),
            JavaField(31, "dayCalendar", "DAY_CALENDAR", CLASS_NAME, "ui-state-highlight"),
            JavaField(
                34,
                "monthAndYearCalendar",
                "MONTH_AND_YEAR_CALENDAR",
                XPATH,
                "//td[@class=' ui-datepicker-days-cell-over  ui-datepicker-current-day ui-datepicker-today']",
            ),
            JavaField(
                37,
                "dateActual",
                "DATE_ACTUAL",
                XPATH,
                "//div[@class='o_control_panel']/ol/li",
            ),
            JavaField(
                40,
                "dateBox",
                "DATE_BOX",
                XPATH,
                "(//td[@class='fc-widget-content'])[29]",
            ),
            JavaField(43, "createNote", "CREATE_NOTE", XPATH, "//div[@class='modal-header']"),
            JavaField(46, "summaryBox", "SUMMARY_BOX", XPATH, "//input[@name='name']"),
            JavaField(
                49,
                "createButton",
                "CREATE_BUTTON",
                XPATH,
                "//button[@class='btn btn-sm btn-primary']",
            ),
            JavaField(
                52,
                "getNote",
                "GET_NOTE",
                XPATH,
                "//div[@class='o_field_name o_field_type_char']",
            ),
            JavaField(55, "createdNote", "CREATED_NOTE", CLASS_NAME, "o_field_name"),
            JavaField(
                58,
                "editButton",
                "EDIT_BUTTON",
                XPATH,
                "//button[@class='btn btn-sm btn-primary']",
            ),
            JavaField(61, "editText", "EDIT_TEXT", ID, "o_field_input_46"),
            JavaField(
                64,
                "createdModele",
                "CREATED_MODELE",
                XPATH,
                "//div[@class='modal-content']",
            ),
            JavaField(67, "tagsCheckbox", "TAGS_CHECKBOX", ID, "o_field_input_59"),
            JavaField(70, "saveButton", "SAVE_BUTTON", XPATH, "//span[.='Save']"),
            JavaField(
                73,
                "selectNote",
                "SELECT_NOTE",
                XPATH,
                "//div[@class='o_field_name o_field_type_char']",
            ),
        ),
    ),
    JavaPage(
        export="ContactsPage",
        module="contacts_page",
        java_class="ContactsP",
        java_lines="ContactsP.java:14-61",
        fields=(
            JavaField(14, "contactModule", "CONTACT_MODULE", PARTIAL_LINK_TEXT, "Contacts"),
            JavaField(17, "createContact", "CREATE_CONTACT", XPATH, "//button[@accesskey='c']"),
            JavaField(20, "callList", "CALL_LIST", XPATH, "//button[@accesskey='l']"),
            JavaField(23, "nameInput", "NAME_INPUT", NAME, "name"),
            JavaField(26, "streetInput", "STREET_INPUT", NAME, "street"),
            JavaField(29, "phoneNoInput", "PHONE_NO_INPUT", NAME, "phone"),
            JavaField(32, "emailInput", "EMAIL_INPUT", NAME, "email"),
            JavaField(35, "okBtn", "OK_BTN", XPATH, "//span[.='Ok']"),
            JavaField(
                38,
                "newContact",
                "NEW_CONTACT",
                XPATH,
                "(//div[@class='o_checkbox']/input)[12]",
            ),
            JavaField(
                41,
                "actionInput",
                "ACTION_INPUT",
                XPATH,
                "(//div[@class='o_cp_sidebar']/div/div)[2]",
            ),
            JavaField(44, "deleteInput", "DELETE_INPUT", XPATH, "//a[@data-index='3']"),
            JavaField(
                47,
                "firstUser",
                "FIRST_USER",
                XPATH,
                "(//div[@class='o_kanban_view o_res_partner_kanban o_kanban_ungrouped']/div)[1]",
            ),
            JavaField(50, "editTitle", "EDIT_TITLE", XPATH, "//div[@class='oe_title']"),
            JavaField(
                53,
                "editBtn",
                "EDIT_BTN",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o_form_button_edit']",
            ),
            JavaField(
                56,
                "printInput",
                "PRINT_INPUT",
                XPATH,
                "//div[@class='btn-group o_dropdown open']",
            ),
            JavaField(
                60,
                "duePayment",
                "DUE_PAYMENT",
                XPATH,
                "(//div[@class='btn-group o_dropdown open']/button)",
            ),
        ),
    ),
    JavaPage(
        export="CrmPage",
        module="crm_page",
        java_class="CrmP",
        java_lines="CrmP.java:13-95",
        fields=(
            JavaField(13, "crmLink", "CRM_LINK", PARTIAL_LINK_TEXT, "CRM"),
            JavaField(16, "createButton", "CREATE_BUTTON", XPATH, "//button[@accesskey='c']"),
            JavaField(19, "opportunityTitle", "OPPORTUNITY_TITLE", NAME, "name"),
            JavaField(
                22,
                "customer",
                "CUSTOMER",
                XPATH,
                "//table[@class='o_group o_inner_group o_group_col_6']//div//div//input",
            ),
            JavaField(25, "customerId", "CUSTOMER_ID", XPATH, "//a[.='&CC']"),
            JavaField(
                28,
                "expectedRevenue",
                "EXPECTED_REVENUE",
                XPATH,
                "//div[@class='o_row']//input",
            ),
            JavaField(
                31,
                "priority",
                "PRIORITY",
                XPATH,
                "//table[@class='o_group o_inner_group o_group_col_6']//tr[4]//a[3]",
            ),
            JavaField(
                34,
                "createPipeline",
                "CREATE_PIPELINE",
                XPATH,
                "//button[@name='close_dialog']",
            ),
            JavaField(
                37,
                "findTitleTest",
                "FIND_TITLE_TEST",
                XPATH,
                "//div[@data-id='1']/div[2]//strong//span",
            ),
            JavaField(40, "totalPrice", "TOTAL_PRICE", XPATH, "//div[@data-id='1']//b"),
            JavaField(
                43,
                "buttonPipeline",
                "BUTTON_PIPELINE",
                XPATH,
                "//div[@data-id='1']/div[2]",
            ),
            JavaField(46, "editButton", "EDIT_BUTTON", XPATH, "//button[@accesskey='a']"),
            JavaField(
                49,
                "opportunityTitleEdit",
                "OPPORTUNITY_TITLE_EDIT",
                XPATH,
                "//input[@name='name']",
            ),
            JavaField(
                52,
                "expectedRevenueEdit",
                "EXPECTED_REVENUE_EDIT",
                XPATH,
                "//input[@id='o_field_input_125']",
            ),
            JavaField(
                55,
                "probabilityEdit",
                "PROBABILITY_EDIT",
                XPATH,
                "//input[@id='o_field_input_127']",
            ),
            JavaField(58, "saveEdit", "SAVE_EDIT", XPATH, "//button[@accesskey='s']"),
            JavaField(
                61,
                "pipelineSideButton",
                "PIPELINE_SIDE_BUTTON",
                XPATH,
                "//a[@href='/web#menu_id=274&action=365']/span",
            ),
            JavaField(
                64,
                "progressPipeline",
                "PROGRESS_PIPELINE",
                XPATH,
                "//div[@data-id='1']/div[2]",
            ),
            JavaField(
                67,
                "progressPipeline2",
                "PROGRESS_PIPELINE2",
                XPATH,
                "//div[@data-id='2']/div[2]",
            ),
            JavaField(
                70,
                "testVerify",
                "TEST_VERIFY",
                XPATH,
                "//div[@data-id='2']//div[2]//strong//span",
            ),
            JavaField(
                73,
                "customerSideButton",
                "CUSTOMER_SIDE_BUTTON",
                XPATH,
                "//a[@href='/web#menu_id=272&action=48']",
            ),
            JavaField(
                76,
                "createCustomer",
                "CREATE_CUSTOMER",
                XPATH,
                "//button[@accesskey='c']",
            ),
            JavaField(79, "inputName", "INPUT_NAME", XPATH, "//input[@name='name']"),
            JavaField(
                82,
                "createCustomerButton",
                "CREATE_CUSTOMER_BUTTON",
                XPATH,
                "//button[@name='close_dialog']",
            ),
            JavaField(
                85,
                "searchingText",
                "SEARCHING_TEXT",
                XPATH,
                "//input[@class='o_searchview_input']",
            ),
            JavaField(88, "nameCustomer", "NAME_CUSTOMER", XPATH, "//span[.='&CC']"),
            JavaField(
                91,
                "duePaymentButton",
                "DUE_PAYMENT_BUTTON",
                XPATH,
                "//a[@data-section='print']",
            ),
            JavaField(
                94,
                "printButton",
                "PRINT_BUTTON",
                XPATH,
                "/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/div[1]/button",
            ),
        ),
    ),
    JavaPage(
        export="EmployeePage",
        module="employee_page",
        java_class="EmployeeP",
        java_lines="EmployeeP.java:14-57",
        fields=(
            JavaField(14, "inputLogin", "INPUT_LOGIN", ID, "login"),
            JavaField(17, "inputPass", "INPUT_PASS", ID, "password"),
            JavaField(20, "loginButton", "LOGIN_BUTTON", XPATH, "//button[.='Log in']"),
            JavaField(23, "emplStage", "EMPL_STAGE", PARTIAL_LINK_TEXT, "Employees"),
            JavaField(26, "badgesBtn", "BADGES_BTN", PARTIAL_LINK_TEXT, "Badges"),
            JavaField(29, "challengesBtn", "CHALLENGES_BTN", PARTIAL_LINK_TEXT, "Challenges"),
            JavaField(
                32,
                "goalsHistoryBtn",
                "GOALS_HISTORY_BTN",
                PARTIAL_LINK_TEXT,
                "Goals History",
            ),
            JavaField(
                35,
                "departmentsBtn",
                "DEPARTMENTS_BTN",
                PARTIAL_LINK_TEXT,
                "Departments",
            ),
            JavaField(
                38,
                "createBtn",
                "CREATE_BTN",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
            ),
            JavaField(
                41,
                "employeesName",
                "EMPLOYEES_NAME",
                XPATH,
                "//input[@class='o_field_char o_field_widget o_input o_required_modifier']",
            ),
            JavaField(
                44,
                "savedMessage",
                "SAVED_MESSAGE",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o_form_button_save']",
            ),
            JavaField(
                47,
                "createdMessage",
                "CREATED_MESSAGE",
                XPATH,
                "//p[.='Employee created']",
            ),
            JavaField(
                50,
                "chooseEmployee",
                "CHOOSE_EMPLOYEE",
                XPATH,
                "//html/body/div[1]/div[2]/div[2]/div/div/div/div[1]",
            ),
            JavaField(
                53,
                "editEmployee",
                "EDIT_EMPLOYEE",
                XPATH,
                "//html/body/div[1]/div[2]/div[1]/div[2]/div[1]/div/div[1]/button[1]",
            ),
            JavaField(56, "nameEdit", "NAME_EDIT", XPATH, '//*[@id="o_field_input_678"]'),
        ),
    ),
    JavaPage(
        export="InventoryPage",
        module="inventory_page",
        java_class="InventoryP",
        java_lines="InventoryP.java:14-36",
        fields=(
            JavaField(
                14,
                "inventoryModule",
                "INVENTORY_MODULE",
                PARTIAL_LINK_TEXT,
                "Inventory",
            ),
            JavaField(17, "products", "PRODUCTS", PARTIAL_LINK_TEXT, "Products"),
            JavaField(20, "createBtn", "CREATE_BTN", CLASS_NAME, "o-kanban-button-new"),
            JavaField(
                23,
                "saveBtn",
                "SAVE_BTN",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o_form_button_save']",
            ),
            JavaField(26, "fieldError", "FIELD_ERROR", CLASS_NAME, "o_notification_manager"),
            JavaField(29, "productName", "PRODUCT_NAME", ID, "o_field_input_479"),
            JavaField(32, "productsList", "PRODUCTS_LIST", XPATH, "//span[.='EY']"),
            JavaField(
                35,
                "createdProduct",
                "CREATED_PRODUCT",
                XPATH,
                "//span[@class='o_field_char o_field_widget o_required_modifier']",
            ),
        ),
    ),
    JavaPage(
        export="LoginPage",
        module="login_page",
        java_class="LoginP",
        java_lines="LoginP.java:13-32",
        fields=(
            JavaField(13, "inputEmail", "INPUT_EMAIL", NAME, "login"),
            JavaField(16, "inputPassword", "INPUT_PASSWORD", NAME, "password"),
            JavaField(19, "button", "BUTTON", XPATH, "//button[.='Log in']"),
            JavaField(22, "resetPass", "RESET_PASS", XPATH, "//a[.='Reset Password']"),
            JavaField(25, "dashboard", "DASHBOARD", ID, "oe_main_menu_navbar"),
            JavaField(28, "alertErrorMessage", "ALERT_ERROR_MESSAGE", CLASS_NAME, "alert"),
            JavaField(31, "bulletPass", "BULLET_PASS", NAME, "password"),
        ),
    ),
    JavaPage(
        export="LogOutPage",
        module="logout_page",
        java_class="LogOutP",
        java_lines="LogOutP.java:14-21",
        fields=(
            JavaField(14, "popUpButton", "POP_UP_BUTTON", CLASS_NAME, "o_user_menu"),
            JavaField(17, "logOutButton", "LOG_OUT_BUTTON", XPATH, "//a[.='Log out']"),
            JavaField(
                20,
                "warningMess",
                "WARNING_MESS",
                XPATH,
                "//div[@class= 'o_dialog_warning modal-body']",
            ),
        ),
    ),
    JavaPage(
        export="NotesPage",
        module="notes_page",
        java_class="NotesP",
        java_lines="NotesP.java:14-42",
        fields=(
            JavaField(14, "tabindex", "TABINDEX", XPATH, "//a[. = 'Create and Edit...']"),
            JavaField(17, "notesModule", "NOTES_MODULE", PARTIAL_LINK_TEXT, "Notes"),
            JavaField(
                20,
                "creatingNotes",
                "CREATING_NOTES",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o-kanban-button-new']",
            ),
            JavaField(
                23,
                "tagsN",
                "TAGS_N",
                XPATH,
                "//input[@class='o_input ui-autocomplete-input']",
            ),
            JavaField(
                26,
                "description",
                "DESCRIPTION",
                XPATH,
                "//div[@class='note-editable panel-body']",
            ),
            JavaField(29, "createdMessage", "CREATED_MESSAGE", XPATH, "//p[.='Note created']"),
            JavaField(
                32,
                "saveBtn",
                "SAVE_BTN",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o_form_button_save']",
            ),
            JavaField(
                35,
                "appK",
                "APP_K",
                XPATH,
                "//span[.='BDD Approach Framework with Cucumber']",
            ),
            JavaField(38, "newTable", "NEW_TABLE", XPATH, "(//div[@data-id='1193']/div)[2]"),
            JavaField(
                41,
                "todayTable",
                "TODAY_TABLE",
                XPATH,
                "(//div[@data-id='1194']/div)[1]",
            ),
        ),
    ),
    JavaPage(
        export="SalesPage",
        module="sales_page",
        java_class="SalesP",
        java_lines="SalesP.java:17-75",
        fields=(
            JavaField(17, "salesPartial", "SALES_PARTIAL", PARTIAL_LINK_TEXT, "Sales"),
            JavaField(
                20,
                "customersButton",
                "CUSTOMERS_BUTTON",
                XPATH,
                "//a[@href='/web#menu_id=447&action=48']/span",
            ),
            JavaField(
                23,
                "createButton",
                "CREATE_BUTTON",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
            ),
            JavaField(
                26,
                "customerName",
                "CUSTOMER_NAME",
                XPATH,
                "//input[@id='o_field_input_470']",
            ),
            JavaField(29, "address", "ADDRESS", XPATH, "//input[@id='o_field_input_474']"),
            JavaField(
                32,
                "stateOptions",
                "STATE_OPTIONS",
                XPATH,
                "//input[@id='o_field_input_477']",
            ),
            JavaField(
                35,
                "createAndEditState",
                "CREATE_AND_EDIT_STATE",
                XPATH,
                "//li[.='Create and Edit...']",
            ),
            JavaField(38, "stateName", "STATE_NAME", XPATH, "//input[@id='o_field_input_516']"),
            JavaField(41, "stateCode", "STATE_CODE", XPATH, "//input[@id='o_field_input_517']"),
            JavaField(
                44,
                "countryStateButton",
                "COUNTRY_STATE_BUTTON",
                XPATH,
                "//input[@id='o_field_input_518']",
            ),
            JavaField(
                47,
                "countrySelection",
                "COUNTRY_SELECTION",
                XPATH,
                "//li[@id='ui-id-30']/a",
            ),
            JavaField(
                50,
                "saveButton",
                "SAVE_BUTTON",
                XPATH,
                "//button[@class='btn btn-sm btn-primary']/span",
            ),
            JavaField(
                53,
                "createCustomer",
                "CREATE_CUSTOMER",
                XPATH,
                "//button[@class='btn btn-primary btn-sm o_form_button_save']",
            ),
            JavaField(
                56,
                "searchBar",
                "SEARCH_BAR",
                XPATH,
                "//div[@class='o_searchview']/input",
            ),
            JavaField(
                59,
                "nameCheck",
                "NAME_CHECK",
                XPATH,
                "//strong[@class='o_kanban_record_title oe_partner_heading']/span",
            ),
            JavaField(
                62,
                "warningButton",
                "WARNING_BUTTON",
                XPATH,
                "//button[@class='btn btn-sm btn-primary']",
            ),
            JavaField(
                65,
                "warning",
                "WARNING",
                XPATH,
                "//div[@class='o_notification_manager']",
            ),
            JavaField(
                68,
                "allCustomers",
                "ALL_CUSTOMERS",
                XPATH,
                "//div[@class='oe_kanban_global_click o_res_partner_kanban o_kanban_record']",
                plural=True,
            ),
            JavaField(71, "link", "LINK", XPATH, "//a[@href='/web#menu_id=447&action=48']"),
            JavaField(
                74,
                "details",
                "DETAILS",
                XPATH,
                '//div[@class="oe_kanban_details"]//span',
            ),
        ),
    ),
    JavaPage(
        export="SessionPage",
        module="session_page",
        java_class="SessionP",
        java_lines="SessionP.java:14-21",
        fields=(
            JavaField(14, "inputLogin", "INPUT_LOGIN", ID, "login"),
            JavaField(17, "inputPass", "INPUT_PASS", ID, "password"),
            JavaField(20, "loginButton", "LOGIN_BUTTON", XPATH, "//button[.='Log in']"),
        ),
    ),
)

# =========================================================================== #
# Figures declared independently of the rows above
#
# These are the measurements the review finding names, and they are stated
# separately on purpose: asserting them against the table catches a
# transcription error in the table itself, which would otherwise be asserted
# against the implementation as though it were authority.
# =========================================================================== #

#: ``@FindBy`` count per Java class - the 21/16/28/15/8/7/3/10/20/3 of the
#: finding, keyed by the class each figure belongs to.
EXPECTED_COUNTS: Final[dict[str, int]] = {
    "CalendarP": 21,
    "ContactsP": 16,
    "CrmP": 28,
    "EmployeeP": 15,
    "InventoryP": 8,
    "LoginP": 7,
    "LogOutP": 3,
    "NotesP": 10,
    "SalesP": 20,
    "SessionP": 3,
}

#: Total ``@FindBy`` fields across the ten classes.
EXPECTED_TOTAL: Final[int] = 131

#: How the 131 divide by strategy.
EXPECTED_STRATEGY_HISTOGRAM: Final[dict[str, int]] = {
    XPATH: 96,
    PARTIAL_LINK_TEXT: 12,
    NAME: 8,
    ID: 8,
    CLASS_NAME: 7,
}

#: Distinct ``(strategy, selector)`` pairs among the 131.  Thirteen selectors
#: are declared more than once - seven of those repetitions inside a single
#: class - and none may be collapsed.
EXPECTED_DISTINCT_LOCATORS: Final[int] = 112

#: ``app.pages.__all__``, in full and in order.  Ten names: the base class is
#: deliberately not among them.
BARREL_EXPORTS: Final[tuple[str, ...]] = (
    "CalendarPage",
    "ContactsPage",
    "CrmPage",
    "EmployeePage",
    "InventoryPage",
    "LoginPage",
    "LogOutPage",
    "NotesPage",
    "SalesPage",
    "SessionPage",
)

#: The seven pairs of constants that share a selector **within one class**.
#: AAP 0.8 forbids de-duplicating them: two names for one selector is how the
#: reference was written, and each name is reached from a different step, so
#: merging them would change which field a step drives.
DUPLICATE_PAIRS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("CalendarPage", "CREATE_BUTTON", "EDIT_BUTTON", "CalendarP.java:49,58"),
    ("CalendarPage", "GET_NOTE", "SELECT_NOTE", "CalendarP.java:52,73"),
    ("CrmPage", "CREATE_BUTTON", "CREATE_CUSTOMER", "CrmP.java:16,76"),
    ("CrmPage", "CREATE_PIPELINE", "CREATE_CUSTOMER_BUTTON", "CrmP.java:34,82"),
    ("CrmPage", "BUTTON_PIPELINE", "PROGRESS_PIPELINE", "CrmP.java:43,64"),
    ("CrmPage", "OPPORTUNITY_TITLE_EDIT", "INPUT_NAME", "CrmP.java:49,79"),
    ("LoginPage", "INPUT_PASSWORD", "BULLET_PASS", "LoginP.java:16,31"),
)

#: The four ``SalesP`` fields no reference step class ever reads - a search
#: across all eleven step classes finds zero uses of ``allCustomers``,
#: ``warningButton``, ``link`` and ``details``.  All four are carried anyway:
#: locator fidelity is the obligation, so a dead field is preserved rather
#: than pruned (AAP 0.8).
UNREFERENCED_SALES_FIELDS: Final[tuple[str, ...]] = (
    "ALL_CUSTOMERS",
    "WARNING_BUTTON",
    "LINK",
    "DETAILS",
)

#: The one field in the reference declared ``List<WebElement>``
#: (``SalesP.java:69``), and the class that ports it.
PLURAL_OWNER: Final[str] = "SalesPage"
PLURAL_CONSTANT: Final[str] = "ALL_CUSTOMERS"

#: The credentials ``EmployeeP.login()`` sends, verbatim from
#: ``EmployeeP.java:60-61``.  AAP 0.8's test-data note governs: they are
#: pre-existing fixture data for an external test instance, carried over
#: because parity requires it, and no agent may redact, parameterize or rotate
#: them, or treat their presence as a finding.
EMPLOYEE_LOGIN_EMAIL: Final[str] = "posmanager50@info.com"
EMPLOYEE_LOGIN_PASSWORD: Final[str] = "posmanager"

#: Which page classes each step module imports from the barrel, measured
#: against ``features/steps/``.  ``notes_steps`` is the only module importing
#: two, because ``Notes.java:16-17`` declares ``InventoryP inventoryP`` and
#: ``NotesP notesP`` side by side.
STEP_MODULE_CONSUMERS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("calendar_steps", frozenset({"CalendarPage"})),
    ("contacts_steps", frozenset({"ContactsPage"})),
    ("crm_steps", frozenset({"CrmPage"})),
    ("employee_steps", frozenset({"EmployeePage"})),
    ("inventory_steps", frozenset({"InventoryPage"})),
    ("login_steps", frozenset({"LoginPage"})),
    ("logout_steps", frozenset({"LogOutPage"})),
    ("notes_steps", frozenset({"InventoryPage", "NotesPage"})),
    ("sales_steps", frozenset({"SalesPage"})),
    ("session_steps", frozenset({"SessionPage"})),
)

#: The import surface AAP 0.4.2 allows a page module: ``By`` for locator
#: construction and the base class, and nothing else.
EXPECTED_PAGE_MODULE_IMPORTS: Final[frozenset[str]] = frozenset(
    {"app.automation.By", "app.pages.base_page.BasePage"}
)

#: Names whose appearance in a page module's *code* would contradict the
#: contract: a page declares locators, it does not resolve them, wait, sleep,
#: memoize or fetch the session itself.
FORBIDDEN_CODE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "get_driver",
        "find_element",
        "find_elements",
        "sleep",
        "WebDriverWait",
        "implicitly_wait",
        "expected_conditions",
        "cached_property",
        "lru_cache",
    }
)

#: The only method calls the ten page modules make: the three DOM operations of
#: ``EmployeeP.login()`` (``EmployeeP.java:60-62``).
EXPECTED_PAGE_MODULE_CALLS: Final[frozenset[str]] = frozenset({"send_keys", "click"})

#: Calls a page module may evaluate at import time.  ``sales_page`` builds its
#: ``PLURAL_LOCATORS`` frozenset; the other nine evaluate nothing at all.
ALLOWED_IMPORT_TIME_CALLS: Final[frozenset[str]] = frozenset({"frozenset"})

#: Directory holding the ten page modules and the barrel.
PAGES_DIR: Final[Path] = REPO_ROOT / "app" / "pages"

#: Directory holding the step modules, read for the consumer-edge assertions.
STEPS_DIR: Final[Path] = REPO_ROOT / "features" / "steps"

#: Log entry names, built from the conftest prefix so this module carries no
#: second copy of the ``"element."`` convention.
ELEMENT_CLICK: Final[str] = f"{ELEMENT_PREFIX}click"
ELEMENT_SEND_KEYS: Final[str] = f"{ELEMENT_PREFIX}send_keys"


# =========================================================================== #
# Parameter sets
#
# Built once so that every per-page and per-locator test reports the page and
# the constant in its own test id: a failure reads
# ``test_locator_strategy_and_selector_is_byte_exact[CrmPage.PRINT_BUTTON]``
# and needs no further explanation.
# =========================================================================== #

PAGE_PARAMS: Final[list[Any]] = [
    pytest.param(page, id=page.export) for page in JAVA_PAGES
]

FIELD_PARAMS: Final[list[Any]] = [
    pytest.param(page, field, id=f"{page.export}.{field.constant}")
    for page in JAVA_PAGES
    for field in page.fields
]

MODULE_PARAMS: Final[list[Any]] = [
    pytest.param(page.module, id=page.module) for page in JAVA_PAGES
]


def page_class(page: JavaPage) -> type[BasePage]:
    """Resolve a table row to the class the barrel exports.

    :param page: A row of :data:`JAVA_PAGES`.
    :returns: The page class, taken from ``app.pages`` rather than from its
        defining module, so every census below also exercises the barrel.
    """
    exported = getattr(pages, page.export)
    assert isinstance(exported, type)

    return exported


def module_source(module: str) -> str:
    """Read one module under ``app/pages/`` as text.

    :param module: Module name without the ``.py`` suffix, or ``"__init__"``.
    :returns: The file's contents, decoded as UTF-8.
    """
    return (PAGES_DIR / f"{module}.py").read_text(encoding="utf-8")


def step_module_source(module: str) -> str:
    """Read one module under ``features/steps/`` as text.

    :param module: Module name without the ``.py`` suffix.
    :returns: The file's contents, decoded as UTF-8.
    """
    return (STEPS_DIR / f"{module}.py").read_text(encoding="utf-8")


def upper_snake(java_field: str) -> str:
    """Apply the port's ``camelCase`` -> ``UPPER_SNAKE`` field-name transform.

    :param java_field: A Java field name, such as ``inputEmail``.
    :returns: The Python constant name, such as ``INPUT_EMAIL``.

    An underscore is inserted before every upper-case character that is not at
    the start, then the whole is upper-cased.  Two fields are the reason this
    is written out rather than taken for granted: ``progressPipeline2`` becomes
    ``PROGRESS_PIPELINE2`` - the digit attracts no separator - and ``tagsN``
    becomes ``TAGS_N``, where a plain ``.upper()`` would give ``TAGSN``.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", java_field).upper()


# --------------------------------------------------------------------------- #
# Source-inspection helpers
#
# Each takes source *text* and answers one question about it, so the same
# helper can be pointed at a real module and at the synthetic bad sources
# below - which is what makes the sensitivity proof a committed test rather
# than a manual experiment.
#
# Syntax-tree based rather than textual, and that is load-bearing here: the
# page modules' docstrings are long and several of them begin a prose line with
# the word "from" or "import" (``contacts_page.py:111``,
# ``employee_page.py:70``, ``sales_page.py:97``), so a grep for an import
# statement produces false positives on this very package.
#
# ``tests/test_base_page.py`` carries its own copy of the helpers it needs.
# The duplication is deliberate: each module must be runnable on its own, the
# suite has no shared test-helper module, and ``tests/conftest.py`` - which
# would be the place for one - is not this module's to extend.
# --------------------------------------------------------------------------- #


def imported_names(source: str) -> frozenset[str]:
    """Every dotted name an import statement in *source* binds.

    :param source: Python source text.
    :returns: ``"module.name"`` per name bound by a ``from`` import, and the
        dotted path itself for a plain ``import``.  Relative imports keep
        their leading dots.

    The whole tree is walked, so an import nested in a function, a ``try`` or
    an ``if TYPE_CHECKING:`` guard is reported like a top-level one.
    """
    names: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            names.update(f"{module}.{alias.name}" for alias in node.names)

    return frozenset(names)


def imported_modules(source: str) -> frozenset[str]:
    """The set of modules *source* imports from, at any nesting depth.

    :param source: Python source text.
    :returns: One entry per module named by an import statement.
    """
    modules: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))

    return frozenset(modules)


def names_imported_from(source: str, module: str) -> frozenset[str]:
    """The names *source* imports from one specific module.

    :param source: Python source text.
    :param module: The module of interest, such as ``"app.pages"``.
    :returns: The names bound from it, empty when it is not imported.
    """
    return frozenset(
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    )


def import_time_call_names(source: str) -> tuple[str, ...]:
    """Names of every call *source* evaluates when it is imported.

    :param source: Python source text.
    :returns: The called name - ``frozenset`` for ``frozenset(...)``,
        ``LoginPage`` for ``LoginPage()`` - once per call site, in source
        order.

    Module-level statements and class bodies execute at import; function and
    method bodies do not, so they are skipped.  A page module that built a
    page object, started a browser or read a file on import would appear here
    as the call it makes.
    """
    names: list[str] = []

    def walk(body: list[ast.stmt]) -> None:
        for statement in body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue

            if isinstance(statement, ast.ClassDef):
                walk(statement.body)
                continue

            for node in ast.walk(statement):
                if isinstance(node, ast.Call):
                    function = node.func
                    names.append(
                        function.id
                        if isinstance(function, ast.Name)
                        else getattr(function, "attr", "<expression>")
                    )

    walk(ast.parse(source).body)

    return tuple(names)


def referenced_code_names(source: str) -> frozenset[str]:
    """Every identifier *source* uses in code: bare names and attributes.

    :param source: Python source text.
    :returns: The union of every ``Name`` identifier and every attribute name.

    Strings, comments and docstrings contribute nothing, which is what lets a
    denylist name a term these modules discuss at length in prose.
    """
    names: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    return frozenset(names)


def called_attribute_names(source: str) -> frozenset[str]:
    """Every attribute *source* calls as a method.

    :param source: Python source text.
    :returns: The attribute names of every ``x.y(...)`` call site.
    """
    return frozenset(
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )


def declared_methods(source: str) -> dict[str, tuple[str, ...]]:
    """The methods each top-level class in *source* declares.

    :param source: Python source text.
    :returns: ``{class name: (method name, ...)}`` in declaration order.

    The census behind "the reference's only page method is
    ``EmployeeP.login()``": a page object is locator constants and nothing
    else, so a second method anywhere in the package is an addition the
    request never asked for.
    """
    methods: dict[str, tuple[str, ...]] = {}

    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            methods[node.name] = tuple(
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            )

    return methods


def module_level_assignments(source: str) -> tuple[str, ...]:
    """Every name assigned at *source*'s module level, in order.

    :param source: Python source text.
    :returns: The assigned names, including the targets of tuple assignment.

    Used to assert that ``app/pages/__init__.py`` *"contains no logic"* (AAP
    0.4.1): its only module-level assignment is ``__all__``.
    """
    names: list[str] = []

    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            names.append(statement.target.id)

    return tuple(names)


# --------------------------------------------------------------------------- #
# Synthetic bad sources, one per guarantee.  Each is the smallest page module
# that violates exactly one of them, so the negative test below it fails only
# if the helper has stopped being able to see that violation.
# --------------------------------------------------------------------------- #

BAD_DIRECT_SELENIUM_IMPORT: Final[str] = '''
"""A page module that reaches past the AAP 0.4.2 boundary."""

from selenium.webdriver.common.by import By

from app.pages.base_page import BasePage


class Page(BasePage):
    """One import too many."""

    INPUT_EMAIL = (By.NAME, "login")
'''

BAD_GUARDED_SELENIUM_IMPORT: Final[str] = '''
"""A page module hiding the same violation behind a typing guard."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.remote.webelement import WebElement
'''

BAD_MODULE_LEVEL_PAGE: Final[str] = '''
"""A page module that builds a page object when it is imported."""

from app.automation import By
from app.pages.base_page import BasePage


class Page(BasePage):
    """Fine in itself."""

    INPUT_EMAIL = (By.NAME, "login")


PAGE = Page()
'''

BAD_SLEEPING_PAGE: Final[str] = '''
"""A page module that waits, which is the step call site's job."""

import time

from app.automation import By
from app.pages.base_page import BasePage


class Page(BasePage):
    """Sleeping here would change how long every step takes."""

    INPUT_EMAIL = (By.NAME, "login")

    def settle(self):
        """Wait for no stated reason."""
        time.sleep(3)
'''

BAD_EXTRA_METHOD: Final[str] = '''
"""A page module carrying a convenience the reference never had."""

from app.automation import By
from app.pages.base_page import BasePage


class Page(BasePage):
    """One locator, two methods."""

    INPUT_EMAIL = (By.NAME, "login")

    def login(self):
        """The one method the reference does have."""
        self.input_email.send_keys("x")

    def count_customers(self):
        """An addition nobody asked for."""
        return len(self.all_customers)
'''


STEP_CONSUMER_PARAMS: Final[list[Any]] = [
    pytest.param(module, names, id=module) for module, names in STEP_MODULE_CONSUMERS
]


class ReorderedLoginProbePage(pages.EmployeePage):
    """**A deliberately wrong page**: it signs in in the wrong order.

    ``EmployeeP.login()`` types the e-mail, types the password, then clicks
    (``EmployeeP.java:60-62``).  This subclass performs the same three
    operations against the same three locators in a different order, so every
    assertion that merely *counted* operations would still pass for it.  It
    exists so the ordered-sequence assertion can be shown to fail for a body
    that is wrong only in its order.
    """

    def login(
        self,
        input_login: str | None = None,
        input_pass: str | None = None,
    ) -> None:
        """Click first, then type - the reverse of the Java body.

        :param input_login: Accepted and ignored, as the real method does.
        :param input_pass: Accepted and ignored, as the real method does.
        :returns: ``None``.
        """
        self.login_button.click()
        self.input_pass.send_keys(EMPLOYEE_LOGIN_PASSWORD)
        self.input_login.send_keys(EMPLOYEE_LOGIN_EMAIL)


# =========================================================================== #
# 1. The barrel: ten classes, and only ten
# =========================================================================== #


@pytest.mark.parametrize(
    ("name", "by_member"),
    AUTHORIZED_STRATEGIES,
    ids=[name for name, _ in AUTHORIZED_STRATEGIES],
)
def test_strategy_names_are_the_authorized_by_re_export(
    name: str,
    by_member: str,
) -> None:
    """The five strategy strings in the table are ``By``'s own values.

    AAP 0.4.2 re-exports ``By`` from ``app.automation`` *"so locator
    construction needs no Selenium import elsewhere"*, and its members are
    plain strings - which is what lets this module compare strategies without
    importing selenium at all.
    """
    assert name == by_member
    assert isinstance(by_member, str)


def test_the_reference_uses_five_of_the_eight_strategies() -> None:
    """``link text``, ``tag name`` and ``css selector`` appear nowhere.

    Stated as a closed set: the histogram below accounts for all 131 fields,
    so an unused strategy turning up would mean a locator was rewritten.
    """
    used = {field.strategy for page in JAVA_PAGES for field in page.fields}

    assert used == {name for name, _ in AUTHORIZED_STRATEGIES}
    assert used.isdisjoint(UNUSED_STRATEGIES)


def test_the_table_is_in_barrel_order() -> None:
    """The expected inventory and ``__all__`` list the ten classes alike."""
    assert tuple(page.export for page in JAVA_PAGES) == BARREL_EXPORTS


def test_the_barrel_exports_exactly_the_ten_page_classes() -> None:
    """``app.pages.__all__`` is the ten names, in module-name order.

    The order is deliberate and documented in the package: sorting
    ``__all__`` case-sensitively would put ``LogOutPage`` before ``LoginPage``
    and break the correspondence with the import block, which follows the
    module names.
    """
    assert tuple(pages.__all__) == BARREL_EXPORTS

    for name in BARREL_EXPORTS:
        assert isinstance(getattr(pages, name), type), name


def test_the_barrel_exposes_no_other_public_class() -> None:
    """Nothing else public is a class, so the surface cannot widen quietly."""
    public_classes = {
        name
        for name, value in vars(pages).items()
        if not name.startswith("_") and isinstance(value, type)
    }

    assert public_classes == set(BARREL_EXPORTS)


def test_base_page_is_not_reachable_through_the_package() -> None:
    """``app.pages.BasePage`` does not resolve, and that is a contract.

    AAP 0.4.1 enumerates exactly *"the ten page classes"* for this package.
    ``BasePage`` stays at its defining module, which is where all ten page
    modules and ``tests/test_base_page.py`` import it from, so a later
    addition "for convenience" fails here instead of quietly widening a
    surface other modules are coded against.
    """
    assert not hasattr(pages, "BasePage")
    assert "BasePage" not in pages.__all__

    # Still importable where it is defined - the omission is about the barrel,
    # not about reachability.
    from app.pages.base_page import BasePage as directly_imported

    assert directly_imported is BasePage


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_every_export_subclasses_base_page(page: JavaPage) -> None:
    """Each page class inherits the lazy-resolution mechanism, not its own."""
    cls = page_class(page)

    assert issubclass(cls, BasePage)
    assert cls is not BasePage


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_every_export_is_defined_in_its_own_module(page: JavaPage) -> None:
    """One class per module, at the module name AAP 0.4.1 fixes.

    ``LogOutPage`` is the case worth naming: the class keeps the capital ``O``
    of the Java ``LogOutP`` while its module is ``logout_page`` with no
    underscore.  The two disagree on purpose.
    """
    cls = page_class(page)

    assert cls.__module__ == f"app.pages.{page.module}"
    assert cls.__name__ == page.export


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_every_page_module_exports_only_its_own_class(page: JavaPage) -> None:
    """Each module's ``__all__`` is exactly its one class."""
    module = __import__(f"app.pages.{page.module}", fromlist=[page.export])

    assert module.__all__ == [page.export]


def test_the_package_init_contains_no_logic() -> None:
    """``app/pages/__init__.py`` is a docstring, ten imports and ``__all__``.

    AAP 0.4.1's package-marker row: each such file *"re-exports its package's
    public names ... and contains no logic"*.  Asserted structurally, because
    the chain this barrel triggers runs in every behave worker process and one
    heavy import added here would be paid ten times over.
    """
    source = module_source("__init__")
    tree = ast.parse(source)

    assert module_level_assignments(source) == ("__all__",)

    import_count = 0
    for index, statement in enumerate(tree.body):
        if index == 0:
            assert isinstance(statement, ast.Expr)
            assert isinstance(statement.value, ast.Constant)
            continue

        assert isinstance(statement, ast.ImportFrom | ast.Assign), ast.dump(statement)
        import_count += isinstance(statement, ast.ImportFrom)

    assert import_count == len(BARREL_EXPORTS)
    assert import_time_call_names(source) == ()


# =========================================================================== #
# 2. The committed table, checked against figures declared apart from it
# =========================================================================== #


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_table_carries_the_declared_field_count(page: JavaPage) -> None:
    """Each class's row count is the ``@FindBy`` count measured for it."""
    assert len(page.fields) == EXPECTED_COUNTS[page.java_class]


def test_the_table_totals_131_fields() -> None:
    """21 + 16 + 28 + 15 + 8 + 7 + 3 + 10 + 20 + 3 = 131."""
    assert sum(EXPECTED_COUNTS.values()) == EXPECTED_TOTAL
    assert sum(len(page.fields) for page in JAVA_PAGES) == EXPECTED_TOTAL


def test_the_table_matches_the_strategy_histogram() -> None:
    """The 131 divide 96 / 12 / 8 / 8 / 7 across the five strategies."""
    histogram = Counter(field.strategy for page in JAVA_PAGES for field in page.fields)

    assert dict(histogram) == EXPECTED_STRATEGY_HISTOGRAM
    assert sum(EXPECTED_STRATEGY_HISTOGRAM.values()) == EXPECTED_TOTAL


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_table_rows_ascend_by_java_line(page: JavaPage) -> None:
    """Rows are in Java source order, which is what makes order assertable."""
    lines = [field.java_line for field in page.fields]

    assert lines == sorted(lines)
    assert len(set(lines)) == len(lines)


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_table_names_are_unique_within_a_class(page: JavaPage) -> None:
    """No constant is declared twice - duplicate *selectors* are, names are not."""
    constants = [field.constant for field in page.fields]
    java_fields = [field.java_field for field in page.fields]

    assert len(set(constants)) == len(constants)
    assert len(set(java_fields)) == len(java_fields)


@pytest.mark.parametrize(("page", "field"), FIELD_PARAMS)
def test_the_constant_is_the_upper_snake_of_the_java_field(
    page: JavaPage,
    field: JavaField,
) -> None:
    """Every name in the table is the transform of its Java field, not a rename.

    Each of the 131 cases carries its own anchor - the row's ``java_class``
    and ``java_line`` name the ``@FindBy`` it came from - so a hand-picked or
    "tidied" constant name fails here rather than being asserted against the
    implementation as though the reference had said it.
    """
    assert upper_snake(field.java_field) == field.constant


def test_the_name_transform_handles_the_two_awkward_fields() -> None:
    """The digit and the single-letter suffix, stated as their own cases.

    ``CrmP.progressPipeline2`` (``CrmP.java:68``) is the reference's only
    digit-bearing field and ``NotesP.tagsN`` (``NotesP.java:24``) its only
    single-upper-case-letter suffix.  A plain ``.upper()`` would give
    ``TAGSN``, which is why the transform is written out and tested.
    """
    assert upper_snake("progressPipeline2") == "PROGRESS_PIPELINE2"
    assert upper_snake("tagsN") == "TAGS_N"
    assert upper_snake("appK") == "APP_K"
    assert upper_snake("inputEmail") == "INPUT_EMAIL"
    assert upper_snake("title") == "TITLE"

    # Sensitivity: the naive transform disagrees on two of the five.
    assert "tagsN".upper() != upper_snake("tagsN")
    assert "inputEmail".upper() != upper_snake("inputEmail")


def test_the_table_marks_exactly_one_plural_field() -> None:
    """``SalesP.allCustomers`` (``SalesP.java:69``) and nothing else.

    The only ``List<WebElement>`` in the whole reference; the other 130 fields
    are scalar ``WebElement``.
    """
    plural = [
        (page.export, field.constant)
        for page in JAVA_PAGES
        for field in page.fields
        if field.plural
    ]

    assert plural == [(PLURAL_OWNER, PLURAL_CONSTANT)]


# =========================================================================== #
# 3. The implementation, measured against the table
# =========================================================================== #


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_page_declares_the_expected_number_of_locators(page: JavaPage) -> None:
    """A dropped or invented field fails here, per class."""
    cls = page_class(page)

    assert len(cls.LOCATORS) == EXPECTED_COUNTS[page.java_class]


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_page_locators_are_in_java_declaration_order(page: JavaPage) -> None:
    """Names and their order both match the ``@FindBy`` sequence.

    ``BasePage.LOCATORS`` is built from the class body and nothing sorts it,
    so this is a real property of the module rather than an artefact of the
    mechanism - and ``SalesP`` is the case that proves it matters:
    ``allCustomers`` sits *between* ``warning`` and ``link`` in the Java file,
    so the tail must read ``WARNING`` -> ``ALL_CUSTOMERS`` -> ``LINK`` ->
    ``DETAILS``.
    """
    cls = page_class(page)

    assert tuple(cls.LOCATORS) == tuple(field.constant for field in page.fields)


@pytest.mark.parametrize(("page", "field"), FIELD_PARAMS)
def test_the_locator_strategy_and_selector_are_byte_exact(
    page: JavaPage,
    field: JavaField,
) -> None:
    """One locator, compared character for character with its Java anchor.

    Both access paths are checked - the inventory entry and the class
    attribute - because step modules and the parity tests reach the constant
    directly while ``BasePage`` resolves through ``LOCATORS``.
    """
    cls = page_class(page)
    expected = (field.strategy, field.selector)
    anchor = f"{page.java_class}.java:{field.java_line}"

    assert cls.LOCATORS[field.constant] == expected, anchor
    assert getattr(cls, field.constant) == expected, anchor

    strategy, selector = cls.LOCATORS[field.constant]
    assert isinstance(strategy, str)
    assert isinstance(selector, str)
    assert len(selector) == len(field.selector), anchor


@pytest.mark.parametrize(("page", "field"), FIELD_PARAMS)
def test_the_locator_has_a_read_only_accessor_under_its_lower_name(
    page: JavaPage,
    field: JavaField,
) -> None:
    """Each constant yields exactly one read-only property.

    The ``@FindBy`` field's own name in Python spelling: ``INPUT_EMAIL`` for
    the locator tuple, ``input_email`` for the live element.
    """
    cls = page_class(page)
    accessor = inspect.getattr_static(cls, field.constant.lower())

    assert isinstance(accessor, property)
    assert accessor.fset is None
    assert accessor.fdel is None


@pytest.mark.parametrize(("page", "field"), FIELD_PARAMS)
def test_the_locator_reaches_the_driver_as_declared(
    page: JavaPage,
    field: JavaField,
    stub_driver: StubDriver,
) -> None:
    """Reading the accessor performs one lookup with exactly this locator.

    The assertion a scenario actually depends on: not that the constant holds
    the right pair, but that the pair the driver receives is that pair - and
    that the plural field is the only one routed to ``find_elements``.
    """
    cls = page_class(page)
    page_object = cls(stub_driver)

    getattr(page_object, field.constant.lower())

    expected_call = (field.strategy, field.selector)
    if field.plural:
        assert stub_driver.calls_of("find_elements") == (expected_call,)
        assert stub_driver.count_of("find_element") == 0
    else:
        assert stub_driver.calls_of("find_element") == (expected_call,)
        assert stub_driver.count_of("find_elements") == 0


@pytest.mark.parametrize("page", PAGE_PARAMS)
def test_the_page_inventory_is_an_immutable_mapping(page: JavaPage) -> None:
    """A consumer enumerating a page's locators cannot alter them."""
    cls = page_class(page)

    assert isinstance(cls.LOCATORS, MappingProxyType)

    with pytest.raises(TypeError):
        cls.LOCATORS["INVENTED"] = (By.ID, "invented")  # type: ignore[index]


def test_the_barrel_declares_131_locators_in_total() -> None:
    """The parity invariant, summed over the ten classes."""
    total = sum(len(page_class(page).LOCATORS) for page in JAVA_PAGES)

    assert total == EXPECTED_TOTAL


def test_the_implementation_matches_the_strategy_histogram() -> None:
    """96 xpath, 12 partial link text, 8 name, 8 id, 7 class name.

    A strategy silently changed - ``class name`` rewritten as a CSS selector,
    say, which would often still work against a live page - fails here.
    """
    histogram = Counter(
        strategy
        for page in JAVA_PAGES
        for strategy, _ in page_class(page).LOCATORS.values()
    )

    assert dict(histogram) == EXPECTED_STRATEGY_HISTOGRAM


# =========================================================================== #
# 4. Preserved quirks
#
# Each of these is a byte a reasonable person would "fix".  Every one of them
# selects a real element in the application under test exactly as written, and
# AAP 0.8 - "Preserve, do not tidy" - is why each has its own named test with
# the Java line that makes it binding.
# =========================================================================== #


def test_calendar_month_and_year_selector_keeps_its_odd_whitespace() -> None:
    """``CalendarP.java:34`` - a leading space *and* an internal double space.

    ``//td[@class='...']`` is an exact attribute-value match, not a token
    match, so trimming the leading space or collapsing the double space would
    change which element is found - from the current day's cell to nothing at
    all.
    """
    selector = pages.CalendarPage.MONTH_AND_YEAR_CALENDAR[1]

    assert selector == (
        "//td[@class=' ui-datepicker-days-cell-over  "
        "ui-datepicker-current-day ui-datepicker-today']"
    )
    assert selector.startswith("//td[@class=' ")
    assert "  ui-datepicker-current-day" in selector
    assert selector.count("  ") == 1

    # Sensitivity, stated in the test rather than assumed: the obvious tidy-up
    # produces a different string.
    assert " ".join(selector.split()) != selector


def test_logout_warning_selector_keeps_the_space_after_the_equals() -> None:
    """``LogOutP.java:20`` - ``@class= 'o_dialog_warning modal-body'``.

    Legal XPath and preserved verbatim; the space sits between the ``=`` and
    the quoted value.  This is the only *attribute* match in the suite written
    that way - ``NotesP.java:14`` spaces a ``.`` comparison instead, which the
    next test covers - so the closed-set assertion below is what keeps it from
    being normalised into the shape the other thirty-eight ``@class=``
    selectors share.
    """
    selector = pages.LogOutPage.WARNING_MESS[1]

    assert selector == "//div[@class= 'o_dialog_warning modal-body']"
    assert "@class= '" in selector
    assert "@class='" not in selector

    spaced = {
        (page.export, constant)
        for page in JAVA_PAGES
        for constant, (_, value) in page_class(page).LOCATORS.items()
        if "@class= " in value
    }

    assert spaced == {("LogOutPage", "WARNING_MESS")}


def test_notes_tabindex_selector_keeps_the_spaces_around_the_dot() -> None:
    """``NotesP.java:14`` - ``[. = 'Create and Edit...']``, spaced.

    ``SalesP.java:35`` writes the same visible text unspaced,
    ``[.='Create and Edit...']``, on a different element.  Two spellings of
    one idea, both preserved, because normalising either would edit a
    reference the port is required to reproduce.
    """
    notes_selector = pages.NotesPage.TABINDEX[1]
    sales_selector = pages.SalesPage.CREATE_AND_EDIT_STATE[1]

    assert notes_selector == "//a[. = 'Create and Edit...']"
    assert "[. = '" in notes_selector

    assert sales_selector == "//li[.='Create and Edit...']"
    assert "[.='" in sales_selector

    assert notes_selector != sales_selector


@pytest.mark.parametrize(
    ("export", "constant", "expected", "anchor"),
    [
        (
            "EmployeePage",
            "NAME_EDIT",
            '//*[@id="o_field_input_678"]',
            "EmployeeP.java:56",
        ),
        (
            "SalesPage",
            "DETAILS",
            '//div[@class="oe_kanban_details"]//span',
            "SalesP.java:74",
        ),
    ],
    ids=["EmployeePage.NAME_EDIT", "SalesPage.DETAILS"],
)
def test_the_two_double_quoted_selectors_carry_literal_double_quotes(
    export: str,
    constant: str,
    expected: str,
    anchor: str,
) -> None:
    """The Java source escapes them; the selector contains the quotes.

    ``"//*[@id=\"o_field_input_678\"]"`` in Java denotes a string that
    *contains* two double-quote characters; the backslashes belong to the Java
    literal, not to the selector.  The port writes it in a single-quoted
    Python string so the bytes survive: converting the inner quotes to single
    quotes, or leaving a backslash in, would each be a different selector.
    """
    strategy, selector = getattr(getattr(pages, export), constant)

    assert strategy == XPATH, anchor
    assert selector == expected, anchor
    assert '"' in selector
    assert "\\" not in selector
    assert "'" not in selector


def test_no_selector_carries_a_java_escape_artefact() -> None:
    """Not one of the 131 selectors contains a backslash.

    The generic form of the test above: a mechanical transcription of the Java
    literals would leave ``\\"`` in the two selectors that escape a quote, and
    nowhere else, so a backslash anywhere is that mistake.
    """
    for page in JAVA_PAGES:
        for constant, (_, selector) in page_class(page).LOCATORS.items():
            assert "\\" not in selector, f"{page.export}.{constant}"


def test_the_crm_ampersand_selectors_are_left_unescaped() -> None:
    """``CrmP.java:25`` and ``:88`` - ``&CC`` as written, not ``&amp;CC``.

    The ``&`` is a literal character of the element's text, and HTML-escaping
    it - the reflex a reviewer might have - would look for a different string.
    """
    customer_id = pages.CrmPage.CUSTOMER_ID[1]
    name_customer = pages.CrmPage.NAME_CUSTOMER[1]

    assert customer_id == "//a[.='&CC']"
    assert name_customer == "//span[.='&CC']"

    for selector in (customer_id, name_customer):
        assert "&CC" in selector
        assert "&amp;" not in selector


@pytest.mark.parametrize(
    ("export", "constant", "expected", "anchor"),
    [
        (
            "CrmPage",
            "PRINT_BUTTON",
            "/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/div[1]/button",
            "CrmP.java:94",
        ),
        (
            "EmployeePage",
            "CHOOSE_EMPLOYEE",
            "//html/body/div[1]/div[2]/div[2]/div/div/div/div[1]",
            "EmployeeP.java:50",
        ),
        (
            "EmployeePage",
            "EDIT_EMPLOYEE",
            "//html/body/div[1]/div[2]/div[1]/div[2]/div[1]/div/div[1]/button[1]",
            "EmployeeP.java:53",
        ),
    ],
    ids=["CrmPage.PRINT_BUTTON", "EmployeePage.CHOOSE_EMPLOYEE", "EmployeePage.EDIT_EMPLOYEE"],
)
def test_the_absolute_dom_paths_are_preserved(
    export: str,
    constant: str,
    expected: str,
    anchor: str,
) -> None:
    """Three brittle-by-construction locators, reproduced exactly.

    They are the reference's choices, not the port's.  "Correcting" one would
    silently retarget the flow that drives it, and the single-slash
    ``/html`` of ``CrmP.java:94`` differs from the double-slash ``//html`` of
    the two ``EmployeeP`` paths - a difference in the source, kept.
    """
    strategy, selector = getattr(getattr(pages, export), constant)

    assert strategy == XPATH, anchor
    assert selector == expected, anchor


def test_the_digit_bearing_and_letter_suffix_names_are_kept() -> None:
    """``PROGRESS_PIPELINE2`` and ``TAGS_N`` exist under exactly those names.

    Both would be casualties of a name "tidy-up": ``PROGRESS_PIPELINE_2`` and
    ``TAGS`` are the plausible rewrites, and neither is what the transform of
    the Java field produces.  ``PROGRESS_PIPELINE`` also exists, with a
    different selector, so the pair must stay distinct.
    """
    crm = pages.CrmPage

    assert "PROGRESS_PIPELINE2" in crm.LOCATORS
    assert "PROGRESS_PIPELINE_2" not in crm.LOCATORS
    assert crm.PROGRESS_PIPELINE2 == (XPATH, "//div[@data-id='2']/div[2]")
    assert crm.PROGRESS_PIPELINE == (XPATH, "//div[@data-id='1']/div[2]")
    assert crm.PROGRESS_PIPELINE2 != crm.PROGRESS_PIPELINE

    assert "TAGS_N" in pages.NotesPage.LOCATORS
    assert "TAGS" not in pages.NotesPage.LOCATORS
    assert hasattr(pages.NotesPage, "tags_n")


@pytest.mark.parametrize(
    ("export", "first", "second", "anchor"),
    [pytest.param(*pair, id=f"{pair[0]}.{pair[1]}~{pair[2]}") for pair in DUPLICATE_PAIRS],
)
def test_the_duplicate_selector_pairs_are_both_declared(
    export: str,
    first: str,
    second: str,
    anchor: str,
) -> None:
    """Two names, one selector, both kept - seven times over.

    De-duplicating would leave one name resolving and the other missing, and
    each name is reached from a different step, so the merge would change
    behaviour rather than tidy it.  ``LoginP``'s pair is the clearest case:
    ``LoginSD.java:62`` deliberately reads ``bulletPass`` rather than the
    identically-selected ``inputPassword``.
    """
    cls = getattr(pages, export)

    assert first in cls.LOCATORS, anchor
    assert second in cls.LOCATORS, anchor
    assert cls.LOCATORS[first] == cls.LOCATORS[second], anchor
    assert first != second

    # Both accessors exist and resolve the same locator independently.
    assert isinstance(inspect.getattr_static(cls, first.lower()), property)
    assert isinstance(inspect.getattr_static(cls, second.lower()), property)


def test_the_seven_pairs_are_every_within_class_duplicate() -> None:
    """No other class declares one selector twice.

    Asserted as a closed set so that a *new* duplicate - which would normally
    mean a copy-paste slip in a page module - is as visible as a removed one.
    Membership rather than sequence: the pairs are discovered in class-body
    order, which for ``CrmP`` interleaves them differently from
    :data:`DUPLICATE_PAIRS`' own first-declaration ordering, and neither
    ordering is part of the contract.
    """
    found: set[tuple[str, str, str]] = set()

    for page in JAVA_PAGES:
        seen: dict[tuple[str, str], str] = {}
        for constant, locator in page_class(page).LOCATORS.items():
            if locator in seen:
                found.add((page.export, seen[locator], constant))
            else:
                seen[locator] = constant

    assert found == {(export, first, second) for export, first, second, _ in DUPLICATE_PAIRS}
    assert len(found) == len(DUPLICATE_PAIRS) == 7


def test_the_barrel_wide_locator_multiset_is_never_collapsed() -> None:
    """131 declarations over 112 distinct pairs, across the ten classes.

    Thirteen selectors are declared more than once in total.  Seven of those
    groups contain two names inside a single class - the pairs above, the ones
    a de-duplication inside one module could merge - and the remaining six
    repeat only across classes, where no single-module change could collapse
    them.  The widest group is the form-save button at four
    (``EmployeeP.java:44``, ``InventoryP.java:23``, ``NotesP.java:32``,
    ``SalesP.java:53``).
    """
    locators = [
        locator for page in JAVA_PAGES for locator in page_class(page).LOCATORS.values()
    ]

    assert len(locators) == EXPECTED_TOTAL
    assert len(set(locators)) == EXPECTED_DISTINCT_LOCATORS


@pytest.mark.parametrize("constant", UNREFERENCED_SALES_FIELDS)
def test_the_unreferenced_sales_fields_are_still_declared(constant: str) -> None:
    """Four ``SalesP`` fields no step ever reads, carried regardless.

    A search across all eleven reference step classes finds zero uses of
    ``allCustomers`` (``SalesP.java:69``), ``warningButton`` (``:62``),
    ``link`` (``:71``) and ``details`` (``:74``), leaving sixteen of the
    twenty reached by ``Sales.java``.  Locator fidelity is the obligation, so
    a dead field is preserved rather than pruned.
    """
    assert constant in pages.SalesPage.LOCATORS
    assert isinstance(inspect.getattr_static(pages.SalesPage, constant.lower()), property)


def test_the_odoo_vocabulary_is_preserved_and_nothing_is_renamed() -> None:
    """AAP 0.1.3 Conflict 8: all three vocabularies stay exactly where they are.

    The specification says Testinium, the pipeline's original checkout says
    Upgenix, and the implementation asserts against Odoo.  The resolution is
    *no renaming*: the Odoo assertion in ``CalendarP.java:13`` is kept as
    written, and no selector acquires either of the other two names.
    """
    selectors = {
        (page.export, constant): selector
        for page in JAVA_PAGES
        for constant, (_, selector) in page_class(page).LOCATORS.items()
    }

    odoo = {key for key, selector in selectors.items() if "Odoo" in selector}
    assert odoo == {("CalendarPage", "TITLE")}
    assert pages.CalendarPage.TITLE == (XPATH, "//title[.='Meetings - Odoo']")

    for key, selector in selectors.items():
        assert "Testinium" not in selector, key
        assert "Upgenix" not in selector, key


def test_the_two_login_forms_keep_their_different_strategies() -> None:
    """``SessionPage`` targets the login form by id, ``LoginPage`` by name.

    The same two controls, reached two ways: ``SessionP.java:14`` and ``:17``
    declare ``@FindBy(id = "login")`` and ``@FindBy(id = "password")``, while
    ``LoginP.java:13`` and ``:16`` declare ``@FindBy(name = "login")`` and
    ``@FindBy(name="password")`` for the same fields of the same Odoo form -
    and the submit control is byte-identical in both classes
    (``LoginP.java:19``, ``SessionP.java:20``).

    Neither class is changed to match the other.  AAP 0.2.2 puts *"correcting
    the source's other inconsistencies"* out of scope, and this divergence is
    the most inviting of them to harmonize precisely because both strategies
    work against the live DOM - which is why it is asserted here as a pair
    rather than left to the two classes' individual census rows.  Collapsing
    them would also silently change what ``login_steps.py`` builds inline at
    ``LoginSD.java:56``, where ``By.name("login")`` is constructed by hand to
    read the field's ``validationMessage``.
    """
    assert pages.SessionPage.INPUT_LOGIN == (By.ID, "login")
    assert pages.SessionPage.INPUT_PASS == (By.ID, "password")

    assert pages.LoginPage.INPUT_EMAIL == (By.NAME, "login")
    assert pages.LoginPage.INPUT_PASSWORD == (By.NAME, "password")

    # Same control, same selector, in both classes.
    assert pages.SessionPage.LOGIN_BUTTON == pages.LoginPage.BUTTON
    assert pages.SessionPage.LOGIN_BUTTON == (XPATH, "//button[.='Log in']")


# =========================================================================== #
# 5. The port's one plural locator
# =========================================================================== #


def test_sales_page_declares_the_only_plural_locator() -> None:
    """``SalesPage.PLURAL_LOCATORS == frozenset({"ALL_CUSTOMERS"})``.

    The port of ``SalesP.java:68-69``, the single ``List<WebElement>`` field
    in the reference - which makes ``app/pages/sales_page.py`` the only
    consumer of ``BasePage.find_all`` and the reason that method exists.
    """
    assert pages.SalesPage.PLURAL_LOCATORS == frozenset({PLURAL_CONSTANT})
    assert isinstance(pages.SalesPage.PLURAL_LOCATORS, frozenset)
    assert PLURAL_CONSTANT in pages.SalesPage.LOCATORS


@pytest.mark.parametrize(
    "page",
    [param for param in PAGE_PARAMS if param.values[0].export != PLURAL_OWNER],
)
def test_the_other_nine_pages_declare_no_plural_locator(page: JavaPage) -> None:
    """Nine classes declare no ``List<WebElement>``, so nine override nothing."""
    cls = page_class(page)

    assert cls.PLURAL_LOCATORS == frozenset()
    assert "PLURAL_LOCATORS" not in vars(cls)


def test_all_customers_resolves_through_find_elements(
    stub_driver: StubDriver,
) -> None:
    """The kanban cards come back as a list, rebuilt from the live DOM."""
    locator = pages.SalesPage.ALL_CUSTOMERS
    stub_driver.set_element_count(locator, 3)
    page = pages.SalesPage(stub_driver)

    customers = page.all_customers

    assert isinstance(customers, list)
    assert len(customers) == 3
    assert stub_driver.operations() == ("find_elements",)
    assert stub_driver.calls_of("find_elements") == (locator,)


def test_all_customers_is_empty_when_nothing_matches(
    stub_driver: StubDriver,
) -> None:
    """Nothing matching is not an error for the plural accessor."""
    stub_driver.set_element_count(pages.SalesPage.ALL_CUSTOMERS, 0)
    page = pages.SalesPage(stub_driver)

    assert page.all_customers == []


def test_all_customers_is_rebuilt_on_every_access(
    stub_driver: StubDriver,
) -> None:
    """Three reads, three ``find_elements`` calls - the list is never cached."""
    stub_driver.set_element_count(pages.SalesPage.ALL_CUSTOMERS, 2)
    page = pages.SalesPage(stub_driver)

    for _ in range(3):
        assert len(page.all_customers) == 2

    assert stub_driver.count_of("find_elements") == 3


def test_a_singular_sales_accessor_still_uses_find_element(
    stub_driver: StubDriver,
) -> None:
    """Plurality is per constant, not per page.

    ``SEARCH_BAR`` sits in the same class as the plural field and must stay
    singular: an accessor that returned a list here would satisfy no step.
    """
    page = pages.SalesPage(stub_driver)

    page.search_bar

    assert stub_driver.operations() == ("find_element",)
    assert stub_driver.calls_of("find_element") == (pages.SalesPage.SEARCH_BAR,)


# =========================================================================== #
# 6. EmployeePage.login() - the only behaviour in the package
# =========================================================================== #


def test_login_performs_the_three_java_operations_in_order(
    stub_driver: StubDriver,
) -> None:
    """``EmployeeP.java:60-62``, statement for statement.

    Three operations, each preceded by its own lookup: the Java proxy
    re-locates on every method invocation, so three operations meant three
    lookups there and mean three here.  Hoisting the elements into locals
    would collapse them to three lookups at one earlier instant and change
    behaviour on a page that re-renders between operations.
    """
    page = pages.EmployeePage(stub_driver)

    page.login()

    assert stub_driver.operations() == (
        "find_element",
        ELEMENT_SEND_KEYS,
        "find_element",
        ELEMENT_SEND_KEYS,
        "find_element",
        ELEMENT_CLICK,
    )
    assert stub_driver.calls_of("find_element") == (
        pages.EmployeePage.INPUT_LOGIN,
        pages.EmployeePage.INPUT_PASS,
        pages.EmployeePage.LOGIN_BUTTON,
    )
    assert stub_driver.calls_of(ELEMENT_SEND_KEYS) == (
        (pages.EmployeePage.INPUT_LOGIN, EMPLOYEE_LOGIN_EMAIL),
        (pages.EmployeePage.INPUT_PASS, EMPLOYEE_LOGIN_PASSWORD),
    )
    assert stub_driver.calls_of(ELEMENT_CLICK) == ((pages.EmployeePage.LOGIN_BUTTON,),)


def test_login_neither_clears_a_field_nor_waits(stub_driver: StubDriver) -> None:
    """Six operations in total, and no others.

    The Java body has three statements and so does the Python one: no
    ``clear()`` before typing - the reference clears only ``nameEdit``, at
    ``EmployeeStage.java:101`` - no explicit wait, and no assertion that the
    sign-in succeeded, which the feature asserts in its own steps.
    """
    page = pages.EmployeePage(stub_driver)

    page.login()

    assert len(stub_driver.calls) == 6
    assert stub_driver.count_of(f"{ELEMENT_PREFIX}clear") == 0
    assert stub_driver.count_of("get") == 0
    assert stub_driver.count_of("title") == 0


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda page: page.login(), id="no-arguments"),
        pytest.param(
            lambda page: page.login("someone@example.com", "secret"),
            id="two-positional",
        ),
        pytest.param(
            lambda page: page.login(input_login="someone@example.com", input_pass="secret"),
            id="two-keyword",
        ),
        pytest.param(lambda page: page.login("someone@example.com"), id="one-positional"),
    ],
)
def test_login_ignores_its_arguments_entirely(
    call: Any,
    stub_driver: StubDriver,
) -> None:
    """Every call shape sends the same two hard-coded credentials.

    ``EmployeeP.java`` declares **two** overloads, ``login()`` at ``:59-63``
    and ``login(String, String)`` at ``:65-69``, with byte-identical bodies:
    the two-argument form discards both arguments and sends the same
    literals.  Python has no overloading, so the port declares one method with
    optional parameters and reproduces the discard.  That is faithful
    reproduction, not an unfinished parameterization - honouring the arguments
    would change behaviour.
    """
    page = pages.EmployeePage(stub_driver)

    assert call(page) is None

    assert stub_driver.calls_of(ELEMENT_SEND_KEYS) == (
        (pages.EmployeePage.INPUT_LOGIN, EMPLOYEE_LOGIN_EMAIL),
        (pages.EmployeePage.INPUT_PASS, EMPLOYEE_LOGIN_PASSWORD),
    )
    assert "someone@example.com" not in str(stub_driver.calls)
    assert "secret" not in str(stub_driver.calls)


def test_login_accepts_zero_one_or_two_arguments() -> None:
    """Both Java call shapes stay expressible, and neither is required."""
    signature = inspect.signature(pages.EmployeePage.login)
    parameters = list(signature.parameters)

    assert parameters == ["self", "input_login", "input_pass"]
    for name in ("input_login", "input_pass"):
        assert signature.parameters[name].default is None
        assert signature.parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_login_consults_no_configuration_accessor(
    stub_driver: StubDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credentials are hard-coded, and no ``app.config`` read happens.

    ``EmployeeP.java:60-61`` types two literals, and AAP 0.4.1 keeps the
    configuration surface at six keys, none of which is a credential: ``url``
    and ``EmplTitle`` are the Employee flow's only two, and AAP 0.4.2 assigns
    both reads to ``features/steps/employee_steps.py``, not to this page.

    The import-surface assertion elsewhere in this module already proves no
    page module imports ``app.config`` at all.  This is the behavioural
    complement, and it covers the one route that a static import check cannot
    see: an ``import app.config`` written *inside* the method body.  Every
    public accessor of the module is replaced with one that raises, so a read
    of any of the six keys would fail the test rather than quietly return a
    value - and the three operations are still asserted afterwards, so a
    method that silently swallowed the error could not pass either.
    """
    consulted: list[str] = []

    def refuse(name: str) -> Callable[..., object]:
        def accessor(*args: object, **kwargs: object) -> object:
            consulted.append(name)
            raise AssertionError(
                f"EmployeePage.login() read configuration through "
                f"app.config.{name}(); EmployeeP.java:60-61 types two "
                f"hard-coded literals and AAP 0.4.2 gives page objects no "
                f"configuration reads at all"
            )

        return accessor

    for name in (
        "get_browser",
        "get_empl_title",
        "get_password",
        "get_property",
        "get_url",
        "get_username",
        "get_web_table_url",
    ):
        monkeypatch.setattr(config, name, refuse(name))

    pages.EmployeePage(stub_driver).login()

    assert consulted == []
    assert stub_driver.calls_of(ELEMENT_SEND_KEYS) == (
        (pages.EmployeePage.INPUT_LOGIN, EMPLOYEE_LOGIN_EMAIL),
        (pages.EmployeePage.INPUT_PASS, EMPLOYEE_LOGIN_PASSWORD),
    )
    assert stub_driver.calls_of(ELEMENT_CLICK) == ((pages.EmployeePage.LOGIN_BUTTON,),)


def test_the_login_sequence_assertion_detects_a_reordered_body(
    stub_driver: StubDriver,
) -> None:
    """Sensitivity: the same three operations in the wrong order are caught.

    :class:`ReorderedLoginProbePage` performs exactly three lookups and
    exactly three operations, so every count-based assertion passes for it.
    Only the ordered sequence distinguishes it from the real method.
    """
    correct = pages.EmployeePage(stub_driver)
    correct.login()
    correct_sequence = stub_driver.operations()

    stub_driver.clear_calls()
    broken = ReorderedLoginProbePage(stub_driver)
    broken.login()
    broken_sequence = stub_driver.operations()

    assert len(stub_driver.calls) == 6
    assert broken_sequence != correct_sequence
    assert broken_sequence == (
        "find_element",
        ELEMENT_CLICK,
        "find_element",
        ELEMENT_SEND_KEYS,
        "find_element",
        ELEMENT_SEND_KEYS,
    )


def test_login_is_the_only_method_in_the_whole_package() -> None:
    """Ten page classes, one method between them.

    ``EmployeeP.login()`` is the only behaviour in the reference's page
    package, so it is the only behaviour here: no navigation helper, no
    ``create_customer(...)`` convenience over a form, no ``count_customers()``
    over the plural accessor.  Each would be an addition the request never
    asked for.
    """
    census = {
        page.export: declared_methods(module_source(page.module)).get(page.export, ())
        for page in JAVA_PAGES
    }

    assert census["EmployeePage"] == ("login",)
    assert {export: methods for export, methods in census.items() if methods} == {
        "EmployeePage": ("login",)
    }


def test_the_method_census_detects_an_added_convenience() -> None:
    """Sensitivity: a second method on a page class is seen as one."""
    census = declared_methods(BAD_EXTRA_METHOD)

    assert census == {"Page": ("login", "count_customers")}


# =========================================================================== #
# 7. The import boundary, the source guarantees and the consumer edges
# =========================================================================== #


@pytest.mark.parametrize("module", MODULE_PARAMS)
def test_a_page_module_imports_exactly_by_and_the_base_class(module: str) -> None:
    """AAP 0.4.2, as an equality rather than a denylist.

    *"Page objects import ``app.automation`` for the current driver, nothing
    else."*  ``By`` for locator construction and ``BasePage`` for the
    mechanism: an added import - ``app.config``, a service, a path helper -
    fails here just as loudly as a browser-library one.
    """
    assert imported_names(module_source(module)) == EXPECTED_PAGE_MODULE_IMPORTS


@pytest.mark.parametrize(
    "module",
    [param.values[0] for param in MODULE_PARAMS] + ["base_page", "__init__"],
)
def test_no_module_under_app_pages_imports_selenium(module: str) -> None:
    """Twelve modules, no browser-library import in any of them.

    ``app.automation`` is the only package in the port permitted to import it,
    which is what lets the unit suite import every page on a host with no
    browser installed - and what this module relies on to run at all.
    """
    modules = imported_modules(module_source(module))

    assert not any(name.split(".")[0] == "selenium" for name in modules), module


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("direct", BAD_DIRECT_SELENIUM_IMPORT),
        ("type-checking-guarded", BAD_GUARDED_SELENIUM_IMPORT),
    ],
)
def test_the_import_check_detects_a_browser_library_import(
    label: str,
    source: str,
) -> None:
    """Sensitivity: both spellings of the violation are seen.

    The guarded form matters twice over: a runtime probe would miss it because
    ``TYPE_CHECKING`` is ``False`` when the interpreter runs, and a textual
    grep would miss the *page modules'* real imports while flagging the word
    "from" in their prose.
    """
    modules = imported_modules(source)

    assert any(name.split(".")[0] == "selenium" for name in modules), label


@pytest.mark.parametrize("module", MODULE_PARAMS)
def test_a_page_module_builds_nothing_when_it_is_imported(module: str) -> None:
    """Import-time calls are limited to building an immutable constant.

    No page module may construct a page object at import: a module-level
    instance would bind whichever worker process imported the module first,
    which is why every step module builds its page per call from
    ``context.driver`` instead.
    """
    calls = set(import_time_call_names(module_source(module)))

    assert calls <= ALLOWED_IMPORT_TIME_CALLS, module


def test_the_import_time_check_detects_a_module_level_page_object() -> None:
    """Sensitivity: ``PAGE = Page()`` at module level is seen."""
    calls = set(import_time_call_names(BAD_MODULE_LEVEL_PAGE))

    assert "Page" in calls
    assert not calls <= ALLOWED_IMPORT_TIME_CALLS


@pytest.mark.parametrize("module", MODULE_PARAMS)
def test_a_page_module_neither_resolves_nor_waits_nor_memoizes(
    module: str,
) -> None:
    """A page declares locators; ``BasePage`` resolves them.

    None of the forbidden names appears in the module's code: no
    ``find_element`` of its own, no ``get_driver``, no wait and no sleep - the
    explicit waits live at the step call sites, because the source fixes a
    different timeout per step class.
    """
    referenced = referenced_code_names(module_source(module))

    assert not (referenced & FORBIDDEN_CODE_NAMES), module


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("time.sleep", BAD_SLEEPING_PAGE, "sleep"),
        ("selenium-import", BAD_DIRECT_SELENIUM_IMPORT, "By"),
    ],
)
def test_the_code_name_check_reads_code_and_not_prose(
    label: str,
    source: str,
    expected: str,
) -> None:
    """Sensitivity: a sleeping page is caught, and prose is never mistaken.

    The second case is the control: ``By`` is used in code by both the
    synthetic source and every real page module, and it is *not* on the
    denylist - so the check is discriminating rather than merely alarming.
    """
    referenced = referenced_code_names(source)

    assert expected in referenced, label
    assert ("sleep" in referenced) == (label == "time.sleep")


def test_the_only_method_calls_in_the_package_are_logins_three() -> None:
    """Across the ten modules, the only calls are ``send_keys`` and ``click``.

    The whole package's behaviour surface in one assertion: nine modules call
    nothing at all, and the tenth performs the three DOM operations of
    ``EmployeeP.java:60-62``.
    """
    calls: set[str] = set()
    for page in JAVA_PAGES:
        calls |= called_attribute_names(module_source(page.module))

    assert calls == EXPECTED_PAGE_MODULE_CALLS


@pytest.mark.parametrize(("module", "expected"), STEP_CONSUMER_PARAMS)
def test_each_step_module_imports_its_pages_from_the_barrel(
    module: str,
    expected: frozenset[str],
) -> None:
    """The consumer edge, per step module.

    A step module imports the page class its Java original instantiated as a
    field, and it imports it *from the barrel* rather than from the defining
    module - which is what makes ``notes_steps``'s two classes one import
    line, ``Notes.java:16-17`` having declared ``InventoryP inventoryP`` and
    ``NotesP notesP`` side by side.
    """
    source = step_module_source(module)

    assert names_imported_from(source, "app.pages") == expected
    # No step module reaches around the barrel into a page module, and none
    # imports the base class: a step drives a page object, not the machinery
    # underneath it.
    for name in imported_modules(source):
        assert not name.startswith("app.pages."), name


def test_every_export_has_at_least_one_step_module_consumer() -> None:
    """All ten classes are actually used, so the barrel carries no dead name.

    AAP 0.4.1 makes the barrel's surface *"a name may be added here only when
    a consumer actually imports it"*, and this is the measurement behind that
    rule.
    """
    consumed = frozenset().union(*(names for _, names in STEP_MODULE_CONSUMERS))

    assert consumed == frozenset(BARREL_EXPORTS)
