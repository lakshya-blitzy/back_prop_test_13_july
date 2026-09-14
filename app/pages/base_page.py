r"""Lazy-locator base class - the Python stand-in for Java's ``PageFactory``.

All ten page objects in this package subclass :class:`BasePage`, the whole of
the mechanism replacing the ``PageFactory``/``@FindBy`` pair that has no Python
equivalent: AAP 0.1.1 goal G5 substitutes *"explicit locator constants resolved
lazily"* and AAP 0.4.1 makes this file the single carrier of
``PageFactory.initElements``'s semantics (``LoginP.java:9-11``) for all ten.

A subclass declares upper-case locator constants, each yielding two access
forms: the constant is the ``(By.X, "value")`` tuple that
``app/automation/waits.py`` and the parity tests take, and the lower-case
accessor - a separate name only because Python is case-sensitive - is the live
element a step drives.  The resolution contract, and what it forbids:

* **Per access.**  An accessor performs exactly one ``find_element`` - or
  ``find_elements`` for a :attr:`BasePage.PLURAL_LOCATORS` name - at the
  moment it is read, and construction resolves nothing, which is what lets a
  scenario build a page object before navigating (AAP 0.3.3).
* **No memoization.**  No ``@FindBy`` field in the reference is a
  ``@CacheLookup`` proxy, so each one re-locates on every use; nothing here
  caches an element, on the instance or through ``functools``.
* **No waiting.**  ``Driver.java:34`` and ``:40`` put a 10-second implicit
  wait on every session, which is what retries a lookup; a ``WebDriverWait``,
  retry loop or ``sleep`` here would stack a second timeout on it.
* **No exception handling.**  A missing element surfaces as
  ``NoSuchElementException`` when that window expires, and no accessor returns
  ``None`` in its place.

:attr:`BasePage.driver` resolves per access rather than in ``__init__``, which
is observationally identical to the Java constructor's ``Driver.getDriver()``:
``features/environment.py`` has created the session before any step runs, and
that call's create-on-demand behaviour stays out of construction (AAP 0.3.3).

AAP 0.4.2 caps a page object's imports at ``app.automation``, which is also
what makes the substitutable ``get_driver`` this module's own binding - a test
either injects a driver or patches that name - and admits no browser-library
import even under ``typing.TYPE_CHECKING``, so every element-valued signature
below is annotated ``Any`` and names ``WebElement`` in prose.  Importing this
module has no side effects at all.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.automation import By, get_driver

__all__ = ["BasePage"]

type Locator = tuple[str, str]

#: Every locator strategy the re-exported ``By`` defines, listed uniformly.
#:
#: This set is what makes a locator constant *recognisable*: a class attribute
#: is treated as a locator only when its first element is one of these
#: strategies (see :func:`_is_locator_declaration`), which keeps an incidental
#: two-string tuple - a pair of expected titles, say - from acquiring an
#: accessor it was never meant to have.
#:
#: All eight are named because the mechanism must stay strategy-agnostic: the
#: reference declares five of them (``xpath`` 96 times, ``partial link text``
#: 12, ``name`` 8, ``id`` 8 and ``class name`` 7), and none of the five is
#: special-cased here or anywhere below.  Membership is tested against the
#: strategy *values*, so ``(By.XPATH, ...)`` and ``("xpath", ...)`` are the
#: same declaration - which they are, since ``By.XPATH == "xpath"``.
_LOCATOR_STRATEGIES: frozenset[str] = frozenset(
    {
        By.ID,
        By.XPATH,
        By.LINK_TEXT,
        By.PARTIAL_LINK_TEXT,
        By.NAME,
        By.TAG_NAME,
        By.CLASS_NAME,
        By.CSS_SELECTOR,
    }
)

#: Upper-case class attributes that belong to the mechanism rather than to a
#: page's locator inventory, and so are never mistaken for locators.
#: :attr:`BasePage.PLURAL_LOCATORS` in particular is declared in a subclass
#: body, is upper-case, and would be a locator declaration by shape if it ever
#: held a two-string tuple.
_RESERVED_CLASS_ATTRIBUTES: frozenset[str] = frozenset({"LOCATORS", "PLURAL_LOCATORS"})

#: Attribute names on :class:`BasePage` that an installed accessor must never
#: shadow.  A page declaring ``DRIVER``, ``FIND`` or ``FIND_ALL`` would
#: otherwise replace the mechanism it depends on with an element lookup, and
#: the failure would surface far from its cause; :meth:`BasePage.__init_subclass__`
#: rejects it at class-creation time instead.  No field in the reference comes
#: close to these three names, so this guard costs the ten page modules
#: nothing.
_PROTECTED_ATTRIBUTES: frozenset[str] = frozenset({"driver", "find", "find_all"})


def _is_locator_declaration(name: str, value: object) -> bool:
    """Report whether a class attribute is a locator constant.

    :param name: The attribute's name as written in the class body.
    :param value: The value bound to it.
    :returns: ``True`` when the attribute is a locator constant and so earns
        an accessor and a :attr:`BasePage.LOCATORS` entry, ``False``
        otherwise.

    Four conditions hold at once, which keeps an incidental two-string tuple
    out of the inventory without rejecting anything the ten page modules
    declare:

    1. The name is upper-case by ``str.isupper()``, which ignores digits and
       underscores, so ``PROGRESS_PIPELINE2`` (``CrmP``'s
       ``progressPipeline2``) qualifies while methods and dunders do not.
    2. The name is not private: a page's internals are not locators.
    3. The name is not one of :data:`_RESERVED_CLASS_ATTRIBUTES`.
    4. The value is a two-element tuple of strings whose first element is a
       recognised strategy (:data:`_LOCATOR_STRATEGIES`).

    A list is not accepted in place of a tuple: every locator in the port is
    a tuple literal, and accepting both would leave ``LOCATORS`` mixed in type.
    """
    return (
        name.isupper()
        and not name.startswith("_")
        and name not in _RESERVED_CLASS_ATTRIBUTES
        and isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, str) for part in value)
        and value[0] in _LOCATOR_STRATEGIES
    )


def _build_accessor(
    owner: str,
    constant_name: str,
    locator: Locator,
    *,
    plural: bool,
) -> property:
    """Build the read-only property that resolves one locator constant.

    :param owner: The subclass's name, used only to give the property a
        legible ``__qualname__`` such as ``LoginPage.input_email``.
    :param constant_name: The upper-case constant the property is derived
        from, for example ``INPUT_EMAIL``.
    :param locator: The ``(By.X, "value")`` pair the property resolves.
    :param plural: ``True`` for a constant listed in the class's
        :attr:`BasePage.PLURAL_LOCATORS`, which resolves through
        :meth:`BasePage.find_all` and yields a list; ``False`` for the usual
        case, which resolves through :meth:`BasePage.find` and yields one
        element.
    :returns: A :class:`property` with a getter and **no setter**, so
        assigning to the accessor raises ``AttributeError`` instead of
        silently replacing the lookup with whatever was assigned.

    The locator is captured by closure rather than looked up by name at access
    time, and both branches go through :meth:`BasePage.find` or
    :meth:`BasePage.find_all` rather than reaching for the driver, which keeps
    the no-caching guarantee assertable at a single site.
    """
    accessor_name = constant_name.lower()

    if plural:

        def accessor(self: "BasePage") -> list[Any]:
            return self.find_all(locator)

        documentation = (
            f"Every element matching ``{constant_name}`` = "
            f"``({locator[0]!r}, {locator[1]!r})``, as a list.\n\n"
            "The port of a Java ``List<WebElement>`` field, of which the "
            "reference declares exactly one (``SalesP.java:69``). Resolved "
            "through :meth:`BasePage.find_all` on **every** access and never "
            "cached, so the list is rebuilt from the live DOM each time. An "
            "empty list means nothing matched; no exception is raised for "
            "that, which is the ``find_elements`` contract and the "
            "Java proxy's."
        )
    else:

        def accessor(self: "BasePage") -> Any:
            return self.find(locator)

        documentation = (
            f"The live element matching ``{constant_name}`` = "
            f"``({locator[0]!r}, {locator[1]!r})``.\n\n"
            "A ``WebElement``, resolved through "
            ":meth:`BasePage.find` on **every** access and never cached - the "
            "behaviour of the ``PageFactory`` proxy this replaces. Raises "
            "``NoSuchElementException`` if the element is still absent when "
            "the session's 10-second implicit wait expires, and never returns "
            "``None``."
        )

    accessor.__name__ = accessor_name
    accessor.__qualname__ = f"{owner}.{accessor_name}"
    accessor.__doc__ = documentation

    return property(accessor, doc=documentation)


class BasePage:
    r"""Base of every page object: locator constants in, lazy accessors out.

    The Python replacement for the constructor body all ten reference page
    classes share, ``PageFactory.initElements(Driver.getDriver(), this)``
    (``LoginP.java:10``): a subclass declares upper-case locator constants and
    inherits one read-only accessor per constant, under the lower-case name.

    .. code-block:: python

        class SalesPage(BasePage):
            PLURAL_LOCATORS = frozenset({"ALL_CUSTOMERS"})
            SEARCH_BAR = (By.XPATH, "//div[@class='o_searchview']/input")
            ALL_CUSTOMERS = (By.XPATH, "//div[@class='o_kanban_record']")

        page = SalesPage()            # touches nothing at all
        page.search_bar.click()       # one find_element, right now
        SalesPage.SEARCH_BAR          # the locator tuple, for wait_visible()

    :attr:`LOCATORS` is the resulting ``{constant name: locator}`` inventory -
    immutable, in declaration order, rebuilt per subclass - and
    :attr:`PLURAL_LOCATORS` names the constants resolving to a list.
    :attr:`driver` is the session the next lookup uses, :meth:`find` and
    :meth:`find_all` its two call sites; explicit waits, keyboard input and
    action chains stay in ``app/automation``, timeout supplied per call site.
    """

    LOCATORS: Mapping[str, Locator] = MappingProxyType({})

    #: The names of the locator constants that resolve to a **list** of
    #: elements instead of one element - the port of a Java
    #: ``List<WebElement>`` field.  Empty here, and the reference declares
    #: exactly one such field in the whole suite (``SalesP.java:69``), so
    #: ``app/pages/sales_page.py`` is the only subclass that overrides this,
    #: with ``frozenset({"ALL_CUSTOMERS"})``.  A name listed here that is not
    #: a declared locator constant is rejected at class-creation time rather
    #: than silently resolving to a single element.
    PLURAL_LOCATORS: frozenset[str] = frozenset()

    def __init__(self, driver: Any = None) -> None:
        """Bind this page object to a session, without touching one.

        :param driver: The ``WebDriver`` every lookup from this
            instance should use, or ``None`` - the normal case - to take this
            worker's current session from
            :func:`~app.automation.driver.get_driver` at each access.  Plain
            positional-or-keyword, so ``LoginPage(stub)`` and
            ``LoginPage(driver=stub)`` are equally valid.
        :returns: ``None``.

        The whole of the constructor: the argument is stored and nothing else
        happens.  No element is located, no page is navigated to, no session
        is created, requested, configured or quit, and no property is read -
        so constructing a page object before the browser is anywhere near the
        right page is safe, which AAP 0.3.3 requires and which the Java
        constructor also gave, since ``PageFactory.initElements`` only
        installs proxies.

        The one thing the Java constructor does that this does not is call
        ``Driver.getDriver()``, whose create-on-demand behaviour would make
        construction launch a browser.  AAP 0.3.3 reserves that to
        ``features/environment.py``; the module docstring sets out why
        deferring it changes nothing observable.
        """
        self._driver = driver

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Turn a subclass's locator constants into accessors and an inventory.

        :param kwargs: Class-creation keyword arguments, forwarded untouched
            to :meth:`object.__init_subclass__` so this hook composes.
        :returns: ``None``.
        :raises ValueError: If :attr:`PLURAL_LOCATORS` names something that is
            not a declared locator constant, or if an accessor name would
            shadow an existing attribute - the mechanism's ``driver``,
            ``find`` or ``find_all``, or a name the subclass itself defines.

        This runs once per subclass, when the ``class`` statement completes -
        the moment ``PageFactory.initElements`` corresponds to.  In order:

        1. Collect the constants inherited from ancestors, oldest first, then
           those declared in this class's own body.  Nothing is sorted, so
           :attr:`LOCATORS` keeps the declaration order the parity tests
           compare against the Java ``@FindBy`` order.  No page here subclasses
           another; the ancestor merge exists so inheritance is not broken.
        2. Validate :attr:`PLURAL_LOCATORS` against those constants, so a
           mistyped name fails here instead of quietly resolving to a single
           element at some later step.
        3. Install one read-only property per constant, rejecting a name that
           would shadow something.
        4. Publish the inventory as an immutable mapping.
        """
        super().__init_subclass__(**kwargs)

        locators: dict[str, Locator] = {}

        # Ancestors first, base-most to nearest, so a re-declared constant in
        # a nearer class wins - the ordinary attribute-lookup outcome. Each
        # ancestor's own LOCATORS is read from its ``__dict__`` rather than
        # through ``getattr``, which would return an inherited mapping and
        # merge the same entries repeatedly.
        for ancestor in reversed(cls.__mro__[1:]):
            inherited = ancestor.__dict__.get("LOCATORS")
            if inherited:
                locators.update(inherited)

        locators.update(
            {
                name: value
                for name, value in vars(cls).items()
                if _is_locator_declaration(name, value)
            }
        )

        # Read through the class so a subclass's own declaration is honoured
        # and, when it declares none, the inherited default applies. Accepting
        # any iterable of names keeps a plain ``set`` literal working; the
        # membership check below is what actually protects the mechanism.
        plural_names = frozenset(cls.PLURAL_LOCATORS)
        undeclared = sorted(plural_names.difference(locators))
        if undeclared:
            raise ValueError(
                f"{cls.__name__}.PLURAL_LOCATORS names "
                f"{undeclared}, which are not locator constants of this "
                f"class. Declared locator constants: "
                f"{sorted(locators)}."
            )

        for constant_name, locator in locators.items():
            accessor_name = constant_name.lower()

            # Two shadowing hazards, one message: an accessor that replaced
            # ``driver``, ``find`` or ``find_all`` would break the very
            # mechanism it resolves through, and one that replaced a method
            # defined in this body - the reference's only page method is
            # ``EmployeeP.login()`` - would silently delete behaviour, since
            # the assignment below happens after the class body has run.
            if accessor_name in _PROTECTED_ATTRIBUTES or accessor_name in vars(cls):
                raise ValueError(
                    f"Locator constant {cls.__name__}.{constant_name} would "
                    f"install an accessor named {accessor_name!r}, which "
                    f"shadows an existing attribute of that name. Rename the "
                    f"constant or the attribute."
                )

            setattr(
                cls,
                accessor_name,
                _build_accessor(
                    cls.__name__,
                    constant_name,
                    locator,
                    plural=constant_name in plural_names,
                ),
            )

        cls.LOCATORS = MappingProxyType(locators)

    @property
    def driver(self) -> Any:
        """The ``WebDriver`` the next lookup will use.

        :returns: The driver injected at construction if there was one,
            otherwise this worker's current session from
            :func:`~app.automation.driver.get_driver` - which is ``None`` when
            no session exists, because ``Driver.java:29-42`` has no default
            branch and ``Driver.java:45`` returns whatever the slot holds.

        Resolved on every access rather than once, which matters in the normal
        case: ``features/environment.py`` quits the session after each
        scenario and creates a fresh one for the next, so a page object that
        had captured the driver would hand its locators to a dead session.
        Reading it per access means a page object built at any time always
        resolves against the session that is live *now*.

        Passing the ``None`` through unchanged is deliberate. The Java code
        dereferences the same ``null`` and fails at the point of use, and AAP
        0.4.1 keeps that: an unrecognised ``browser`` value *"fails at first
        driver use, as today"*. Substituting a driver, raising a friendlier
        error or reading configuration here would each replace that behaviour
        with something the source does not do.
        """
        if self._driver is not None:
            return self._driver

        return get_driver()

    def find(self, locator: Locator) -> Any:
        """Locate one element, now.

        :param locator: A ``(By.X, "value")`` pair, normally one of this
            class's locator constants.
        :returns: The matching ``WebElement``.
        :raises Exception: Whatever the driver raises - typically
            ``NoSuchElementException`` once the session's 10-second implicit
            wait expires, or ``AttributeError`` if no session exists at all
            (see :attr:`driver`). Nothing is caught, wrapped or retried here.

        The single ``find_element`` call site in this package, and the funnel
        every singular accessor goes through. Holding it to one line and one
        place is what makes the no-caching guarantee checkable: there is
        exactly one place a cache could ever live, and it does not.

        Step modules may call this directly with an ad-hoc locator, which is
        the port of a Java step that built a ``By`` inline rather than using a
        page field (``LoginSD.java:56``).
        """
        return self.driver.find_element(*locator)

    def find_all(self, locator: Locator) -> list[Any]:
        """Locate every matching element, now.

        :param locator: A ``(By.X, "value")`` pair, normally one of this
            class's locator constants.
        :returns: A list of matching ``WebElement`` objects, empty when
            nothing matches - the ``find_elements`` contract, and the Java
            ``List<WebElement>`` proxy's.
        :raises Exception: Whatever the driver raises; as with :meth:`find`,
            nothing is caught or retried here.

        The single ``find_elements`` call site in this package. It exists
        because the reference declares exactly one ``List<WebElement>`` field,
        ``SalesP.java:69``, reached through
        ``PLURAL_LOCATORS = frozenset({"ALL_CUSTOMERS"})`` on
        ``app/pages/sales_page.py``.
        """
        return self.driver.find_elements(*locator)
