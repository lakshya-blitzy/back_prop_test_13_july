"""Criterion V1: ``tests/features/login.feature`` is byte-identical to the documented block.

What this module proves
=======================
Validation criterion V1 of the migration plan, stated there as: *the bytes of*
``tests/features/login.feature`` *equal the bytes of the Gherkin block documented in*
``README.md`` *(45 lines) exactly, including the missing space in* ``Scenario Outline:Users``,
*both Examples tables, and the French assertion literal.*

That criterion realises goal O4. The feature text embedded in the README is materialised as a
real ``.feature`` file **byte-for-byte** because it is the only executable specification the
project possesses, and byte-exactness is not stylistic: it is what guarantees the ported suite
behaves identically **including its defects**. Change one space and the ported suite quietly
stops being a port.

This module is one of the three named realisations of engineering baseline item B11,
"Executable parity evidence rather than assertions of parity". Every statement it makes about
the specification is therefore an assertion against bytes on disk, never prose.

The invariant is the digest, not a line range
=============================================
The block is located in ``README.md`` by **content anchor**, never by line number. The plan
cites it as ``README.md`` lines 104-148, but that is a *historical* locator: ``README.md`` is
itself rewritten by this same migration -- the tool list, the prerequisites, the runner
configuration block and several whole new sections change, much of it *above* the Gherkin --
so the block moves. It has already moved: it now begins some three hundred lines further down
than the plan records. A line-indexed comparison would fail for a reason that has nothing
whatever to do with parity, which is the single worst failure mode a parity test can have.

The anchors are the first line, ``@Login``, and the last, the final PosManager data row. Both
are asserted to be unique whole lines of the README, and the anchored pattern is asserted to
match exactly once, so a future rewrite that duplicated the block would fail loudly here
rather than silently match the first copy. Line numbers 104 and 148 survive only as clearly
labelled historical constants used to enrich the diagnostic when the anchor finds nothing.

Comparison is on **bytes**, never on decoded strings. A decoded comparison silently normalises
nothing on this platform but would mask an encoding or line-ending difference on another; a
byte comparison cannot. Every text read below names ``encoding="utf-8"`` explicitly (baseline
item B7) and the feature file itself is read with :meth:`~pathlib.Path.read_bytes`.

Why the comparison is stable on every platform
==============================================
``.gitattributes`` pins ``*.feature text eol=lf``. Without that pin a checkout under
``core.autocrlf=true`` -- the Windows default -- rewrites every LF to CRLF on the way out of
the object store, inflating this file from 1864 to 1909 bytes and breaking V1 on a machine
where nothing is actually wrong. That pin is precisely why ``.gitattributes`` is modified by
this migration rather than merely referenced, and the plan makes the ordering explicit: the
line-ending behaviour must be pinned *before* the feature file is written. The pin is
therefore **asserted** here, alongside the original ``*.html linguist-detectable=false`` rule
it was appended to, instead of being assumed.

No git attribute can protect *trailing* whitespace, and this block contains three
whitespace-only lines. They are the most fragile bytes in the repository: a single editor
"trim trailing whitespace" on save, or a whitespace linter allowed to reach ``.feature``
files, destroys V1 without touching a visible character. Their exact widths -- two, four and
six spaces -- are asserted individually, and so are the four genuinely empty lines, because a
formatter can collapse the former into the latter and only asserting both notices.

The defects are the point
=========================
Transformation rule T4 governs everything here: *defects are behavior*. The rewrite was
required to "fully match the behavior and logic of the current implementation", so the
catalogued source defects are preserved as the default and are asserted to REMAIN:

* **D2** -- the documented tag selector ``@LogOut`` matches nothing; the string ``LogOut``
  does not occur in this file at all.
* **D4** -- ``@UPGN-288`` feeds the *password* column value into the *username* step.
* **D5** -- the assertion expects the French ``Veuillez renseigner ce champ.`` while the
  comment above it describes an English message.
* **D8** -- the third outline's keyword is written ``Scenario Outline:Users``, with no space
  after the colon.
* **D9** -- the second and third outlines parse to the identical scenario name, which is what
  silently costs ``@UPGN-287`` its coverage.

Only the *byte-level footprint* of each is asserted here. The behavioural proofs -- that
collection yields exactly six tests, that ``@UPGN-286`` passes with literal placeholders, that
the emitted report records the password value in the username step -- belong to
``tests/parity/test_defect_preservation.py`` and are deliberately not duplicated.
``docs/migration-parity.md`` is the authoritative register of D1 through D9, each with its
evidence, its preservation status and the switch that opts into a fix (baseline item B12).

Import hygiene, and one thing this module deliberately does not depend on
========================================================================
**Standard library only, plus pytest itself.** This module must run, and pass, with no
third-party distribution installed at all: V1 is deliberately one of the always-checkable
criteria, so the acceptance gate still yields evidence in a bare environment. Nothing here
imports ``pytest_bdd`` or any Gherkin parser -- this is a byte comparison and parsing it would
be both unnecessary and a new dependency -- nothing imports Selenium or reaches into
``tests.support`` or ``tests.pages``, and nothing imports from ``app``: reaching any module
under ``app`` executes ``app/__init__.py`` first, which is the Flask application factory, so
every such reference transitively requires Flask.

``scripts/extract_feature_from_readme.py`` is the deterministic regenerator for the feature
file, and it is an **optional cross-check here, never a dependency**. It is not on the import
path in any guaranteed way -- ``pyproject.toml`` excludes ``scripts*`` from the distribution
and the directory holds no ``__init__.py`` -- its constant names are its own to change, and it
hard-codes the historical line range this module refuses to rely on. One test reaches for it
behind an existence-and-import guard and skips when it is absent, comparing only the constants
it actually finds; it never runs the script. Deleting that file cannot fail this module, and
nobody reading this should conclude the script has to exist.

This module performs **no writes of any kind**. It creates no file, no directory and no
subprocess: it reads three committed files and asserts. That is deliberate -- a test that can
rewrite the artifact it is checking is not evidence.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Annotation-only. `from __future__ import annotations` keeps every annotation a string,
    # so this block never executes and the module stays importable under a bare interpreter.
    from types import ModuleType

# ---------------------------------------------------------------------------------------------
# Repository-relative locations of the three committed files this module reads.
# Relative fragments only: they are always composed onto an absolute repository root, because
# the run is parallel by default, every xdist worker is a separate process and none of them is
# guaranteed to have been started from the repository root.
# ---------------------------------------------------------------------------------------------
FEATURE_RELATIVE_PATH: Final[str] = "tests/features/login.feature"
README_RELATIVE_PATH: Final[str] = "README.md"
GITATTRIBUTES_RELATIVE_PATH: Final[str] = ".gitattributes"

# ---------------------------------------------------------------------------------------------
# THE MEASURED BYTE CONTRACT.
# Independently measured five times with zero drift, and re-measured against the file on disk
# before this module was written. Every value is a fact about the artifact, not a preference.
# ---------------------------------------------------------------------------------------------
EXPECTED_BYTE_COUNT: Final[int] = 1864
"""Total size of the materialised specification, in bytes: 1819 of content plus 45 newlines."""

EXPECTED_LINE_COUNT: Final[int] = 45
"""Physical lines, which is also the newline count: every line is LF-terminated."""

EXPECTED_SHA256: Final[str] = "112d90560569d3011bb8a2c32d9acbc65d346be8f70ad7c182105b934079504c"
"""SHA-256 of the whole file. The single strongest statement of the byte contract."""

EXPECTED_FIRST_LINE: Final[str] = "@Login"
"""The feature-level tag, and the opening content anchor used to locate the README block."""

EXPECTED_LAST_LINE: Final[str] = "      |posmanager6@info.com   |posmanager|"
"""The final PosManager data row, and the closing content anchor. Three spaces before the
second pipe -- the PosManager rows pad differently from the SalesManager rows, which is data,
not a typo."""

UTF8_BOM: Final[bytes] = b"\xef\xbb\xbf"
"""The UTF-8 byte-order mark. Written as escapes so this source file stays pure ASCII."""

# The grid below is hand-aligned into rows of ten so a reader can count to a line number
# instead of scrolling forty-five entries, and so it can be compared at a glance with the
# vector recorded in the migration plan. `fmt: off` protects that alignment from the
# formatter's one-element-per-line expansion; it suppresses no check of any kind, and both
# ruff and black are otherwise applied to this file in full.
# fmt: off
EXPECTED_LINE_LENGTHS: Final[tuple[int, ...]] = (
    6,  36,  13,  86,   0,  40,   0,  89,  45,   0,
    123, 11,  55,  42,  41,  36,  38,   2, 141,  11,
    83,  42,  41,  36,  32,   4,  99,  11,  82,  42,
    36,  58,   0,  17,  50,  44,  44,  44,  44,   6,
    15,  48,  42,  42,  42,
)
# fmt: on
"""Length of every line in file order, newline excluded -- the strongest single check here.

One comparison of this vector proves, simultaneously and without naming any of them: the four
genuinely empty lines (positions 5, 7, 10 and 33 are ``0``); the three whitespace-only lines
(position 18 is ``2``, position 26 is ``4``, position 40 is ``6``); the opening tag (position 1
is ``6``); the final data row (position 45 is ``42``); and the asymmetric Examples header
padding, because position 36 is ``44`` while position 43 is ``42``. It sums to 1819, which plus
45 newlines is exactly :data:`EXPECTED_BYTE_COUNT` -- so the vector is self-proving.
"""

# ---------------------------------------------------------------------------------------------
# THE FRAGILE WHITESPACE.
# 1-based line numbers, because that is what an editor, a diff and a grep -n all report.
# ---------------------------------------------------------------------------------------------
WHITESPACE_ONLY_LINES: Final[tuple[tuple[int, int], ...]] = ((18, 2), (26, 4), (40, 6))
"""``(line number, exact width in spaces)`` for the three whitespace-only lines.

Historically ``README.md`` lines 121, 129 and 143. These lines carry no visible character, so
every whitespace-normalising tool in existence considers them noise; the specification
considers them content. The widths are asserted, not merely "is whitespace", because a
re-indent that turned four spaces into two would otherwise pass.
"""

ZERO_LENGTH_LINES: Final[tuple[int, ...]] = (5, 7, 10, 33)
"""The four genuinely empty lines. Historically ``README.md`` lines 108, 110, 113 and 136.

Asserted separately from :data:`WHITESPACE_ONLY_LINES` on purpose: a formatter pass collapses a
whitespace-only line into an empty one, and only checking both sets -- and both counts --
catches that.
"""

# ---------------------------------------------------------------------------------------------
# THE TWO EXAMPLES TABLES.
# Their padding is deliberately inconsistent. It looks like a typo; it is data, and every byte
# of it is part of the contract.
# ---------------------------------------------------------------------------------------------
EXAMPLES_HEADER_ROWS: Final[tuple[tuple[int, str], ...]] = (
    (36, "      |username               |password    |"),
    (43, "      |username               |password  |"),
)
"""``(line number, verbatim row)`` for the two Examples header rows.

The SalesManager header (line 36, historically ``README.md`` L139) closes with **four** spaces
before the final pipe and is 44 characters long; the PosManager header (line 43, historically
L146) closes with **two** and is 42. Normalising either to match the other would change the
bytes of the specification.
"""

EXAMPLES_DATA_ROWS: Final[tuple[str, ...]] = (
    "      |salesmanager7@info.com |salesmanager|",
    "      |salesmanager8@info.com |salesmanager|",
    "      |salesmanager9@info.com |salesmanager|",
    "      |posmanager5@info.com   |posmanager|",
    "      |posmanager6@info.com   |posmanager|",
)
"""The five Examples data rows, verbatim and in file order.

Three SalesManager rows with **one** space before the second pipe, two PosManager rows with
**three**. Five rows is also the parametrisation count that makes collection yield six tests
rather than seven once defect D9 has swallowed ``@UPGN-287``.
"""

EXAMPLES_DATA_ROW_PATTERN: Final[re.Pattern[str]] = re.compile(r"^      \| *(?:sales|pos)manager")
"""Recognises an Examples data row, mirroring the shell check prescribed for this criterion.

Deliberately not a general table-row pattern: it must not match either header row, so counting
its hits yields exactly the five data rows.
"""

EXAMPLES_TITLE_ROWS: Final[tuple[str, ...]] = (
    "    Examples: SalesManager's username and password",
    "    Examples: PosManager's username and password",
)
"""The two Examples titles, apostrophes and all. Both are asserted byte-exactly because an
apostrophe is exactly the character a well-meaning "smart quotes" pass would replace, and that
replacement would be the file's only non-ASCII byte."""

EXAMPLES_KEYWORD: Final[str] = "Examples:"
"""The Examples keyword. Occurs exactly twice, which is what defect D1 is about: both tables
bind to the third outline only, because Gherkin attaches ``Examples`` to the outline
immediately above it."""

# ---------------------------------------------------------------------------------------------
# THE PRESERVED DEFECTS -- byte-level footprints only.
# INTENTIONALLY PRESERVED, every one of them. Transformation rule T4: "Defects are behavior."
# The authoritative register, with the evidence and the opt-in fix for each, is
# docs/migration-parity.md. Do not "correct" anything named below.
# ---------------------------------------------------------------------------------------------
MALFORMED_OUTLINE_KEYWORD: Final[str] = "Scenario Outline:Users"
"""Defect **D8**, INTENTIONALLY PRESERVED. The third outline's keyword, missing the space
after the colon (historically ``README.md`` L132). Gherkin treats the colon as the keyword
separator, so the malformed spelling parses -- and produces a scenario *name* indistinguishable
from the well-formed outline above it, which is the mechanism of defect D9. See
``docs/migration-parity.md``."""

WELL_FORMED_OUTLINE_KEYWORD: Final[str] = "Scenario Outline: "
"""The correctly spaced keyword, for contrast: it occurs twice, against D8's one."""

OUTLINE_KEYWORD: Final[str] = "Scenario Outline"
"""The keyword itself, ignoring what follows it: three outlines are authored in total."""

COLLIDED_SCENARIO_NAME: Final[str] = (
    "Users log in with invalid email or invalid password credentials"
)
"""Defect **D9**'s byte-level footprint, INTENTIONALLY PRESERVED.

This name occurs **twice**: once after ``Scenario Outline: `` under ``@UPGN-287`` and once
after ``Scenario Outline:`` under ``@UPGN-288``. pytest-bdd derives the generated
test-function name from the scenario name, so both bind the same symbol in the runner module
and the later definition silently replaces the earlier -- ``@UPGN-287`` becomes unreachable and
its coverage is lost without a single warning. The behavioural proof (collection yields exactly
six tests, none of them derived from ``@UPGN-287``) belongs to
``tests/parity/test_defect_preservation.py``; only the two occurrences are asserted here. The
one-line fix is recorded in ``docs/migration-parity.md`` and is deliberately NOT applied.
"""

PASSWORD_INTO_USERNAME_STEP: Final[str] = '    When User enters "<password>" username'
"""Defect **D4**, INTENTIONALLY PRESERVED. ``@UPGN-288`` feeds the *password* column's value
into the *username* field, while the comment above it describes an empty-field case. The
emitted Cucumber JSON records ``User enters "salesmanager" username`` -- the password column,
never the e-mail addresses -- which is direct proof in the published artifact. That proof lives
in ``tests/parity/test_defect_preservation.py``; here the step line is simply asserted to occur
exactly once. See ``docs/migration-parity.md``."""

FRENCH_VALIDATION_MESSAGE: Final[str] = "Veuillez renseigner ce champ."
"""Defect **D5**, INTENTIONALLY PRESERVED. The assertion expects this French string while the
comment above it describes the English "Please fill out this field". The assertion is the
executable truth, so the French string stays.

29 characters and 29 bytes: French-*language* text, but pure ASCII, so nothing here is
multi-byte. It must not be translated to agree with the comment, must not be normalised, and
must keep its trailing period. See ``docs/migration-parity.md``.
"""

ABSENT_DEFAULT_TAG: Final[str] = "LogOut"
"""Defect **D2**, INTENTIONALLY PRESERVED. The documented runner selects ``tags = "@LogOut"``,
and this string does not occur in the specification **at all** -- so the run as documented
deselects every scenario and executes nothing. That zero-scenario run is a *success*, matching
a source build that went green in exactly this situation. The exit-code half of that contract
is criterion V6's; the byte half is here: zero occurrences. See ``docs/migration-parity.md``."""

BACKGROUND_LINE: Final[str] = (
    "  Background: For the scenarios in the feature file, user is expected to be on login page"
)
"""The single ``Background``. Asserted because it is why the emitted Cucumber JSON's
``elements`` array can hold an entry that is not a scenario, which the report-schema criterion
depends on."""

CODE_FENCE: Final[str] = "`" * 3
"""A Markdown code fence. The block is extracted from between two of them, so finding one
*inside* the feature file would mean the extraction over-reached. Built by repetition rather
than written literally so this constant cannot itself terminate a fenced quotation of this
module."""

# ---------------------------------------------------------------------------------------------
# THE SIX GHERKIN TAGS.
# ---------------------------------------------------------------------------------------------
EXPECTED_TAG_LINES: Final[tuple[str, ...]] = (
    "@Login",
    "@UPGN-286",
    "@UPGN-287",
    "@UPGN-288",
    "@SalesManager",
    "@PosManager",
)
"""Every tag in the specification, in file order.

All six are registered as pytest markers in ``pytest.ini`` with the ``@`` stripped, which is
how pytest-bdd exposes them and how the ``UPGN-`` Jira traceability convention survives the
migration without introducing a Jira client. This module registers no marker and applies none.
"""

PHANTOM_TAG: Final[str] = "@info.com"
"""The trap. A bare ``@\\w+``-style scan over this file harvests a seventh "tag" out of the
e-mail addresses in the Examples tables -- ``salesmanager7@info.com`` and friends -- and
reports seven tags where there are six.

A tag is therefore recognised **only** on a line whose stripped content begins with ``@``.
Never replace that with a regular expression over the whole text, however tidy it looks.
"""

# ---------------------------------------------------------------------------------------------
# LOCATING THE BLOCK IN README.md -- content anchors, never line numbers.
# ---------------------------------------------------------------------------------------------
README_GHERKIN_BLOCK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"@Login\n.*?\|posmanager6@info\.com   \|posmanager\|\n",
    re.DOTALL,
)
"""The canonical extraction pattern, shared verbatim with the README's own validation contract.

``@Login\\n`` opens it, the final PosManager data row closes it, and ``.*?`` under
:data:`re.DOTALL` takes the shortest span between them. The three literal spaces inside
``posmanager6@info\\.com   \\|`` are table padding and are matched literally: collapsing them to
``\\s+`` would make the pattern tolerant of exactly the drift it exists to detect. Neither
Markdown fence is part of the match, and the pattern is asserted to match exactly once.
"""

HISTORICAL_README_FIRST_LINE: Final[int] = 104
"""HISTORICAL ONLY -- where the block sat before ``README.md`` was rewritten. Used exclusively
to enrich the diagnostic when anchored extraction finds nothing. Never the primary strategy."""

HISTORICAL_README_LAST_LINE: Final[int] = 148
"""HISTORICAL ONLY -- see :data:`HISTORICAL_README_FIRST_LINE`. Inclusive, 1-based."""

# ---------------------------------------------------------------------------------------------
# THE RECORDED GROUND TRUTH.
# A second, independent statement of the same 1864 bytes, base64-encoded so no editor,
# formatter or line-ending setting can touch it. It is deliberately NOT the primary comparison:
# checking the feature file against the live README is what stops the two artifacts drifting.
# This exists so that "what are the correct bytes?" has an unambiguous answer even if README.md
# is one day rewritten in a way that defeats extraction.
# ---------------------------------------------------------------------------------------------
RECORDED_FEATURE_BASE64: Final[str] = (
    "QExvZ2luCkZlYXR1cmU6IFRlc3Rpbml1bSBhcHAgbG9naW4gZmVhdHVyZQogIFVzZXIgU3Rvcnk6"
    "CiAgQXMgYSB1c2VyLCBJIHNob3VsZCBiZSBhYmxlIHRvIGxvZ2luIHdpdGggY29ycmVjdCBjcmVk"
    "ZW50aWFscyB0byBkaWZmZXJlbnQgYWNjb3VudHMuCgogIEFjY291bnRzIGFyZTogUG9zTWFuYWdl"
    "ciwgU2FsZXNNYW5hZ2VyCgogIEJhY2tncm91bmQ6IEZvciB0aGUgc2NlbmFyaW9zIGluIHRoZSBm"
    "ZWF0dXJlIGZpbGUsIHVzZXIgaXMgZXhwZWN0ZWQgdG8gYmUgb24gbG9naW4gcGFnZQogICAgR2l2"
    "ZW4gVXNlciBpcyBvbiB0aGUgVGVzdGluaXVtIGxvZ2luIHBhZ2UKCiAgIzEtVXNlcnMgY2FuIGxv"
    "ZyBpbiB3aXRoIHZhbGlkIGNyZWRlbnRpYWxzIChXZSBoYXZlIDUgdHlwZXMgb2YgdXNlcnMgYnV0"
    "IHdpbGwgdGVzdCBvbmx5IDIgdXNlcjogUG9zTWFuYWdlciwgU2FsZXNNYW5hZ2VyKQogIEBVUEdO"
    "LTI4NgogIFNjZW5hcmlvIE91dGxpbmU6IFVzZXJzIGxvZyBpbiB3aXRoIHZhbGlkIGNyZWRlbnRp"
    "YWxzCiAgICBXaGVuIFVzZXIgZW50ZXJzICI8dXNlcm5hbWU+IiB1c2VybmFtZQogICAgQW5kIFVz"
    "ZXIgZW50ZXJzICI8cGFzc3dvcmQ+IiBwYXNzd29yZAogICAgQW5kIFVzZXIgY2xpY2tzIHRoZSBs"
    "b2dpbiBidXR0b24KICAgIFRoZW4gVXNlciBzaG91bGQgc2VlIHRoZSBkYXNoYm9hcmQKICAKICAj"
    "Mi0iV3JvbmcgbG9naW4vcGFzc3dvcmQiIHNob3VsZCBiZSBkaXNwbGF5ZWQgZm9yIGludmFsaWQg"
    "KHZhbGlkIHVzZXJuYW1lLWludmFsaWQgcGFzc3dvcmQgYW5kIGludmFsaWQgdXNlcm5hbWUtdmFs"
    "aWQgcGFzc3dvcmQpIGNyZWRlbnRpYWxzCiAgQFVQR04tMjg3CiAgU2NlbmFyaW8gT3V0bGluZTog"
    "VXNlcnMgbG9nIGluIHdpdGggaW52YWxpZCBlbWFpbCBvciBpbnZhbGlkIHBhc3N3b3JkIGNyZWRl"
    "bnRpYWxzCiAgICBXaGVuIFVzZXIgZW50ZXJzICI8dXNlcm5hbWU+IiB1c2VybmFtZQogICAgQW5k"
    "IFVzZXIgZW50ZXJzICI8cGFzc3dvcmQ+IiBwYXNzd29yZAogICAgQW5kIFVzZXIgY2xpY2tzIHRo"
    "ZSBsb2dpbiBidXR0b24KICAgIFRoZW4gVXNlciBzZWVzIGVycm9yIG1lc3NhZ2UKICAgIAogICMz"
    "LSAiUGxlYXNlIGZpbGwgb3V0IHRoaXMgZmllbGQiIG1lc3NhZ2Ugc2hvdWxkIGJlIGRpc3BsYXll"
    "ZCBpZiB0aGUgcGFzc3dvcmQgb3IgdXNlcm5hbWUgaXMgZW1wdHkKICBAVVBHTi0yODgKICBTY2Vu"
    "YXJpbyBPdXRsaW5lOlVzZXJzIGxvZyBpbiB3aXRoIGludmFsaWQgZW1haWwgb3IgaW52YWxpZCBw"
    "YXNzd29yZCBjcmVkZW50aWFscwogICAgV2hlbiBVc2VyIGVudGVycyAiPHBhc3N3b3JkPiIgdXNl"
    "cm5hbWUKICAgIEFuZCBVc2VyIGNsaWNrcyB0aGUgbG9naW4gYnV0dG9uCiAgICBUaGVuIFVzZXIg"
    "c2VlcyAiVmV1aWxsZXogcmVuc2VpZ25lciBjZSBjaGFtcC4iIG1lc3NhZ2UKCiAgICBAU2FsZXNN"
    "YW5hZ2VyCiAgICBFeGFtcGxlczogU2FsZXNNYW5hZ2VyJ3MgdXNlcm5hbWUgYW5kIHBhc3N3b3Jk"
    "CiAgICAgIHx1c2VybmFtZSAgICAgICAgICAgICAgIHxwYXNzd29yZCAgICB8CiAgICAgIHxzYWxl"
    "c21hbmFnZXI3QGluZm8uY29tIHxzYWxlc21hbmFnZXJ8CiAgICAgIHxzYWxlc21hbmFnZXI4QGlu"
    "Zm8uY29tIHxzYWxlc21hbmFnZXJ8CiAgICAgIHxzYWxlc21hbmFnZXI5QGluZm8uY29tIHxzYWxl"
    "c21hbmFnZXJ8CiAgICAgIAogICAgQFBvc01hbmFnZXIKICAgIEV4YW1wbGVzOiBQb3NNYW5hZ2Vy"
    "J3MgdXNlcm5hbWUgYW5kIHBhc3N3b3JkCiAgICAgIHx1c2VybmFtZSAgICAgICAgICAgICAgIHxw"
    "YXNzd29yZCAgfAogICAgICB8cG9zbWFuYWdlcjVAaW5mby5jb20gICB8cG9zbWFuYWdlcnwKICAg"
    "ICAgfHBvc21hbmFnZXI2QGluZm8uY29tICAgfHBvc21hbmFnZXJ8Cg=="
)

# ---------------------------------------------------------------------------------------------
# THE LINE-ENDING PIN that makes all of the above stable on every platform.
# ---------------------------------------------------------------------------------------------
GITATTRIBUTES_ORIGINAL_RULE: Final[str] = "*.html linguist-detectable=false"
"""The one rule the pre-migration ``.gitattributes`` contained, preserved as its first line.

The whole pre-migration file was this single line: 32 bytes, no trailing newline. It keeps
GitHub Linguist from counting generated or served HTML as detectable language content, which is
still correct after the migration, so it survives untouched.
"""

GITATTRIBUTES_FEATURE_PATTERN: Final[str] = "*.feature"
"""The path pattern whose end-of-line attribute this criterion depends on."""

GITATTRIBUTES_REQUIRED_ATTRIBUTES: Final[tuple[str, ...]] = ("text", "eol=lf")
"""The attributes the ``*.feature`` rule must carry.

``text`` marks the file as line-ending-normalised on the way into the object store; ``eol=lf``
forces LF on the way back out regardless of ``core.autocrlf``. Both are needed: ``text`` alone
leaves the checkout at the platform's mercy.
"""

GITATTRIBUTES_COMMENT_PREFIX: Final[str] = "#"
"""Comment marker in the gitattributes format. Comments and blank lines carry no rule."""

# ---------------------------------------------------------------------------------------------
# THE OPTIONAL REGENERATOR -- a cross-check, never a dependency. See the module docstring.
# ---------------------------------------------------------------------------------------------
REGENERATOR_MODULE_NAME: Final[str] = "scripts.extract_feature_from_readme"
"""Dotted name of the deterministic regenerator, reached only behind an import guard.

It is not guaranteed to be importable: ``pyproject.toml`` excludes ``scripts*`` from the
distribution and the directory carries no ``__init__.py``, so resolution depends on the
repository root happening to be on ``sys.path``. Absence is a skip, never a failure.
"""

REGENERATOR_EXPECTED_CONSTANTS: Final[tuple[tuple[str, object], ...]] = (
    ("EXPECTED_BYTE_COUNT", EXPECTED_BYTE_COUNT),
    ("EXPECTED_LINE_COUNT", EXPECTED_LINE_COUNT),
    ("EXPECTED_SHA256", EXPECTED_SHA256),
    ("EXPECTED_FIRST_LINE", EXPECTED_FIRST_LINE),
    ("EXPECTED_LAST_LINE", EXPECTED_LAST_LINE),
    ("WHITESPACE_ONLY_LINE_WIDTHS", tuple(width for _, width in WHITESPACE_ONLY_LINES)),
    ("CANONICAL_FIRST_LINE_NUMBER", HISTORICAL_README_FIRST_LINE),
    ("CANONICAL_LAST_LINE_NUMBER", HISTORICAL_README_LAST_LINE),
)
"""``(attribute name, value this module holds)`` pairs to cross-check against the regenerator.

Every name is looked up with a default, and only the ones actually present are compared. The
script owns its own naming and may rename or drop any of these without breaking criterion V1,
which depends on ``README.md`` and the feature file alone.
"""


# The repository root as derived from this module's own location: `tests/parity/` sits two
# directories below it. This is the STANDALONE FALLBACK only -- the `repository_root` fixture
# below prefers the session-scoped `project_root` fixture published by `tests/conftest.py`, so
# there is exactly one authority per session. Deriving it from the process working directory
# instead would be a latent flake: the run is parallel by default and no worker process is
# guaranteed to have been started from the root.
_MODULE_DERIVED_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

_PROJECT_ROOT_FIXTURE_NAME: Final[str] = "project_root"


def _historical_block_slice(readme_text: str) -> str:
    """Return the README text the historical line range would have selected.

    Diagnostic use only. Anchored extraction is the primary and only supported strategy; this
    reproduces the pre-rewrite ``lines[103:148]`` slice so that a failure message can say
    whether the old locator would have worked, which is the difference between "the block
    moved" and "the block changed".

    Args:
        readme_text: The full decoded contents of ``README.md``.

    Returns:
        The joined slice, or the empty string when the document is too short to contain it.
    """
    lines = readme_text.splitlines(keepends=True)
    return "".join(lines[HISTORICAL_README_FIRST_LINE - 1 : HISTORICAL_README_LAST_LINE])


def _extraction_failure_message(
    readme_path: Path,
    readme_text: str,
    match_count: int,
) -> str:
    """Build the diagnostic for a failed anchored extraction, naming both strategies.

    A silent pass or a skip here would be worse than useless: it would report parity that was
    never checked. So extraction failure is always a hard failure, and the message carries
    everything needed to tell the two possible causes apart -- the block was duplicated, or the
    anchors themselves changed.

    Args:
        readme_path: Absolute path of the document that was searched, so the reader knows which
            file to open.
        readme_text: Its full decoded contents, used to evaluate the historical fallback.
        match_count: How many times the anchored pattern matched. Anything but one is a
            failure: zero means the anchors are gone, more than one means the block was
            duplicated and picking the first would be arbitrary.

    Returns:
        A multi-line, self-contained explanation.
    """
    historical = _historical_block_slice(readme_text)
    historical_digest = hashlib.sha256(historical.encode("utf-8")).hexdigest()
    historical_verdict = (
        "would have matched"
        if historical_digest == EXPECTED_SHA256
        else f"would NOT have matched (sha256 {historical_digest})"
    )
    return "\n".join(
        (
            f"Could not extract the Gherkin block from {readme_path}: the anchored pattern "
            f"matched {match_count} time(s), expected exactly 1.",
            "",
            "Strategy 1 (primary, content anchors): first line "
            f"{EXPECTED_FIRST_LINE!r} through last line {EXPECTED_LAST_LINE!r}, "
            f"pattern {README_GHERKIN_BLOCK_PATTERN.pattern!r}.",
            f"Strategy 2 (historical, diagnostic only): README.md lines "
            f"{HISTORICAL_README_FIRST_LINE}-{HISTORICAL_README_LAST_LINE} "
            f"{historical_verdict}.",
            "",
            "Zero matches means an anchor line changed -- restore it, or the specification and "
            "its documentation have genuinely diverged. More than one match means the block "
            "was duplicated in README.md; deduplicate it rather than relaxing this check, "
            "because silently taking the first copy would make criterion V1 meaningless.",
            f"The correct bytes are recorded independently in this module: "
            f"{EXPECTED_BYTE_COUNT} bytes, sha256 {EXPECTED_SHA256}.",
        )
    )


def _extract_gherkin_block(readme_text: str, readme_path: Path) -> str:
    """Extract the fenced Gherkin block from ``README.md`` by content anchor.

    The anchors are the block's own first and last lines, so the result is independent of where
    in the document the block happens to sit -- which matters, because the migration rewrites
    ``README.md`` and has already moved this block several hundred lines. Neither Markdown fence
    is included in the result.

    Args:
        readme_text: The full decoded contents of ``README.md``.
        readme_path: Its absolute path, used only to make a failure message actionable.

    Returns:
        The matched region, still decoded, terminated by the newline that closes its final data
        row and nothing more.
    """
    matches = list(README_GHERKIN_BLOCK_PATTERN.finditer(readme_text))
    if len(matches) != 1:
        pytest.fail(_extraction_failure_message(readme_path, readme_text, len(matches)))
    return matches[0].group(0)


def _gherkin_tag_lines(feature_lines: tuple[str, ...]) -> tuple[str, ...]:
    """Return every Gherkin tag in file order, recognised by line rather than by pattern.

    A tag line is one whose stripped content begins with ``@``. That restriction is the whole
    point of this helper: scanning the text with a bare ``@``-word regular expression instead
    harvests a phantom seventh tag out of the ``@info.com`` in the Examples e-mail addresses.
    See :data:`PHANTOM_TAG` before simplifying this.

    Args:
        feature_lines: The specification's lines, newline excluded, in file order.

    Returns:
        The stripped tag lines, in file order. A line carrying several tags is returned as one
        entry; the specification carries exactly one tag per line, and that is asserted.
    """
    return tuple(stripped for line in feature_lines if (stripped := line.strip()).startswith("@"))


def _gitattributes_rules(gitattributes_text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse ``.gitattributes`` into ``(path pattern, attributes)`` pairs.

    Deliberately minimal: the format is whitespace-separated, the first field is the pattern and
    the rest are attributes. Blank lines and ``#`` comments carry no rule and are dropped. No
    macro definition, negation or attribute-value parsing is attempted, because the only
    question asked of this file is whether ``*.feature`` is pinned to LF.

    Args:
        gitattributes_text: The full decoded contents of ``.gitattributes``.

    Returns:
        One entry per rule line, in file order.
    """
    rules: list[tuple[str, tuple[str, ...]]] = []
    for line in gitattributes_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(GITATTRIBUTES_COMMENT_PREFIX):
            continue
        pattern, *attributes = stripped.split()
        rules.append((pattern, tuple(attributes)))
    return tuple(rules)


@pytest.fixture(scope="module")
def repository_root(request: pytest.FixtureRequest) -> Path:
    """Return the absolute repository root, preferring the session-scoped ``project_root``.

    ``tests/conftest.py`` publishes ``project_root`` for exactly this purpose and names this
    test among its intended consumers, so it is asked for first: one authority per session
    beats two independent computations that can disagree.

    The fallback exists so this module stays usable outside that conftest chain -- run with
    ``--noconftest``, or copied somewhere on its own -- rather than failing for a reason
    unrelated to parity. It is derived from ``__file__``, never from the process working
    directory, and never expressed as a relative path.

    Args:
        request: The fixture request, used to look the session fixture up by name.

    Returns:
        The absolute, symlink-resolved repository root.
    """
    try:
        resolved = request.getfixturevalue(_PROJECT_ROOT_FIXTURE_NAME)
    except pytest.FixtureLookupError:
        return _MODULE_DERIVED_ROOT
    if isinstance(resolved, Path):
        return resolved
    return _MODULE_DERIVED_ROOT


@pytest.fixture(scope="module")
def feature_path(repository_root: Path) -> Path:
    """Absolute path of the materialised specification."""
    return repository_root / FEATURE_RELATIVE_PATH


@pytest.fixture(scope="module")
def feature_bytes(feature_path: Path) -> bytes:
    """The specification's raw bytes -- the single read every byte assertion works from.

    Read as bytes, never as text: decoding first would hide an encoding or line-ending
    difference behind Python's own tolerance, and those differences are precisely what this
    criterion exists to catch.
    """
    return feature_path.read_bytes()


@pytest.fixture(scope="module")
def feature_text(feature_bytes: bytes) -> str:
    """The specification decoded as UTF-8, for the assertions that are about characters.

    Decoded from the bytes already read rather than by a second, differently configured read,
    so the two views can never disagree.
    """
    return feature_bytes.decode("utf-8")


@pytest.fixture(scope="module")
def feature_lines(feature_text: str) -> tuple[str, ...]:
    """The specification's lines, newline excluded, in file order.

    Split on ``"\\n"`` and not with :meth:`str.splitlines`, which also breaks on vertical tab,
    form feed and the file separators -- all ASCII, all therefore possible in a file this
    module has only asserted to *be* ASCII. Splitting on the one terminator the contract names
    keeps the line numbering exact and makes no assumption. The trailing element produced by the
    file's final newline is dropped, and that it was empty is asserted separately.
    """
    return tuple(feature_text.split("\n")[:-1])


@pytest.fixture(scope="module")
def readme_path(repository_root: Path) -> Path:
    """Absolute path of the document the specification is extracted from."""
    return repository_root / README_RELATIVE_PATH


@pytest.fixture(scope="module")
def readme_text(readme_path: Path) -> str:
    """The full documentation text, decoded as UTF-8 explicitly (baseline item B7)."""
    return readme_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gitattributes_text(repository_root: Path) -> str:
    """The full ``.gitattributes`` contents, decoded as UTF-8 explicitly."""
    return (repository_root / GITATTRIBUTES_RELATIVE_PATH).read_text(encoding="utf-8")


# =============================================================================================
# CRITERION V1 -- the byte comparison itself.
# =============================================================================================


def test_feature_file_bytes_equal_the_documented_block(
    feature_bytes: bytes,
    readme_text: str,
    feature_path: Path,
    readme_path: Path,
) -> None:
    """Criterion V1: the materialised specification is byte-identical to the documented block.

    This is the assertion the module exists for. Everything else below narrows down *where* a
    difference is, should this one ever fail.

    The extraction is performed here rather than in a fixture on purpose: a fixture that fails
    is reported as an *error at setup*, and the headline assertion of an acceptance criterion
    should read as a plain failure with the diagnostic attached to it.
    """
    documented_bytes = _extract_gherkin_block(readme_text, readme_path).encode("utf-8")
    assert feature_bytes == documented_bytes, (
        f"{feature_path} is no longer byte-identical to the Gherkin block documented in "
        f"{readme_path}. Regenerate it with `make feature` rather than editing either side by "
        f"hand: feature file {len(feature_bytes)} bytes "
        f"(sha256 {hashlib.sha256(feature_bytes).hexdigest()}) versus documented block "
        f"{len(documented_bytes)} bytes "
        f"(sha256 {hashlib.sha256(documented_bytes).hexdigest()})."
    )


def test_documented_block_occurs_exactly_once(readme_text: str) -> None:
    """The anchored pattern matches the documentation exactly once.

    Asserted in its own right, and not merely relied on inside the extraction helper, because a
    rewrite that duplicated the block would otherwise let extraction pick the first copy and
    quietly compare against whichever one happened to come first.
    """
    match_count = len(README_GHERKIN_BLOCK_PATTERN.findall(readme_text))
    assert match_count == 1, (
        f"Expected exactly one Gherkin block in README.md, found {match_count}. "
        "Deduplicate the documentation rather than relaxing this check."
    )


def test_documented_block_anchors_are_unique_whole_lines(readme_text: str) -> None:
    """Both content anchors are unique *whole lines* of the documentation.

    This is what makes anchoring safe. ``@Login`` appears more than once in the prose -- inside
    backticks, mid-sentence, where the file's tags are being listed -- so uniqueness has to be
    asserted at line granularity, not substring granularity. If a future rewrite ever ends a
    prose line with an anchor, this fails here with an explanation instead of silently
    over-matching several hundred lines of documentation into the comparison.
    """
    readme_lines = readme_text.split("\n")
    first_anchor_lines = [
        number for number, line in enumerate(readme_lines, start=1) if line == EXPECTED_FIRST_LINE
    ]
    last_anchor_lines = [
        number for number, line in enumerate(readme_lines, start=1) if line == EXPECTED_LAST_LINE
    ]
    assert len(first_anchor_lines) == 1, (
        f"README.md must contain exactly one line equal to {EXPECTED_FIRST_LINE!r}; "
        f"found it on lines {first_anchor_lines}."
    )
    assert len(last_anchor_lines) == 1, (
        f"README.md must contain exactly one line equal to {EXPECTED_LAST_LINE!r}; "
        f"found it on lines {last_anchor_lines}."
    )
    assert first_anchor_lines[0] < last_anchor_lines[0], (
        "The opening anchor must precede the closing anchor in README.md; found them on lines "
        f"{first_anchor_lines[0]} and {last_anchor_lines[0]}."
    )


def test_feature_file_matches_the_recorded_ground_truth(feature_bytes: bytes) -> None:
    """The specification also matches the independently recorded payload.

    A second anchor on the same 1864 bytes, held base64-encoded so no formatter, editor or
    line-ending setting can reach it. Deliberately *not* the primary comparison -- checking
    against the live documentation is what keeps the two artifacts from drifting -- but it is
    what answers "which side is wrong?" when the primary comparison fails.
    """
    recorded = base64.b64decode(RECORDED_FEATURE_BASE64)
    assert feature_bytes == recorded, (
        "The feature file differs from the payload recorded in this module. If README.md was "
        "edited deliberately, the recorded payload and the byte contract here must be updated "
        "in the same change; if not, restore the feature file with `make feature`."
    )


# =============================================================================================
# THE MEASURED BYTE CONTRACT.
# =============================================================================================


def test_feature_file_byte_count(feature_bytes: bytes) -> None:
    """The specification is exactly 1864 bytes.

    A CRLF checkout would report 1909 -- 45 bytes more, one per line -- which is the single most
    likely cause of this failing on a machine where nothing is actually wrong. See the
    ``*.feature text eol=lf`` pin asserted further down.
    """
    assert len(feature_bytes) == EXPECTED_BYTE_COUNT


def test_feature_file_line_count(feature_bytes: bytes, feature_lines: tuple[str, ...]) -> None:
    """The specification is exactly 45 LF-terminated lines.

    Newline count and physical line count are asserted to be the same number, which together
    with the trailing-newline check below means every line is terminated and none is orphaned.
    """
    assert feature_bytes.count(b"\n") == EXPECTED_LINE_COUNT
    assert len(feature_lines) == EXPECTED_LINE_COUNT


def test_feature_file_sha256(feature_bytes: bytes) -> None:
    """The specification's digest is unchanged -- the strongest single statement of parity."""
    assert hashlib.sha256(feature_bytes).hexdigest() == EXPECTED_SHA256


def test_feature_file_is_pure_ascii(feature_bytes: bytes) -> None:
    """Every byte is below 128.

    Worth asserting explicitly because the specification contains French text, which invites the
    assumption that it must be multi-byte. It is not: ``Veuillez renseigner ce champ.`` is pure
    ASCII. A non-ASCII byte appearing here would mean something re-typed the file -- a smart
    quote for an apostrophe, or an en dash for a hyphen.
    """
    assert feature_bytes.isascii()


def test_feature_file_has_no_carriage_return(feature_bytes: bytes) -> None:
    """Not one CR byte: no CRLF pair, and no bare CR either."""
    assert feature_bytes.count(b"\r") == 0


def test_feature_file_has_no_byte_order_mark(feature_bytes: bytes) -> None:
    """No UTF-8 BOM. Gherkin's first token is the ``@Login`` tag and nothing precedes it."""
    assert not feature_bytes.startswith(UTF8_BOM)


def test_feature_file_ends_with_exactly_one_newline(
    feature_bytes: bytes,
    feature_text: str,
) -> None:
    """The payload ends with exactly one LF -- not none, and not two.

    The block's own terminator is the newline closing its final data row, so a second one would
    be an extra byte that the documented block does not contain. The split-derived check states
    the same fact a second way: the element after the final newline is the empty string, which
    is what proves the file does not end mid-line.
    """
    assert feature_bytes.endswith(b"\n")
    assert not feature_bytes.endswith(b"\n\n")
    assert feature_text.split("\n")[-1] == ""


def test_feature_file_first_and_last_line(feature_lines: tuple[str, ...]) -> None:
    """The two content anchors are the specification's own first and last lines."""
    assert feature_lines[0] == EXPECTED_FIRST_LINE
    assert feature_lines[-1] == EXPECTED_LAST_LINE


def test_per_line_length_vector(feature_lines: tuple[str, ...]) -> None:
    """Every line has exactly the length recorded for it.

    The single most informative check in the module: it localises a difference to a line number
    without needing a separate assertion per fragile line, and it catches added or removed
    trailing whitespace anywhere in the file, which a digest reports only as "different".
    """
    measured = tuple(len(line) for line in feature_lines)
    assert measured == EXPECTED_LINE_LENGTHS, (
        "Line lengths drifted from the recorded contract at line(s) "
        + ", ".join(
            str(number)
            for number, (actual, expected) in enumerate(
                zip(measured, EXPECTED_LINE_LENGTHS, strict=False), start=1
            )
            if actual != expected
        )
        + " (1-based)."
    )


def test_line_length_vector_accounts_for_every_byte(feature_bytes: bytes) -> None:
    """The recorded vector is self-proving: its sum plus one newline per line is the file size.

    1819 content bytes plus 45 newlines is 1864. If this ever fails, the constants in this
    module contradict each other and must be re-measured against the artifact before anything
    else here is trusted.
    """
    assert len(EXPECTED_LINE_LENGTHS) == EXPECTED_LINE_COUNT
    assert sum(EXPECTED_LINE_LENGTHS) + EXPECTED_LINE_COUNT == EXPECTED_BYTE_COUNT
    assert len(feature_bytes) == EXPECTED_BYTE_COUNT


# =============================================================================================
# THE FRAGILE WHITESPACE -- the most easily destroyed bytes in the repository.
# =============================================================================================


@pytest.mark.parametrize(("line_number", "width"), WHITESPACE_ONLY_LINES)
def test_whitespace_only_line_has_exact_width(
    feature_lines: tuple[str, ...],
    line_number: int,
    width: int,
) -> None:
    """A whitespace-only line is exactly the recorded number of spaces -- no more, no fewer.

    Two spaces at line 18, four at line 26, six at line 40. Nothing visible distinguishes these
    from empty lines, so a single "trim trailing whitespace" on save, or a whitespace linter
    allowed to reach ``.feature`` files, silently destroys criterion V1. The width is asserted
    rather than merely "is whitespace", because a re-indent preserves whitespace-ness while
    changing the bytes.
    """
    line = feature_lines[line_number - 1]
    assert line == " " * width, (
        f"Feature line {line_number} must be exactly {width} space(s), found {line!r}. "
        "Do not let a whitespace-trimming tool near tests/features/."
    )


def test_whitespace_only_line_count_is_exactly_three(feature_lines: tuple[str, ...]) -> None:
    """There are exactly three whitespace-only lines, at the recorded positions.

    The count matters as much as the positions: a formatter that collapsed one of these into a
    genuinely empty line would still leave the other two intact and pass a positions-only check.
    """
    found = tuple(
        number
        for number, line in enumerate(feature_lines, start=1)
        if line != "" and line.strip() == ""
    )
    expected = tuple(number for number, _ in WHITESPACE_ONLY_LINES)
    assert found == expected, (
        f"Expected whitespace-only lines at {expected}, found them at {found}. A line that moved "
        "from this set into the empty-line set has had its trailing whitespace stripped."
    )


@pytest.mark.parametrize("line_number", ZERO_LENGTH_LINES)
def test_zero_length_line_is_empty(feature_lines: tuple[str, ...], line_number: int) -> None:
    """A genuinely empty line carries no character at all -- not even a space."""
    line = feature_lines[line_number - 1]
    assert line == "", f"Feature line {line_number} must be empty, found {line!r}."


def test_zero_length_line_count_is_exactly_four(feature_lines: tuple[str, ...]) -> None:
    """There are exactly four genuinely empty lines, at the recorded positions.

    The counterpart to the whitespace-only count: together the two assertions pin down which of
    the seven blank-looking lines carry bytes and which do not.
    """
    found = tuple(number for number, line in enumerate(feature_lines, start=1) if line == "")
    assert found == ZERO_LENGTH_LINES, (
        f"Expected empty lines at {ZERO_LENGTH_LINES}, found them at {found}. A line that moved "
        "into this set from the whitespace-only set has lost its trailing whitespace."
    )


# =============================================================================================
# THE TWO EXAMPLES TABLES -- deliberately inconsistent padding, preserved byte for byte.
# =============================================================================================


@pytest.mark.parametrize(("line_number", "row"), EXAMPLES_HEADER_ROWS)
def test_examples_header_row_keeps_its_own_padding(
    feature_lines: tuple[str, ...],
    line_number: int,
    row: str,
) -> None:
    """Each Examples header row is byte-exact, including its own trailing padding.

    The two headers are padded differently -- four spaces before the closing pipe on line 36 and
    two on line 43, so 44 characters against 42. It reads like a typo in one of them. It is data,
    and aligning them would change the bytes of the specification.
    """
    assert feature_lines[line_number - 1] == row, (
        f"Feature line {line_number} must be exactly {row!r}. The two Examples headers pad "
        "differently on purpose; do not align them."
    )


def test_examples_header_rows_have_asymmetric_widths() -> None:
    """The recorded contract itself states the asymmetry, so the two rows cannot be conflated.

    Asserted against the recorded constants rather than the file: this pins the *contract*, so a
    future tidy-up of this module cannot quietly make both headers the same width and then
    "prove" the file matches.
    """
    widths = tuple(len(row) for _, row in EXAMPLES_HEADER_ROWS)
    assert widths == (44, 42)
    assert EXPECTED_LINE_LENGTHS[35] == 44
    assert EXPECTED_LINE_LENGTHS[42] == 42


def test_examples_data_rows_are_verbatim(feature_lines: tuple[str, ...]) -> None:
    """All five Examples data rows are present, verbatim and in file order.

    The SalesManager rows leave one space before the second pipe and the PosManager rows leave
    three. Five rows is also why collection yields six tests rather than seven once defect D9 has
    swallowed ``@UPGN-287``: one test for ``@UPGN-286`` plus five parametrisations of the
    collided name.
    """
    found = tuple(line for line in feature_lines if EXAMPLES_DATA_ROW_PATTERN.match(line))
    assert found == EXAMPLES_DATA_ROWS


def test_examples_data_row_count_is_exactly_five(feature_lines: tuple[str, ...]) -> None:
    """Exactly five data rows, mirroring the prescribed shell count for this criterion."""
    assert sum(1 for line in feature_lines if EXAMPLES_DATA_ROW_PATTERN.match(line)) == len(
        EXAMPLES_DATA_ROWS
    )


def test_examples_titles_keep_their_apostrophes(feature_lines: tuple[str, ...]) -> None:
    """Both Examples titles are byte-exact, apostrophes included.

    A straight ASCII apostrophe is exactly what a "smart quotes" pass would replace with U+2019,
    and that single substitution would give this pure-ASCII file its first multi-byte character.
    """
    assert EXAMPLES_TITLE_ROWS[0] in feature_lines
    assert EXAMPLES_TITLE_ROWS[1] in feature_lines


def test_examples_keyword_occurs_exactly_twice(feature_text: str) -> None:
    """Two Examples tables, no more.

    Both bind to the third outline alone -- Gherkin attaches ``Examples`` to the outline
    immediately above it -- which is defect D1, INTENTIONALLY PRESERVED. The behavioural half of
    D1, that ``@UPGN-286`` therefore runs with its placeholders unsubstituted and passes, is
    asserted in ``tests/parity/test_defect_preservation.py``. See ``docs/migration-parity.md``.
    """
    assert feature_text.count(EXAMPLES_KEYWORD) == 2


# =============================================================================================
# THE PRESERVED DEFECTS -- byte-level footprints. Every one INTENTIONALLY PRESERVED.
# Authoritative register: docs/migration-parity.md. Do not "fix" anything asserted below.
# =============================================================================================


def test_defect_d8_malformed_outline_keyword_survives(feature_text: str) -> None:
    """Defect **D8**, INTENTIONALLY PRESERVED: one outline keyword has no space after its colon.

    ``Scenario Outline:Users`` occurs once, ``Scenario Outline: `` occurs twice, and
    ``Scenario Outline`` three times in total. Gherkin treats the colon as the keyword
    separator, so the malformed spelling parses perfectly well -- and yields a scenario name
    indistinguishable from the outline above it, which is the mechanism behind defect D9.
    Inserting the missing space is a behavioural change, explicitly out of scope. See
    ``docs/migration-parity.md``.
    """
    assert feature_text.count(MALFORMED_OUTLINE_KEYWORD) == 1
    assert feature_text.count(WELL_FORMED_OUTLINE_KEYWORD) == 2
    assert feature_text.count(OUTLINE_KEYWORD) == 3


def test_defect_d9_duplicate_scenario_name_survives(feature_text: str) -> None:
    """Defect **D9**, INTENTIONALLY PRESERVED: two outlines parse to the identical name.

    The name occurs twice, once under each of ``@UPGN-287`` and ``@UPGN-288``. pytest-bdd derives
    the generated test-function name from the scenario name, so the later definition silently
    replaces the earlier and ``@UPGN-287`` loses its coverage without a warning.

    Only the two occurrences are asserted here. The behavioural proof -- that collection yields
    exactly six tests and that none of them derives from ``@UPGN-287`` -- belongs to
    ``tests/parity/test_defect_preservation.py`` and is deliberately not duplicated. The one-line
    fix is recorded in ``docs/migration-parity.md`` as an owner decision and is NOT applied.
    """
    assert feature_text.count(COLLIDED_SCENARIO_NAME) == 2
    assert f"{WELL_FORMED_OUTLINE_KEYWORD}{COLLIDED_SCENARIO_NAME}" in feature_text
    assert f"{MALFORMED_OUTLINE_KEYWORD[: -len('Users')]}{COLLIDED_SCENARIO_NAME}" in feature_text


def test_defect_d4_password_placeholder_feeds_the_username_step(feature_text: str) -> None:
    """Defect **D4**, INTENTIONALLY PRESERVED: the password column is typed into the username.

    ``@UPGN-288``'s first step is ``When User enters "<password>" username``, while the comment
    above it describes an empty-field case. The published Cucumber JSON records
    ``User enters "salesmanager" username`` -- the password column's values, never the e-mail
    addresses -- so the defect is observable in the artifact the source system would have
    published, not merely in the text.

    Here only the step line is asserted, exactly once. The report-level proof belongs to
    ``tests/parity/test_defect_preservation.py``. See ``docs/migration-parity.md``.
    """
    assert feature_text.count(PASSWORD_INTO_USERNAME_STEP) == 1


def test_defect_d5_french_assertion_literal_survives(feature_text: str) -> None:
    """Defect **D5**, INTENTIONALLY PRESERVED: the assertion is French, the comment English.

    The comment above the scenario describes "Please fill out this field"; the assertion expects
    ``Veuillez renseigner ce champ.``. The assertion is the executable truth, so the French
    string stays as it is -- untranslated, unnormalised, and with its trailing period intact.

    It is 29 characters and 29 bytes: French-language text that is nonetheless pure ASCII, so
    nothing here is multi-byte and no encoding subtlety is involved. See
    ``docs/migration-parity.md``.
    """
    assert feature_text.count(FRENCH_VALIDATION_MESSAGE) == 1
    assert len(FRENCH_VALIDATION_MESSAGE) == 29
    assert len(FRENCH_VALIDATION_MESSAGE.encode("utf-8")) == 29
    assert FRENCH_VALIDATION_MESSAGE.isascii()
    assert FRENCH_VALIDATION_MESSAGE.endswith(
        "."
    ), "The trailing period is part of the asserted string; a strip() somewhere has removed it."
    assert f'Then User sees "{FRENCH_VALIDATION_MESSAGE}" message' in feature_text


def test_defect_d2_documented_tag_is_absent_from_the_specification(feature_text: str) -> None:
    """Defect **D2**, INTENTIONALLY PRESERVED: the documented tag selector matches nothing.

    The runner's documented ``tags = "@LogOut"`` selects scenarios by a tag that does not exist:
    the string ``LogOut`` occurs **zero** times in the specification. Run exactly as documented,
    the suite therefore executes nothing -- and that zero-scenario run is a SUCCESS, because the
    source build went green in precisely this situation and its report thresholds gated nothing.

    The exit-code half of that contract (pytest's code 5 mapping to success) is criterion V6's,
    in ``tests/parity/test_default_run_exit_code.py``. The byte half is here. Adding the tag to
    the specification to "make the suite run" is the exact behavioural change rule T4 forbids.
    See ``docs/migration-parity.md``.
    """
    assert feature_text.count(ABSENT_DEFAULT_TAG) == 0


# =============================================================================================
# STRUCTURAL FACTS the rest of the harness depends on.
# =============================================================================================


def test_background_line_appears_exactly_once(
    feature_text: str,
    feature_lines: tuple[str, ...],
) -> None:
    """The single ``Background`` is present, verbatim.

    Asserted because it has a downstream consequence: a Background makes the emitted Cucumber
    JSON's ``elements`` array able to hold an entry that is not a scenario, which the report
    schema criterion has to allow for.
    """
    assert feature_text.count("Background:") == 1
    assert BACKGROUND_LINE in feature_lines


def test_no_markdown_fence_leaked_into_the_specification(feature_text: str) -> None:
    """No Markdown code fence anywhere in the specification.

    The block is extracted from between two fences -- and the opening one is bare, with no
    language hint, which is part of defect D8 and is preserved in the documentation. Finding a
    fence *inside* the feature file would mean the extraction had over-reached and swallowed a
    delimiter.
    """
    assert feature_text.count(CODE_FENCE) == 0


def test_gherkin_tag_lines_are_exactly_the_six_documented_tags(
    feature_lines: tuple[str, ...],
) -> None:
    """Exactly six tags, in file order, recognised line by line.

    THE TRAP, named so nobody simplifies this back into a one-line regular expression: scanning
    the text for ``@``-words instead harvests a seventh phantom tag, ``@info.com``, out of the
    e-mail addresses in the Examples tables. A tag is recognised only on a line whose stripped
    content begins with ``@``.

    All six are registered as markers in ``pytest.ini`` with the ``@`` stripped, which is both
    how pytest-bdd exposes them and how the ``UPGN-`` Jira traceability convention survives the
    migration without a Jira client. This module registers no marker and applies none.
    """
    tag_lines = _gherkin_tag_lines(feature_lines)
    assert tag_lines == EXPECTED_TAG_LINES
    assert PHANTOM_TAG not in tag_lines
    assert all(len(tag.split()) == 1 for tag in tag_lines), (
        "Each tag line carries exactly one tag in this specification; a line with several would "
        "change how pytest-bdd derives its markers."
    )


def test_phantom_tag_is_why_a_naive_regex_is_not_used(feature_text: str) -> None:
    """The phantom tag really is in the text, which is why the line-based rule exists.

    This asserts the *trap*, not the specification: ``@info.com`` genuinely occurs -- five times,
    once per Examples e-mail address -- so any scan that does not work line by line will report
    seven tags where there are six. Keeping this assertion here means the reasoning cannot be
    lost to a future tidy-up.
    """
    assert feature_text.count(PHANTOM_TAG) == len(EXAMPLES_DATA_ROWS)
    assert not any(line.strip().startswith(PHANTOM_TAG) for line in feature_text.split("\n"))


# =============================================================================================
# THE LINE-ENDING PIN that makes every comparison above stable on every platform.
# =============================================================================================


def test_gitattributes_preserves_the_original_html_rule(gitattributes_text: str) -> None:
    """``.gitattributes`` still opens with the one rule it contained before the migration.

    The pre-migration file was exactly this line and nothing else. It is preserved as line 1 --
    the Linguist exclusion is still correct for the generated Cucumber HTML report and the Jinja2
    templates -- and the end-of-line pins were appended below it.
    """
    first_line = gitattributes_text.split("\n")[0]
    assert first_line == GITATTRIBUTES_ORIGINAL_RULE


def test_gitattributes_pins_feature_files_to_lf(gitattributes_text: str) -> None:
    """``*.feature`` is pinned to ``text eol=lf``, which is what makes criterion V1 portable.

    Without this rule a checkout under ``core.autocrlf=true`` -- the Windows default -- rewrites
    every LF to CRLF on the way out of the object store, inflating the specification from 1864 to
    1909 bytes and failing the byte comparison on a machine where nothing is wrong. This pin is
    the reason ``.gitattributes`` is modified by the migration rather than merely referenced, and
    it has to be in place *before* the feature file is written.

    Asserted rather than assumed, because an assumption is exactly what a silent platform
    difference feeds on.
    """
    feature_rules = [
        attributes
        for pattern, attributes in _gitattributes_rules(gitattributes_text)
        if pattern == GITATTRIBUTES_FEATURE_PATTERN
    ]
    assert feature_rules, (
        f"No {GITATTRIBUTES_FEATURE_PATTERN!r} rule in .gitattributes. Without it, "
        "tests/features/login.feature is at the mercy of core.autocrlf and criterion V1 becomes "
        "platform-dependent."
    )
    for required in GITATTRIBUTES_REQUIRED_ATTRIBUTES:
        assert any(required in attributes for attributes in feature_rules), (
            f"The {GITATTRIBUTES_FEATURE_PATTERN!r} rule must carry {required!r}; found "
            f"{feature_rules}."
        )


# =============================================================================================
# THE OPTIONAL CROSS-CHECK -- never a dependency. See the module docstring.
# =============================================================================================


def test_optional_regenerator_constants_agree_with_this_module() -> None:
    """Cross-check the recorded contract against the regenerator, when it is reachable.

    ``scripts/extract_feature_from_readme.py`` regenerates the feature file deterministically and
    declares the same byte count, digest and line count that this module holds. Where both are
    present they must agree, otherwise `make feature` and criterion V1 could disagree about what
    "correct" means.

    It is an OPTIONAL cross-check and nothing more. The script is not guaranteed to be
    importable -- ``pyproject.toml`` excludes ``scripts*`` and the directory has no
    ``__init__.py`` -- it owns its own constant names, and it hard-codes the historical line range
    this module refuses to depend on. So absence is a skip, only the attributes actually found are
    compared, and the script is **never executed**: criterion V1 rests on ``README.md`` and the
    feature file alone.
    """
    try:
        if importlib.util.find_spec(REGENERATOR_MODULE_NAME) is None:
            pytest.skip(
                f"{REGENERATOR_MODULE_NAME} is not importable, so the optional regenerator "
                "cross-check was not performed. Criterion V1 does not depend on it."
            )
        regenerator: ModuleType = importlib.import_module(REGENERATOR_MODULE_NAME)
    except ImportError as error:
        pytest.skip(
            f"{REGENERATOR_MODULE_NAME} could not be imported ({type(error).__name__}: {error}), "
            "so the optional regenerator cross-check was not performed. Criterion V1 does not "
            "depend on it."
        )

    missing_sentinel = object()
    compared = 0
    for attribute, expected in REGENERATOR_EXPECTED_CONSTANTS:
        found = getattr(regenerator, attribute, missing_sentinel)
        if found is missing_sentinel:
            continue
        compared += 1
        assert found == expected, (
            f"{REGENERATOR_MODULE_NAME}.{attribute} is {found!r} but this module records "
            f"{expected!r}. The regenerator and criterion V1 must agree on the byte contract; "
            "update whichever of the two is stale."
        )

    if compared == 0:
        pytest.skip(
            f"{REGENERATOR_MODULE_NAME} exposes none of the constants this cross-check knows "
            "about, so nothing was compared. Criterion V1 does not depend on it."
        )
