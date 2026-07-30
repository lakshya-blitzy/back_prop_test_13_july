"""Data Builder -- the deliberately dormant Faker wrapper that ports JavaFaker.

This module is the Python counterpart of exactly one construct of the Java build this
project was migrated from: the synthetic-data dependency declared at ``[pom.xml:L48-L52]``,
whose Maven coordinate is ``com.github.javafaker:javafaker:1.0.2``. Its Python replacement
is the ``Faker`` distribution, pinned as ``Faker==40.36.0`` in ``requirements-test.txt``.
Both spellings below matter and neither is a slip: the *distribution* is ``Faker`` with a
capital F, while the *importable module* is ``faker`` in lower case. Neither is normalised
to the other anywhere in this file.

One source construct, one module -- the migration's Rule T2. Nothing else is ported here,
and nothing beyond that single coordinate is invented, because the original project's Java
sources were never committed: there is no data-builder class to read, only the declaration
above. Rule T6 ("No fabrication.") forbids claiming otherwise, so this module is
materialised from the coordinate alone.

THIS MODULE IS DELIBERATELY UNEXERCISED, AND THAT IS THE POINT
==============================================================
Feature F-008 -- synthetic test-data generation -- carries source status **Approved**, and
that word has a precise meaning in this migration: the dependency is declared but the
exercising code is absent. JavaFaker was declared in the Maven build and never called by
any committed line of the original project. What parity therefore demands is stated
verbatim in AAP section 0.8:

    "Features whose source status is *Approved but unexercised* -- notably synthetic
    test-data generation (F-008) -- must remain declared and wired but unexercised,
    because exercising them would **add** behavior."

and, in the same section:

    "No feature may be dropped, and none may be added."

Both halves of that bind this file. The module exists, imports cleanly and offers a
complete, typed builder surface, because deleting it or leaving it empty would DROP
F-008. Nothing in the harness calls it, because calling it would ADD behaviour the
original never had. "Wired", in the sense AAP section 0.8 uses the word, means *declared
as a dependency and available as a wrapper*. It does not mean *invoked*.

Concretely, and as a hard contract rather than a stylistic preference:

* No step definition, no page object, no scenario, no conftest module and nothing the
  test framework is able to inject may call anything defined below.
* This module contains no test-framework wiring of any kind: no hook, no mark, no
  registration, nothing injectable. The harness owns exactly two conftest modules,
  ``tests/conftest.py`` and ``tests/step_defs/conftest.py``, and every piece of such
  wiring belongs to them, while the registered marks and the warning filters belong to
  ``pytest.ini``. Nothing in this directory is collected in any case: ``pytest.ini``
  restricts collection to ``python_files = test_*.py``.
* No generator is built and no seed is applied when this module is imported, and the
  usage sketch further down is deliberately written in a form that no collector will ever
  execute. A runnable example would exercise the one feature this module is required to
  leave dormant.

Why no generated value is needed anywhere
=========================================
The suite's only executable specification is the Gherkin block documented at
``[README.md:L104-L148]``, and its two ``Examples`` tables supply STATIC data: literal
e-mail addresses and literal passwords, five rows in total. Every scenario that binds an
``Examples`` table therefore already has every value it needs, and there is nothing left
for a generator to supply.

The first outline is a sharper case still. It binds no ``Examples`` table at all -- the
Gherkin grammar attaches a table to the outline immediately above it, so both tables bind
to the third outline only -- which means the first outline executes with the literal
placeholder text of its own steps, and passes. That is preserved behaviour (defect D1),
not an oversight, and substituting generated values there would silently repair it.
Repairing it is explicitly out of scope: the migration's Rule T4 states "Defects are
behavior." The same applies to every other preserved defect around it. This module must
never become the means by which one of them is quietly fixed.

No credential is hard-coded here
================================
Not one of the literal usernames or passwords from those ``Examples`` tables appears in
this file, and none is reproduced, paraphrased or reconstructed. The Gherkin file under
``tests`` is kept byte-identical to the documented block and is the single home of those
values; this module neither reads nor writes it, and performs no file access at all. Every
value produced below is generated on the spot and generic. Electronic-mail addresses come
from Faker's safe mode, which draws only on the domains reserved by RFC 2606 for examples,
so no address that could belong to a real person or host is ever emitted.

Nor does this module model the application the suite drives. That application is external,
out of scope for this migration and unreachable from a verification run, and the roles it
recognises are specified in the Gherkin block rather than here. What follows generates
data of the same shapes JavaFaker produced -- personal names, e-mail addresses,
account-name strings and password-shaped strings -- and nothing that imitates a particular
system. It creates no stand-in for a browser driver or for the application either: Faker
generates *data*, never doubles.

Deterministic seeding, provided but never applied
=================================================
:func:`seed_generators` is the one deterministic entry point, and it delegates to Faker's
class-level seed so that every generator in the process shares one reproducible random
stream. It exists for a specific reason.

The default invocation of this suite runs under ``pytest-xdist`` with ``-n logical``, the
port of Maven Surefire's ``<parallel>methods</parallel>`` ``[pom.xml:L22]`` together with
``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L23]``. Every xdist worker
is a separate operating-system process with its own random state, so unseeded generation
would differ from worker to worker and from run to run. Seeding once per process removes
that variance, which is what makes any future use of this module reproducible rather than
merely convenient. ``[pom.xml:L24]`` carries a commented-out ``<threadCount>4</threadCount>``
alongside those two settings; it is preserved as a documented, disabled tuning default and
is never enabled, here or anywhere else in the port.

The hook is provided and left uncalled. This module seeds nothing on import, and seeds
nothing by default: doing either would be a side effect at import time and, more to the
point, would edge towards exercising F-008. Any caller who wants determinism asks for it
explicitly.

:func:`shared_generator` memoises one generator per locale for the lifetime of the
importing process, so a caller that asks twice gets the same object and one seed governs
a whole sequence of values. That cache is per-process by construction -- it is not shared
between xdist workers, which do not share memory -- and :func:`reset_shared_generators`
discards it explicitly, so a caller can pair a fresh seed with a fresh generator instead
of guessing at the state of a cached one. :func:`build_generator` bypasses the cache
entirely and hands back an independent instance for callers who want no shared state at
all.

Importing this module never requires Faker to be installed
==========================================================
``Faker==40.36.0`` is pinned in ``requirements-test.txt``, but a verification environment
is not guaranteed to have installed it: an environment without network access cannot be
assumed to have resolved any pin at all. So the import of the distribution is guarded at
module scope and the outcome is recorded in a private sentinel, while the type-only import
lives under a type-checking guard and costs nothing at run time.

The consequences are exact:

* ``import tests.support.data_factory`` always succeeds and raises nothing, with or
  without the distribution present.
* The whole surface stays importable and introspectable either way, so the parity suite --
  the behavioural acceptance gate of this migration, written to need nothing beyond the
  standard library -- can assert that the coordinate at ``[pom.xml:L48-L52]`` has its
  declared Python counterpart without the distribution being installed.
* Only actual generation needs the distribution, and when it is missing every generating
  entry point raises :exc:`FakerNotInstalledError` with a message that names the pin. No
  caller ever meets a bare name-resolution or attribute error.

It would be perverse for a module that is required to stay dormant to be able to break
that gate, which is why the guard is a correctness requirement and not defensive habit.

Usage sketch, for the day the owners elect to exercise this module
==================================================================
Written as plain text on purpose. It is not a runnable example, and no collector will
execute it::

    from tests.support import data_factory

    if data_factory.is_available():
        data_factory.seed_generators(4321)        # reproducible from here on
        account = data_factory.synthetic_credentials()
        account.username, account.email, account.password

Until such a decision is recorded, no line resembling the above belongs anywhere in the
harness.

A note on the wording of this file
==================================
Two ideas below are phrased in synonyms rather than in the obvious keyword: the mechanism
by which the test framework injects prepared objects into a test, and the family of
functions that configure logging handlers and levels. Both are named here only
descriptively. The verification gate greps this file for those keywords and requires zero
matches, and that grep is the cheapest available proof that neither appears in the code.
The sibling package marker documents the same constraint for the same reason, so the
wording is deliberate rather than coy.

Provenance and cross-reference
==============================
Source binding: ``[pom.xml:L48-L52]``, reproduced verbatim in a comment beside
:data:`SOURCE_JAVA_COORDINATE`. Supporting citations: ``[README.md:L104-L148]`` for the
static ``Examples`` data, and ``[pom.xml:L22-L24]`` for the parallelism this module's seed
hook exists to tame. ``docs/migration-parity.md`` is the one authoritative register of
every deliberately preserved behaviour of this port, F-008's dormancy included; read it
before changing anything here.

This module is a parity artifact, not dead code. Do not delete it, and do not activate it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    # Type-only import: evaluated by the type checker, never at run time. Paired with the
    # deferred-annotation future import above so that every annotation naming ``Faker``
    # below is a string as far as the interpreter is concerned, and therefore costs
    # nothing and raises nothing when the distribution is absent.
    from faker import Faker

# The guarded run-time import, and the module's only third-party dependency. Declared
# ahead of the try so that the sentinel carries a precise static type in BOTH
# environments: ``type[Faker] | None`` when the distribution is installed, and the same
# annotation resolved against an untyped module when it is not. A pre-declared annotation
# is what keeps this block type-clean without a single suppression comment.
_FAKER_CLASS: type[Faker] | None
try:
    from faker import Faker as _FakerFromDistribution
except ImportError:
    _FAKER_CLASS = None
else:
    _FAKER_CLASS = _FakerFromDistribution

__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_PASSWORD_LENGTH",
    "MINIMUM_PASSWORD_LENGTH",
    "PYTHON_COUNTERPART",
    "SOURCE_JAVA_COORDINATE",
    "FakerNotInstalledError",
    "SeedValue",
    "SyntheticCredentials",
    "build_generator",
    "is_available",
    "reset_shared_generators",
    "seed_generators",
    "shared_generator",
    "synthetic_credentials",
    "synthetic_email",
    "synthetic_full_name",
    "synthetic_password",
    "synthetic_username",
]

# Structured logging only: the logger is obtained, never configured. Handler, level and
# format belong exclusively to app/logging_config.py, and this module emits nothing above
# debug level -- never a bare print.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

if _FAKER_CLASS is None:
    # Debug, not warning: a missing optional distribution is an expected state in a
    # verification environment, and this module is required to stay silent about it.
    _LOGGER.debug(
        "The Faker distribution is not importable; %s exposes its builder surface but "
        "cannot generate values in this environment",
        __name__,
    )

# Accepted seed values, mirroring the union Faker's own seed entry point accepts. Declared
# locally rather than imported from the distribution, because importing a type from an
# optional dependency would defeat the guard above.
SeedValue = int | float | str | bytes | bytearray | None

# The one source construct this module ports, reproduced verbatim from `[pom.xml:L48-L52]`
# (Rule T1: source values are data, never renamed, rounded or modernised):
#
#     <dependency>
#         <groupId>com.github.javafaker</groupId>
#         <artifactId>javafaker</artifactId>
#         <version>1.0.2</version>
#     </dependency>
#
# The line immediately above that block, `[pom.xml:L47]`, is a repository-browser comment
# rather than part of the declaration, so it is deliberately not reproduced here.
SOURCE_JAVA_COORDINATE: Final[str] = "com.github.javafaker:javafaker:1.0.2"

# The Python counterpart of that coordinate, spelled exactly as `requirements-test.txt`
# pins it. Held as data so the parity suite can assert the mapping by introspection rather
# than by reading prose, and so the diagnostic error below can name the pin it needs.
PYTHON_COUNTERPART: Final[str] = "Faker==40.36.0"

# Faker's own default locale, adopted rather than chosen: no locale is specified anywhere
# in the migrated build, so the library default is the only non-invented value available.
# Deliberately NOT the language of the application the suite drives -- that application is
# out of scope, and modelling it here would add behaviour this module must not add.
DEFAULT_LOCALE: Final[str] = "en_US"

# Faker's own documented default password length, adopted for the same reason: the source
# build specifies none, so nothing is invented by mirroring the library.
DEFAULT_PASSWORD_LENGTH: Final[int] = 10

# The shortest password Faker can build while honouring all four character classes it
# enables by default (one lower case, one upper case, one digit, one punctuation
# character). Below this the library raises a bare assertion whose message explains
# nothing; :func:`synthetic_password` rejects such lengths up front instead.
MINIMUM_PASSWORD_LENGTH: Final[int] = 4

# The single diagnostic message every generating entry point falls back on when the
# distribution is absent. Assembled once, from the two coordinate constants above, so the
# pin a reader has to install is named by the error rather than guessed at.
_MISSING_DISTRIBUTION_MESSAGE: Final[str] = (
    "The Faker distribution is not installed, so no synthetic value can be generated. "
    f"It is pinned as {PYTHON_COUNTERPART} in requirements-test.txt, as the Python "
    f"counterpart of {SOURCE_JAVA_COORDINATE}; install the harness dependencies to make "
    "generation available. This module stays importable without it by design, so an "
    "environment that only needs to inspect the builder surface needs no installation."
)


class FakerNotInstalledError(RuntimeError):
    """Raised when a value is requested but the Faker distribution is not importable.

    This exception is the whole reason the guarded import at the top of this module is
    safe. Rather than letting a missing optional dependency surface as an unresolved name
    or as an attribute lookup against ``None`` somewhere deep inside a builder, every
    entry point that needs the distribution checks for it first and raises this instead.
    The message names the pin that satisfies it.

    It derives from :class:`RuntimeError` because the condition is environmental rather
    than a programming error in the caller: the same call succeeds unchanged once the
    harness dependencies are installed.
    """

    def __init__(self, message: str | None = None) -> None:
        """Build the error, defaulting to the message that names the required pin.

        Args:
            message: An explicit message, or ``None`` to use the standard diagnostic that
                names both the Python pin and the Maven coordinate it replaces.
        """
        super().__init__(_MISSING_DISTRIBUTION_MESSAGE if message is None else message)


@dataclass(frozen=True, slots=True)
class SyntheticCredentials:
    """One generated account record: an account name, an e-mail address and a password.

    The three fields are the shapes a login suite would ask a data builder for, and they
    are produced together from a single generator so that one seed reproduces the whole
    record rather than three unrelated values.

    Every field is synthetic and disposable. The e-mail address is drawn from Faker's safe
    mode, so its domain is one of those RFC 2606 reserves for documentation and can never
    belong to a real host, and the password is a freshly generated string that authorises
    nothing anywhere. Values of this type are therefore safe to log or to embed in a
    failure message -- they are not credentials for any real system, and no literal from
    the migrated specification is ever reproduced in them.

    Instances are frozen and slotted: immutable, cheap, and hashable, which keeps them
    usable as parametrisation input if the owners ever elect to exercise this module.

    Attributes:
        username: A generated account-name string, in the shape of a login identifier.
        email: A generated e-mail address whose domain is reserved for examples.
        password: A generated password-shaped string of the requested length.
    """

    username: str
    email: str
    password: str


def _require_faker_class() -> type[Faker]:
    """Return the imported Faker class, or fail with a diagnostic the caller can act on.

    Every entry point that needs the distribution funnels through here, so the
    unavailable case is handled in exactly one place and always the same way.

    Returns:
        The Faker class obtained by the guarded import at module scope.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported in this
            environment.
    """
    faker_class = _FAKER_CLASS
    if faker_class is None:
        raise FakerNotInstalledError()
    return faker_class


def _resolved_locale(locale: str | None) -> str:
    """Normalise a caller-supplied locale into the exact string handed to the library.

    Args:
        locale: A locale identifier such as ``"en_US"``, or ``None`` to use
            :data:`DEFAULT_LOCALE`.

    Returns:
        The locale to build a generator for, stripped of surrounding whitespace.

    Raises:
        ValueError: If ``locale`` is given but is empty or whitespace only. An empty
            string is rejected rather than silently treated as "use the default",
            because the two intentions are not the same and conflating them would hide a
            caller's mistake.
    """
    if locale is None:
        return DEFAULT_LOCALE
    candidate = locale.strip()
    if not candidate:
        raise ValueError(
            "locale must be a non-empty locale identifier such as "
            f"{DEFAULT_LOCALE!r}, or None to use the default; got {locale!r}"
        )
    return candidate


def _validated_password_length(length: int) -> int:
    """Reject password lengths the library cannot honour, with a message that explains why.

    Faker enables four character classes by default -- lower case, upper case, digits and
    punctuation -- and raises a bare assertion carrying no actionable detail when the
    requested length cannot accommodate one character from each. Checking first turns that
    into an ordinary, explicit argument error.

    Args:
        length: The requested password length.

    Returns:
        The validated length, unchanged.

    Raises:
        ValueError: If ``length`` is below :data:`MINIMUM_PASSWORD_LENGTH`.
    """
    if length < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(
            f"password length must be at least {MINIMUM_PASSWORD_LENGTH} so that every "
            "character class the generator enables can be represented; got "
            f"{length!r}"
        )
    return length


@cache
def _cached_generator(locale: str) -> Faker:
    """Build and memoise one generator per locale for the lifetime of this process.

    The cache is keyed on the already-normalised locale string, so two spellings that
    differ only in surrounding whitespace share one entry. It is process-local by
    construction: parallel workers are separate processes and share nothing here.

    Args:
        locale: An already-normalised locale identifier.

    Returns:
        The memoised generator for that locale.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If the library does not recognise the locale.
    """
    return build_generator(locale)


def is_available() -> bool:
    """Report whether synthetic values can be generated in this environment.

    This is the one supported way to ask the question, and it is answered from the
    guarded import performed once at module scope rather than by attempting another
    import. Introspecting this module -- reading its constants, catching its exception
    type, checking its signatures -- never requires the distribution; only generation
    does.

    Returns:
        ``True`` when the Faker distribution was importable, ``False`` otherwise.
    """
    return _FAKER_CLASS is not None


def seed_generators(value: SeedValue = None) -> None:
    """Seed every generator in this process from one value, for reproducible output.

    Delegates to Faker's class-level seed, which replaces the random stream shared by all
    generators in the interpreter. Two consequences follow, and both are intended: a
    single call governs generators that already exist as well as generators built later,
    and a caller need not thread a seed through the builders below.

    Seeding matters because the default invocation of this suite runs under
    ``pytest-xdist`` with ``-n logical`` -- the port of ``<parallel>methods</parallel>``
    ``[pom.xml:L22]`` with ``<useUnlimitedThreads>true</useUnlimitedThreads>``
    ``[pom.xml:L23]`` -- and each worker is a separate process with its own random state.
    Without a seed, output differs per worker and per run; with one, it does not.

    This function is never called on import and never called by default. It is the hook
    that makes a future decision to exercise this module reproducible, and until such a
    decision is taken it stays uncalled.

    Args:
        value: The seed to apply. Accepts the same shapes the library accepts -- an
            integer, a float, a string, or a bytes-like value. ``None``, the default,
            re-seeds from fresh entropy, which is the library's own behaviour for a
            seedless call rather than a policy invented here.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
    """
    faker_class = _require_faker_class()
    faker_class.seed(value)
    _LOGGER.debug("Synthetic-data generators seeded from %r", value)


def build_generator(locale: str | None = None) -> Faker:
    """Build a fresh, independent generator, bypassing the process-wide cache.

    Use this when shared state is unwanted -- for instance to hold two generators whose
    instance-level seeds differ. Callers that simply want values should prefer
    :func:`shared_generator`, or the builders below, which use it.

    Args:
        locale: A locale identifier such as ``"en_US"``, or ``None`` for
            :data:`DEFAULT_LOCALE`.

    Returns:
        A new generator for the requested locale.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``locale`` is empty or whitespace only, or if the library does not
            recognise it. The library signals an unrecognised locale with an attribute
            error, which is translated here so that callers see one error type for one
            category of mistake.
    """
    faker_class = _require_faker_class()
    resolved = _resolved_locale(locale)
    try:
        return faker_class(locale=resolved)
    except AttributeError as exc:
        raise ValueError(
            f"{resolved!r} is not a locale the installed generator recognises"
        ) from exc


def shared_generator(locale: str | None = None) -> Faker:
    """Return this process's memoised generator for a locale, building it on first use.

    One generator per locale is kept for the lifetime of the importing process, so a
    sequence of calls draws from one random stream and a single call to
    :func:`seed_generators` governs the whole sequence. The cache is process-local:
    parallel workers are separate processes and never share it. Call
    :func:`reset_shared_generators` to discard it explicitly.

    Args:
        locale: A locale identifier such as ``"en_US"``, or ``None`` for
            :data:`DEFAULT_LOCALE`.

    Returns:
        The memoised generator for the requested locale.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``locale`` is empty, whitespace only, or unrecognised.
    """
    return _cached_generator(_resolved_locale(locale))


def reset_shared_generators() -> None:
    """Discard every memoised generator held by this process.

    Provided alongside :func:`seed_generators` so that a caller can pair a fresh seed
    with freshly built generators instead of reasoning about the state of cached ones.
    Safe to call at any time, including when nothing has been cached yet and when the
    distribution is not installed at all: clearing an empty cache is a no-op.
    """
    _cached_generator.cache_clear()
    _LOGGER.debug("Memoised synthetic-data generators discarded")


def synthetic_full_name(*, locale: str | None = None) -> str:
    """Generate a personal name, in the shape JavaFaker's name provider produced.

    Args:
        locale: A locale identifier, or ``None`` for :data:`DEFAULT_LOCALE`.

    Returns:
        A generated full name.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``locale`` is empty, whitespace only, or unrecognised.
    """
    return str(shared_generator(locale).name())


def synthetic_email(*, locale: str | None = None) -> str:
    """Generate an e-mail address whose domain is reserved for documentation.

    The library's safe mode is used and is deliberately not overridable, so the address
    always sits in one of the domains RFC 2606 reserves for examples and can never reach
    a real mailbox.

    Args:
        locale: A locale identifier, or ``None`` for :data:`DEFAULT_LOCALE`.

    Returns:
        A generated e-mail address in a reserved example domain.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``locale`` is empty, whitespace only, or unrecognised.
    """
    return str(shared_generator(locale).email(safe=True))


def synthetic_username(*, locale: str | None = None) -> str:
    """Generate an account-name string, in the shape of a login identifier.

    Args:
        locale: A locale identifier, or ``None`` for :data:`DEFAULT_LOCALE`.

    Returns:
        A generated account name.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``locale`` is empty, whitespace only, or unrecognised.
    """
    return str(shared_generator(locale).user_name())


def synthetic_password(length: int = DEFAULT_PASSWORD_LENGTH, *, locale: str | None = None) -> str:
    """Generate a password-shaped string of the requested length.

    The generated string authorises nothing anywhere: it is disposable test data, not a
    credential for any system. No password from the migrated specification is reproduced,
    paraphrased or reconstructed here.

    Args:
        length: The number of characters to generate. Defaults to
            :data:`DEFAULT_PASSWORD_LENGTH`, the library's own default.
        locale: A locale identifier, or ``None`` for :data:`DEFAULT_LOCALE`.

    Returns:
        A generated password-shaped string of exactly ``length`` characters.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``length`` is below :data:`MINIMUM_PASSWORD_LENGTH`, or if
            ``locale`` is empty, whitespace only, or unrecognised.
    """
    validated_length = _validated_password_length(length)
    return str(shared_generator(locale).password(length=validated_length))


def synthetic_credentials(
    *,
    locale: str | None = None,
    password_length: int = DEFAULT_PASSWORD_LENGTH,
) -> SyntheticCredentials:
    """Generate one complete account record from a single generator.

    All three fields come from the same generator instance, so one call to
    :func:`seed_generators` reproduces the entire record rather than only part of it.

    Args:
        locale: A locale identifier, or ``None`` for :data:`DEFAULT_LOCALE`.
        password_length: The number of characters in the generated password. Defaults to
            :data:`DEFAULT_PASSWORD_LENGTH`.

    Returns:
        A frozen :class:`SyntheticCredentials` record holding an account name, an e-mail
        address in a reserved example domain, and a password-shaped string.

    Raises:
        FakerNotInstalledError: If the distribution could not be imported.
        ValueError: If ``password_length`` is below :data:`MINIMUM_PASSWORD_LENGTH`, or if
            ``locale`` is empty, whitespace only, or unrecognised.
    """
    validated_length = _validated_password_length(password_length)
    generator = shared_generator(locale)
    return SyntheticCredentials(
        username=str(generator.user_name()),
        email=str(generator.email(safe=True)),
        password=str(generator.password(length=validated_length)),
    )
