r"""Notes page object - the port of ``NotesP.java``.

The ten ``@FindBy`` fields of ``NotesP.java:14-42``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order.  The Java class declares no
method beyond its constructor, so this module declares constants only:
``NotesPage.SAVE_BTN`` is the tuple a ``wait_visible(locator, timeout)`` call
takes and ``page.save_btn`` the element, re-resolved on every access.

``features/steps/notes_steps.py``, the port of ``Notes.java``, is the only
consumer and reaches all ten.  It imports
:class:`~app.pages.inventory_page.InventoryPage` as well, because
``Notes.java:16-17`` declares ``InventoryP`` beside ``NotesP`` and
``Notes.java:65`` clicks ``inventoryP.saveBtn``; no locator moves between the
two pages (AAP 0.8).

Five source quirks are carried verbatim under AAP 0.2.2 and AAP 0.8's
*"Preserve, do not tidy"*: :attr:`~NotesPage.TABINDEX` keeps the spaces around
its ``=`` (``//a[. = 'Create and Edit...']``) where ``SalesP.java:35`` writes
the same affordance without them on a different element, so the two pages share
no constant; ``tabindex`` names that anchor rather than any tab index, and the
accessor keeps the name; ``tagsN`` and ``appK`` keep their abbreviations;
:attr:`~NotesPage.CREATING_NOTES` matches a hyphenated Odoo class where
:attr:`~NotesPage.SAVE_BTN` matches an underscored one; and
:attr:`~NotesPage.NEW_TABLE` and :attr:`~NotesPage.TODAY_TABLE` hard-code record
ids ``1193`` and ``1194`` and index *different* children, ``[2]`` and ``[1]``.

Per AAP 0.4.2 the imports are :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, no configuration, and neither interaction
helper (``notes_steps.py`` owns the ``Keys.ENTER`` send at ``Notes.java:35``
and the drag chain at ``:79``).  The 20-second wait (``Notes.java:19``) and the
one 2-second delay (``:23``) stay at their step call sites per AAP 0.4.1, and
importing this module has no side effects: it never creates or quits a driver
(AAP 0.3.3).  ``features/Notes.feature`` carries no tag, holds three scenarios,
and its mis-titled header - "Feature: Testinium app login feature" - is
preserved, so it shares a JSON ``id`` with ``Login.feature`` (AAP 0.2.2).
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["NotesPage"]


class NotesPage(BasePage):
    r"""The Notes module's ten elements, in ``NotesP.java`` declaration order.

    A pure declaration: no ``__init__``, no hand-written property and no
    method, because ``NotesP.java`` has none beyond the constructor whose job
    :class:`~app.pages.base_page.BasePage` already performs.  Each constant
    below carries the ``NotesP.java`` line and field name it ports, and yields
    a lower-case accessor - ``page.tabindex`` through ``page.today_table`` -
    that resolves its element on every access and caches nothing.

    :attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS` is not overridden:
    ``NotesP.java`` declares no ``List<WebElement>`` field, so all ten
    accessors are singular.  The reference's only plural field is
    ``SalesP.java:69``.

    Usage, with the 20-second timeout ``notes_steps.py`` supplies::

        notes = NotesPage()                 # touches nothing at all
        notes.notes_module.click()          # one find_element, right now
        wait_visible(NotesPage.SAVE_BTN, 20)
        notes.save_btn.click()
    """

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

    #: ``NotesP.java:17-18``'s ``notesModule``, the one non-XPath locator here.
    NOTES_MODULE = (By.PARTIAL_LINK_TEXT, "Notes")

    #: ``NotesP.java:20-21``'s ``creatingNotes``, hyphenated as written.
    CREATING_NOTES = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new']",
    )

    #: ``NotesP.java:23-24``'s ``tagsN``, abbreviated as written.
    TAGS_N = (By.XPATH, "//input[@class='o_input ui-autocomplete-input']")

    #: ``NotesP.java:26-27``'s ``description``.
    DESCRIPTION = (By.XPATH, "//div[@class='note-editable panel-body']")

    #: ``NotesP.java:29-30``'s ``createdMessage``, with no spaces around its
    #: ``=`` where :attr:`TABINDEX` has them.
    CREATED_MESSAGE = (By.XPATH, "//p[.='Note created']")

    #: ``NotesP.java:32-33``'s ``saveBtn``, underscored as written.
    SAVE_BTN = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
    )

    #: ``NotesP.java:35-36``'s ``appK``, abbreviated as written.
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
