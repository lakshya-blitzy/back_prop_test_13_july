r"""Lazy-locator base class - the Python stand-in for Java's ``PageFactory``.

Every one of the ten page objects in this package subclasses :class:`BasePage`,
so this module is the whole of the mechanism that replaces Java's
``PageFactory``/``@FindBy`` pair.  That pair has no Python equivalent, which is
why AAP 0.1.1 goal G5 replaces it with *"explicit locator constants resolved
lazily"* and AAP 0.4.1 gives this file its one-line job: it *"carries
``PageFactory.initElements``'s lazy-resolution semantics (``LoginP.java:9-11``)
for all ten page objects."*

The Java pattern being ported, in the words of the anchor
--------------------------------------------------------
All ten classes in ``src/main/java/com/testinium/pages`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240`` - held REFERENCE by AAP 0.2.1 and
never modified - share the shape of ``LoginP.java:8-14``:

.. code-block:: java

    public class LoginP {
        public LoginP(){
            PageFactory.initElements(Driver.getDriver(), this);
        }
        @FindBy(name = "login")
        public WebElement inputEmail;

``initElements`` does **not** look anything up.  It replaces each annotated
field with a proxy that re-runs ``findElement`` on every method invocation, so
resolution happens **on access, not at construction**.  AAP 0.3.3 states the
consequence this module must not break: *"a Python page object that called
``find_element`` in ``__init__`` would change when elements are looked up and
break scenarios that build a page before navigating."*

The Python shape, and the two access forms
------------------------------------------
A subclass declares locator constants and nothing else:

.. code-block:: python

    class LoginPage(BasePage):   # ``By`` comes from ``app.automation``
        INPUT_EMAIL = (By.NAME, "login")
        BUTTON = (By.XPATH, "//button[.='Log in']")

and both of these then work, on the class and on an instance:

======================================  =======================================
Access form                             Yields
======================================  =======================================
``LoginPage.INPUT_EMAIL``               the ``(By.NAME, "login")`` **tuple**,
``page.INPUT_EMAIL``                    for ``wait_visible(locator, timeout)``,
                                        ``press_keys(locator, ...)`` and the
                                        per-module parity tests
``page.input_email``                    the live **element**, re-resolved on
                                        every access, for ``.click()``,
                                        ``.send_keys()``, ``.is_displayed()``,
                                        ``.get_attribute()`` and
                                        ``wait_visible_element(element, ...)``
======================================  =======================================

Both forms are required rather than convenient: ``app/automation/waits.py``
exposes ``wait_visible(locator, timeout)`` *and*
``wait_visible_element(element, timeout)``, and
``app/automation/interactions.py``'s ``press_keys`` accepts either a resolved
element or a ``(By.X, "value")`` pair.  A Java step class reached both shapes
off the same field - ``loginP.inputEmail`` for the element and ``By.name(...)``
for a locator - and the two Python names coexist without collision purely
because Python is case-sensitive: the constant is ``INPUT_EMAIL``, the accessor
is ``input_email``.

Resolution semantics: resolve every time, cache nothing, wait nowhere
--------------------------------------------------------------------
* **No caching.**  ``grep -rn CacheLookup`` over the reference sources returns
  zero hits, so no ``@FindBy`` field in the suite is a cached proxy and every
  one of them re-locates on each use.  Memoizing a found element - on the
  instance, in a dict, through ``functools.cached_property``, anywhere - would
  be a behaviour change, so :meth:`BasePage.find` and
  :meth:`BasePage.find_all` are the single funnel and they hold no state at
  all.  ``tests/test_base_page.py`` asserts this by counting calls: three
  accesses of one accessor must produce three ``find_element`` calls.
* **No waiting here.**  ``Driver.java:34`` and ``:40`` set a 10-second
  implicit wait on every session, and that is what already makes a lookup
  retry until the element appears.  A ``WebDriverWait``, a retry loop or a
  sleep in this module would stack a second timeout on top of it and change
  how long a failing step takes.  Explicit waits are a separate concern with a
  per-call-site timeout, and they live in ``app/automation/waits.py`` because
  the source fixes a different timeout per step class.
* **No exception handling.**  A missing element must surface as
  ``NoSuchElementException`` after the implicit-wait window, exactly as the
  Java proxy raises it.  Nothing here catches, wraps, retries or logs it, and
  no accessor ever returns ``None`` in place of an element - a caller that got
  ``None`` would fail later, at a place that no longer names the locator.

The driver seam
---------------
``__init__`` stores its argument and does nothing else.  It deliberately does
**not** call :func:`~app.automation.driver.get_driver`, even though the Java
constructor calls ``Driver.getDriver()``: that call *creates a browser session
on demand*, so reproducing it here would mean that merely constructing a page
object launches a browser.  AAP 0.3.3 assigns that ownership elsewhere - *"One
owner for the lifecycle, so no step or page ever creates or quits a driver"* -
and ``features/environment.py`` has already created the session in
``before_scenario`` before any step runs.  Deferring the lookup to
attribute-access time is therefore observationally identical to the Java
behaviour and compliant with the ownership rule at the same time.

The :attr:`~BasePage.driver` property resolves per access: an injected driver
when one was passed to the constructor, otherwise this worker's session from
:func:`~app.automation.driver.get_driver`.  ``get_driver()`` may hand back
``None`` - ``Driver.java:29-42`` has no default branch, so an unrecognised
``browser`` value leaves the slot empty and ``Driver.java:45`` returns that
``null`` - and this module passes that straight through, which reproduces the
source's *"fails at first driver use"* behaviour rather than second-guessing
it.

Two substitution seams exist, and the first is the one to prefer:

1. **Injection** - ``LoginPage(stub)`` or ``LoginPage(driver=stub)``.  A plain
   positional-or-keyword parameter, so both spellings work.  This is what the
   ten ``tests/test_steps_<area>.py`` modules use to *"drive the module
   against a stubbed driver"* (AAP 0.4.1), and it mirrors the ``driver=None``
   seam that ``app/automation/waits.py`` and ``interactions.py`` already
   adopted.
2. **Module-global substitution** - monkeypatching
   ``app.pages.base_page.get_driver``.  Because AAP 0.4.2 fixes the import
   form as ``from app.automation import ...``, the substitutable name is the
   binding *in this module*, which is the same convention
   ``app/automation/driver.py`` documents for ``interactions.py``: the callee
   is *"read through the module global so a test can substitute it"*.
   Patching ``app.automation.get_driver`` instead rebinds a different name and
   this module will not see it.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else"*, and that package is the only one in the port permitted to import the
browser-automation library at all.  So this module imports exactly two names
from the application - :data:`~app.automation.By` and
:func:`~app.automation.get_driver` - plus three standard-library modules, and
nothing further: no browser-library import of any kind, not even under
``typing.TYPE_CHECKING``, because a guarded import is still an import
statement and would trip the grep-based boundary check the suite performs; no
``app.config``, because page objects never read configuration - step modules
do, which is where the Java classes read it; and no service, no reporting
writer, no path helper, no web framework and no Gherkin engine.

The cost of refusing the guarded import is annotation precision, and it is
paid deliberately: every element-valued signature below is annotated ``Any``
and names its real type - a ``WebElement`` - in prose instead.
``app/automation/waits.py`` may annotate precisely because it is inside the
boundary; this module is not.

What this module deliberately does not contain
----------------------------------------------
Each omission is the contract, not an oversight, and AAP 0.8's *"Preserve, do
not tidy"* is why:

* **No locator constants of its own.**  :class:`BasePage` is mechanism; the
  ten subclasses hold all 131 locators the reference declares.
* **No navigation helper** - no ``open()``, ``goto()`` or ``load()``.  None of
  the ten Java page classes has one; navigation is a step's job, done through
  the driver the step already holds.
* **No wait wrapper, no assertion helper, no screenshot capture, no logging of
  element lookups.**  The only behaviour method in the entire Java page
  package is ``EmployeeP.login()`` (``EmployeeP.java:59-69``), and it belongs
  to ``app/pages/employee_page.py``.  Adding a convenience here would be an
  addition the request never asked for, and every page object in the port
  would inherit it.
* **No path literal, no configuration read, no environment-variable read.**
  ``app/utils/paths.py`` owns paths and ``app/config.py`` owns configuration.

Importing this module has no side effects whatever: it starts no browser,
provisions no driver binary, reads no ``configuration.properties``, touches no
filesystem and configures no logging, which is what lets the unit suite import
it on a machine with no browser installed.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.automation import By, get_driver

__all__ = ["BasePage"]

#: The locator shape a page constant holds and every element lookup expects:
#: a ``(By.X, "value")`` pair.  Spelled identically to the alias in
#: ``app/automation/waits.py`` so a locator reads the same on both sides of the
#: page/automation boundary, and kept out of :data:`__all__` for the same
#: reason that module keeps its own out - the alias exists to make the
#: signatures below legible, not to widen this module's surface.
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

    The test is structural and needs four things to hold at once, which
    together make a false positive implausible without rejecting anything the
    ten page modules legitimately declare:

    1. The name is upper-case - ``str.isupper()``, which ignores digits and
       underscores, so ``INPUT_EMAIL`` and ``PROGRESS_PIPELINE2`` both qualify
       (the latter is the port of the reference's only digit-bearing field,
       ``CrmP``'s ``progressPipeline2``).  Methods, ordinary attributes and
       dunders are all excluded by this alone.
    2. The name is not private.  A leading underscore marks a page's own
       internal, and internals are not part of the locator inventory.
    3. The name is not one of the mechanism's own (:data:`_RESERVED_CLASS_ATTRIBUTES`).
    4. The value is a two-element tuple of strings whose first element is a
       recognised strategy (:data:`_LOCATOR_STRATEGIES`).

    A list is not accepted where a tuple is expected, because every locator in
    the port is written as a tuple literal and accepting both would make
    ``LOCATORS`` inconsistent in type for no gain.
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
    time: one indirection fewer per element, and a subclass that re-declares
    an inherited constant gets a fresh property for its own value anyway,
    because :meth:`BasePage.__init_subclass__` installs one for every constant
    the subclass ends up with.

    Both branches funnel through the two lookup methods rather than reaching
    for the driver themselves, which is what keeps the no-caching guarantee
    assertable at a single site.
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
    (``LoginP.java:10``).  A subclass declares upper-case locator constants and
    inherits, for each of them, a read-only accessor under the lower-case name
    that resolves the element on every access:

    .. code-block:: python

        class SalesPage(BasePage):        # ``By`` from ``app.automation``
            PLURAL_LOCATORS = frozenset({"ALL_CUSTOMERS"})

            SEARCH_BAR = (By.XPATH, "//div[@class='o_searchview']/input")
            ALL_CUSTOMERS = (By.XPATH, "//div[@class='o_kanban_record']")

        page = SalesPage()            # touches nothing at all
        page.search_bar.click()       # one find_element, right now
        len(page.all_customers)       # one find_elements, right now
        SalesPage.SEARCH_BAR          # the locator tuple, for wait_visible()

    Class surface
    -------------
    ====================  ==================================================
    Name                  Role
    ====================  ==================================================
    :attr:`LOCATORS`      Immutable ``{constant name: locator}`` mapping in
                          declaration order, built per subclass
    :attr:`PLURAL_LOCATORS`  Names of the constants that resolve to a list
    :attr:`driver`        The session used for the next lookup, per access
    :meth:`find`          The one ``find_element`` call site
    :meth:`find_all`      The one ``find_elements`` call site
    ====================  ==================================================

    Everything else a page needs - explicit waits, keyboard input, action
    chains - stays in ``app/automation`` with its timeout supplied at the call
    site, so this class holds no behaviour beyond resolution.
    """

    #: This class's locator inventory: ``{constant name: (By.X, "value")}``,
    #: in the order the constants appear in the class body, wrapped in a
    #: :class:`~types.MappingProxyType` so a caller enumerating it cannot
    #: alter it.  Empty on :class:`BasePage` itself, which declares no
    #: locators of its own, and rebuilt for each subclass by
    #: :meth:`__init_subclass__`.  ``tests/test_pages.py`` enumerates it to
    #: assert each page's inventory against the ``@FindBy`` fields of the Java
    #: class it ports - 131 locators across the ten pages.
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
        # Deliberately the only statement, and deliberately not normalized:
        # a falsy-but-not-None driver stub stays exactly what the caller
        # passed, because `is not None` is what the `driver` property tests.
        self._driver = driver

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Turn a subclass's locator constants into accessors and an inventory.

        :param kwargs: Class-creation keyword arguments, forwarded untouched
            to :meth:`object.__init_subclass__` so this hook composes with any
            other that a subclass might introduce.
        :returns: ``None``.
        :raises ValueError: If :attr:`PLURAL_LOCATORS` names something that is
            not a declared locator constant, or if an accessor name would
            shadow an existing attribute - either part of this class's
            mechanism (``driver``, ``find``, ``find_all``) or a name the
            subclass itself defines, such as a method.

        This runs once per subclass, when the ``class`` statement completes -
        the moment ``PageFactory.initElements`` corresponds to, and the last
        moment before any instance exists.  In order:

        1. Collect the locator constants inherited from ancestors, oldest
           first, then the ones declared in this class's own body.  Class
           ``__dict__`` iteration preserves declaration order and nothing is
           sorted, so :attr:`LOCATORS` reads in source order - which is how
           ``tests/test_pages.py`` compares a page against the ``@FindBy``
           order of the Java class it ports.  No page in this package
           subclasses another, so in practice the inherited part is empty; it
           is merged anyway so that inheritance is not actively broken.
        2. Validate :attr:`PLURAL_LOCATORS` against those constants, so a
           mistyped name fails loudly here instead of quietly resolving to a
           single element at some later step.
        3. Install one read-only property per constant, and reject a name that
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

        # ``locators`` is local and now unreferenced elsewhere, so the proxy
        # is the only handle on it and the inventory is immutable in practice
        # as well as by type.
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

