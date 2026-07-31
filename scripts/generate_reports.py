#!/usr/bin/env python3
"""Command-line front end over the four Cucumber report artifacts.

This is the Python port of the two report commands the source project documented,
and of the artifact side of its ``'Generate report'`` pipeline stage. It *reports
on* all four artifacts and *produces* the two that no test-runner option can
produce. It is a front end over the adapters in ``app/reporting/`` and contains
no reporting engine of its own: every behaviour below is delegated, so there is
never a second implementation that can drift from the first.

The two documented source commands
==================================
``README.md`` documents exactly two report invocations, and no others::

    mvn test -Dcucumber.options="--plugin html:target/cucumber-reports.html"
    mvn test -Dcucumber.options="--plugin rerun:target/rerun.txt"

``[README.md:L157, L161]``. They are modelled here as the selectable actions
``--artifact html`` and ``--artifact rerun``, so the correspondence between the
documented commands and this script is auditable from ``--help`` alone.

DELIBERATE RULE T5 CORRECTION -- please do not "restore" it. In both source lines
the character immediately before ``plugin`` is U+2013 EN DASH, not a hyphen, so
the commands exactly as printed would not parse. Rule T5 of the migration permits
a cosmetic source-text error to be corrected precisely when leaving it would make
the Python artifact non-functional, and this is one of only two corrections the
entire migration permits -- the other being the discoverable file-name alias
added beside the pipeline definition, which is not this script's concern. Both
occurrences are therefore written with the ASCII double hyphen ``--plugin``, here
and everywhere below, and this source file is pure ASCII by design so that the
EN DASH cannot creep back in unnoticed.

Who produces what
=================
The four artifacts are the four plugin declarations the documented runner listed.
Quoted from the source text verbatim, they are ``"html:target/cucumber-reports.html"``,
``"json:target/cucumber.json"``, ``"rerun:target/rerun.txt"`` and
``"me.jvt.cucumber.report.PrettyReports:target/cucumber"``. Those four strings are
the only paths written out anywhere in this file, and they appear only as quoted
source text: every path this script actually uses is taken from
``app/utils/paths.py``, so the artifact root is never spelled twice.

Two of the four are written by test-runner plugins during the run itself; the other
two have no runner option at all and exist only because something calls the adapter
that renders them:

==================  ========================================  ==================
Artifact            Written by                                This script
==================  ========================================  ==================
Cucumber JSON       the run's native Cucumber-JSON writer     read and validate
Cucumber HTML       the run's self-contained HTML writer      locate + describe
rerun manifest      ``app/reporting/rerun_report.py``         PRODUCES it
PrettyReports dir   ``app/reporting/pretty_reports.py``       PRODUCES it
==================  ========================================  ==================

Two boundaries are absolute:

* **The HTML report is never generated, re-rendered or rewritten here.** It is
  treated as a static artifact, because re-rendering it would risk diverging from
  the source's output. ``app/reporting/html_report.py`` is deliberately a locator
  and describer with no generation code whatsoever, and this script respects that
  boundary: for the HTML artifact it reports presence, size, modification time and
  content type, and explains that the run produces it -- exactly as the
  documented source command also produced it as part of the test run.
* **The Cucumber JSON is never written here.** The ported test stack ships a
  native Cucumber-JSON writer that emits the very schema the CI publisher
  consumes, and that single fact is what makes report parity achievable without
  writing a report serializer at all.

This script never runs the tests
===============================
Running the suite belongs to ``scripts/run_tests.sh``, ``scripts/run_tests.bat``,
the ``Makefile`` ``test`` target and ``app/services/test_runner_service.py``.
This script only post-processes artifacts that already exist: it imports no test
framework, and it starts no process -- there is no subprocess call anywhere below.

Non-gating, and quiet when there is nothing to do
=================================================
The source pipeline could not fail. Its report stage ran unconditionally after
the test stage, failures included, and every publisher threshold was ``-1``,
which the publisher reads as "no threshold". Generating reports here is therefore
non-gating in the same way: a report full of failures is generated exactly as
successfully as a report full of passes, and the presence of a failed scenario
never influences this script's exit code.

"Not generated yet" is the ordinary state, three times over: a fresh checkout has
no artifact tree, the preserved default tag expression selects no scenario at
all, and the application under test is external and unreachable from CI. The
absent path is therefore the quiet, boring, successful path. It becomes an error
only when a derived artifact was explicitly requested and cannot be produced.

Deliberate omissions
====================
* **No file I/O of its own.** Every read and every write goes through an adapter,
  each of which opens its streams with an explicit UTF-8 encoding. There is
  therefore no ``open``, ``read_text`` or ``write_text`` call anywhere below for
  the project-wide "always state the encoding" requirement to apply to.
* **No directory creation and no deletion.** Creating the artifact tree is
  delegated to ``app/utils/paths.py``; wiping it belongs to the clean target and
  to the two test-run scripts. Nothing here creates or removes a path.
* **No process execution.** No subprocess is spawned, so there is no command
  string for external input to be interpolated into.
* **No third-party dependency.** The standard library plus the application's own
  reporting and utility modules, and nothing else.

Usage
=====
Run it with the interpreter the project pins -- CPython 3.14.6, as recorded in
``.python-version`` and required by ``requires-python = ">=3.14"``. The
virtual environment and the container image both provide it, and ``make report``
is wired to it, so the commands below need nothing installed and no PYTHONPATH::

    python3 scripts/generate_reports.py                    # every artifact
    python3 scripts/generate_reports.py --artifact rerun   # one artifact
    python3 scripts/generate_reports.py --show-config      # publisher settings
    make report                                            # the wired-up target
"""

import argparse
import logging
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final

# ---------------------------------------------------------------------------
# LOAD-BEARING, NOT DECORATIVE -- do not "tidy" this away.
#
# `scripts/` is deliberately excluded from the installed package (the packaging
# metadata includes `app*` and excludes `scripts*`) and deliberately has no
# package initialiser. Running `python3 scripts/generate_reports.py` therefore
# puts `scripts/` -- not the repository root -- at the front of the import path,
# so `import app...` below fails with ModuleNotFoundError unless the root is put
# there explicitly. Deriving the root from this file's own location rather than
# from the working directory is what lets the script be invoked from anywhere,
# with no editable install and no PYTHONPATH.
#
# The membership test keeps a repeated insertion harmless (the module could be
# imported as well as executed), and the imports that follow are placed after
# this block out of necessity -- hence the narrowly scoped `noqa: E402` on each
# one, rather than any relaxation of the project's lint configuration.
# ---------------------------------------------------------------------------
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The module alias and the name import below address the same concrete module on
# purpose. Importing `PUBLISHER_THRESHOLDS` by name is the cross-module contract
# the migration plan fixes verbatim for the ported publisher call, while the alias
# reaches the sort-order and include-pattern constants without restating either
# value. Both spellings resolve to the one frozen constants module, so no value is
# ever declared twice, and neither module root is imported: `app/reporting` and
# `app/utils` are pure markers that export nothing.
import app.reporting.thresholds as thresholds  # noqa: E402
from app.reporting.cucumber_json import (  # noqa: E402
    CucumberJsonReport,
    NormalizedFeature,
    NormalizedStep,
    load_report,
    report_path,
)
from app.reporting.html_report import (  # noqa: E402
    PLUGIN_DECLARATION as HTML_PLUGIN_DECLARATION,
)
from app.reporting.html_report import (  # noqa: E402
    PRODUCER_DISTRIBUTION as HTML_PRODUCER_DISTRIBUTION,
)
from app.reporting.html_report import HtmlReportArtifact, describe_html_report  # noqa: E402
from app.reporting.pretty_reports import (  # noqa: E402
    PLUGIN_DECLARATION as PRETTY_PLUGIN_DECLARATION,
)
from app.reporting.pretty_reports import PrettyReportsResult, generate_pretty_reports  # noqa: E402
from app.reporting.rerun_report import (  # noqa: E402
    PLUGIN_DECLARATION as RERUN_PLUGIN_DECLARATION,
)
from app.reporting.rerun_report import (  # noqa: E402
    RerunManifest,
    generate_rerun_manifest,
    rerun_manifest_path,
)
from app.reporting.thresholds import PUBLISHER_THRESHOLDS  # noqa: E402
from app.utils.paths import (  # noqa: E402
    artifact_paths,
    ensure_target_layout,
    managed_directories,
    to_posix,
)

# A module logger for diagnostics, and nothing more. Handlers, levels and
# formatters belong exclusively to `app/logging_config.py`: nothing here
# configures the logging system. Human-facing command-line output is a separate
# concern and goes through `_write` / `_write_error` below, so a reader can always
# tell the two apart at the call site.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# The two documented source commands, with the Rule T5 correction applied.
#
# Neither string hard-codes a path: each artifact's plugin declaration is taken
# from the adapter that owns it, which in turn takes its path from
# `app/utils/paths.py`. The artifact root can therefore never be spelled twice,
# and these commands cannot drift from the artifacts they describe.
# =============================================================================

_COMMAND_TEMPLATE: Final[str] = 'mvn test -Dcucumber.options="--plugin {declaration}"'

DOCUMENTED_HTML_COMMAND: Final[str] = _COMMAND_TEMPLATE.format(declaration=HTML_PLUGIN_DECLARATION)
"""The HTML-report command as documented at ``[README.md:L157]``, EN DASH corrected."""

DOCUMENTED_TXT_COMMAND: Final[str] = _COMMAND_TEMPLATE.format(declaration=RERUN_PLUGIN_DECLARATION)
"""The Txt-report command as documented at ``[README.md:L161]``, EN DASH corrected."""


# =============================================================================
# Actions. One per artifact, plus the everything-obtainable default.
# =============================================================================

ACTION_JSON: Final[str] = "json"
"""Read and validate the Cucumber JSON report, and summarise what it recorded."""

ACTION_HTML: Final[str] = "html"
"""Locate and describe the Cucumber HTML report. Never generates it."""

ACTION_RERUN: Final[str] = "rerun"
"""Produce the rerun manifest from the Cucumber JSON report."""

ACTION_PRETTY: Final[str] = "pretty"
"""Produce the PrettyReports directory from the Cucumber JSON report."""

ACTION_ALL: Final[str] = "all"
"""Report on every artifact and produce every artifact that can be produced."""

ARTIFACT_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_JSON,
    ACTION_HTML,
    ACTION_RERUN,
    ACTION_PRETTY,
)
"""The four artifact actions, in the order they are always executed and printed."""

ACTION_CHOICES: Final[tuple[str, ...]] = (ACTION_ALL, *ARTIFACT_ACTIONS)
"""Every accepted ``--artifact`` value, with the default first."""

PRODUCIBLE_ACTIONS: Final[frozenset[str]] = frozenset({ACTION_RERUN, ACTION_PRETTY})
"""The two artifacts this script can actually write.

The other two are written by the test run: the Cucumber JSON by the native
Cucumber-JSON writer and the HTML report by the self-contained HTML writer.
Asking for either of those here reports on it; it never produces it.
"""

EXIT_SUCCESS: Final[int] = 0
"""Process exit code for success, including every "not generated yet" outcome."""

EXIT_FAILURE: Final[int] = 1
"""Process exit code for a genuine failure of this script -- never for a test failure."""

PROGRAM_NAME: Final[str] = Path(__file__).name
"""This script's file name, used as the command name and as the message prefix.

Derived from the module's own location rather than from the process arguments, so
help text and diagnostics read identically however the script was invoked.
"""


# =============================================================================
# PRESERVED DEFECTS -- read this before changing anything below.
#
# The migration's governing rule is that defects are behaviour: the rewrite must
# fully match the behaviour and logic of the current implementation, so each
# defect below is reproduced as the default rather than quietly corrected.
# `docs/migration-parity.md` is the authoritative register of defects D1 through
# D9 and records, for each one, the fix its owners may elect. Six of them reach
# this script:
#
#   * D1 -- INTENTIONALLY PRESERVED. The first scenario outline carries no
#     Examples table (both tables bind to the third outline only), so its steps
#     execute with the LITERAL placeholder text `<username>` and `<password>` --
#     and they pass. Step names are therefore printed exactly as the report
#     recorded them. Nothing here substitutes a placeholder, rebinds an Examples
#     table or rewrites a step name, and the rendered directory the PrettyReports
#     adapter writes HTML-escapes that text so it stays visible rather than
#     becoming markup.
#
#   * D2 -- INTENTIONALLY PRESERVED. The preserved default tag expression selects
#     a tag no scenario carries, so the documented invocation runs ZERO
#     scenarios and produces an essentially empty report. That is handled as a
#     quiet success everywhere below; no tag is added and no special case
#     rescues it.
#
#   * D3 -- INTENTIONALLY PRESERVED. All six publisher thresholds are `-1`
#     `[Jenkins:L15]`, which the publisher reads as "no threshold", and the source
#     build additionally swallowed test failures outright through
#     `<testFailureIgnore>true</testFailureIgnore>` `[pom.xml:L25]`, so the source
#     system had no build-time quality gate at all. Reports are therefore
#     generated even after failures, and generating them influences no verdict:
#     there is deliberately no threshold evaluation and no build-should-fail
#     helper anywhere in this file. (`pom.xml` is a read-only parity contract: it
#     is cited here for provenance and is never read, modified or compiled.)
#
#   * D4 -- INTENTIONALLY PRESERVED. The third outline feeds the PASSWORD column
#     value into the USERNAME step, so the report records step names such as
#     `User enters "salesmanager" username` rather than the e-mail address from
#     the username column. Those names are printed verbatim; nothing here swaps
#     the columns back or checks that a username-shaped step looks like an
#     e-mail address.
#
#   * D5 -- INTENTIONALLY PRESERVED. One scenario asserts a French message whose
#     trailing period is significant, while the comment above it describes an
#     English one. Step text is never translated, trimmed, case-folded or
#     Unicode-normalised on its way through this script.
#
#   * D9 -- INTENTIONALLY PRESERVED. Two outlines parse to the IDENTICAL scenario
#     name, so one of them is silently unreachable and five executed scenarios
#     legitimately share a single name. Consequence for this file: no output
#     name, dictionary key or de-duplication may ever be derived from a scenario
#     name -- doing so would collapse five parametrisations into one and destroy
#     four fifths of the data. Scenarios are keyed and reported by their
#     `<uri>:<line>` coordinate throughout, and nothing below de-duplicates them.
#     Asserting the collected-test count belongs to the parity suite, not here.
# =============================================================================


# =============================================================================
# ALPHABETICAL feature ordering.
#
# The pipeline's report publisher was configured with `sortingMethod:
# 'ALPHABETICAL'` [Jenkins:L15], and that setting has to be genuinely
# implemented even though it is a no-op while the suite holds a single feature:
# leaving it out would turn a preserved setting into a divergence that only
# surfaced once a second feature was authored.
#
# The method name is READ from the frozen constants module rather than restated
# here, which is exactly why that value lives in one place: this script and the
# service that reproduces the publication step for the HTTP surface cannot drift
# apart.
#
# The ordering is total and stable. The report layer already orders features by a
# stable key (uri first), so re-sorting by name alone would leave same-named
# features in an order that a parallel run could permute; adding `uri` and then
# `line` as tiebreakers makes two runs over the same data print byte-identical
# output. That is a correctness requirement, not a stylistic one.
# =============================================================================


def alphabetical_feature_key(feature: NormalizedFeature) -> tuple[str, str, int]:
    """Return the ALPHABETICAL ordering key of *feature*.

    Args:
        feature: A normalized feature from the Cucumber JSON adapter.

    Returns:
        ``(name, uri, line)`` -- the publisher's alphabetical-by-name order, made
        total by two deterministic tiebreakers so the ordering is reproducible
        even when two features share a name.
    """
    return (feature.name, feature.uri, feature.line)


FEATURE_SORT_KEYS: Final[Mapping[str, Callable[[NormalizedFeature], tuple[str, str, int]]]] = (
    MappingProxyType({thresholds.REPORT_SORTING_METHOD: alphabetical_feature_key})
)
"""Ordering strategies, keyed by the publisher's sorting-method name.

Keyed by the constant rather than by a literal, so the supported method name can
only ever be the one the pipeline configures.
"""


def sort_features(
    features: Iterable[NormalizedFeature],
    sorting_method: str = thresholds.REPORT_SORTING_METHOD,
) -> tuple[NormalizedFeature, ...]:
    """Order *features* the way the report publisher was configured to.

    Args:
        features: The features to order. Consumed once, so a generator is fine.
        sorting_method: The publisher's sorting-method name. Defaults to the
            configured value, which is the only method the source declares; an
            unrecognised name is reported and the incoming order is preserved
            rather than guessed at.

    Returns:
        An immutable tuple. Nothing is added, dropped, merged or de-duplicated --
        this only ever reorders.
    """
    key = FEATURE_SORT_KEYS.get(sorting_method)
    if key is None:
        _LOGGER.warning(
            "Unknown report sorting method %r; leaving features in the order the "
            "report layer fixed",
            sorting_method,
        )
        return tuple(features)
    return tuple(sorted(features, key=key))


# =============================================================================
# Human-facing output.
#
# This is a command-line tool, so its artifact summary is written to standard
# output for a person to read. That is deliberately kept distinct from logging:
# `_LOGGER` carries diagnostics for whoever configured the logging system, while
# the two helpers below carry the report a user asked for. Neither one configures
# logging, and no diagnostic is ever smuggled out through a print.
# =============================================================================

_FIELD_WIDTH: Final[int] = 23
"""Column width of the label in a ``  label   value`` detail line.

Wide enough for every label this script can emit, so the value column stays
aligned throughout. The two widest are supplied by the modules this script reads
its data from rather than written out here: ``failedScenariosNumber``, the
longest publisher-threshold key, and ``surefire_junit_xml_path``, the longest
artifact-location key. A longer label would still render correctly -- the format
below treats this as a minimum -- but its value would sit one column out.
"""

_INDENT: Final[str] = "    "
"""Indent of the per-feature and per-scenario listing lines."""


def _write(text: str = "") -> None:
    """Write one line of human-facing output to standard output."""
    print(text, file=sys.stdout)


def _write_error(text: str) -> None:
    """Write one line of human-facing output to standard error."""
    print(text, file=sys.stderr)


def _write_all(lines: Iterable[str]) -> None:
    """Write every line of a prepared human-facing section."""
    for line in lines:
        _write(line)


def _field(label: str, value: object) -> str:
    """Render one aligned ``label   value`` detail line."""
    return f"  {label:<{_FIELD_WIDTH}} {value}"


def _tally(total: int, counts: Mapping[str, int]) -> str:
    """Render ``total (status n, status n)`` with the statuses in a stable order."""
    if not counts:
        return str(total)
    breakdown = ", ".join(f"{status} {count}" for status, count in sorted(counts.items()))
    return f"{total} ({breakdown})"


def _tags(tags: Sequence[str]) -> str:
    """Render tag values exactly as the report recorded them.

    The report emits tags with the leading ``@`` already stripped, matching the
    source toolchain's own output, and hyphenated issue keys keep their hyphen.
    Nothing here re-adds a prefix or reshapes a tag.
    """
    return ", ".join(tags) if tags else "none"


def _step_text(step: NormalizedStep) -> str:
    """Render ``<keyword> <name>`` without altering either part.

    Step text carries preserved defects D1, D4 and D5, so it is never rewritten:
    the keyword and the name are joined with a single space and otherwise passed
    through byte for byte -- no translation, trimming, case folding or Unicode
    normalisation.
    """
    if step.keyword:
        return f"{step.keyword} {step.name}"
    return step.name


# =============================================================================
# Prepared sections: the publisher settings, the artifact layout and the two
# documented source commands.
# =============================================================================


def publisher_configuration_lines() -> list[str]:
    """Render the publisher settings this script was configured with.

    Every value comes from the frozen constants module, which quotes the single
    pipeline statement that declared them ``[Jenkins:L15]``. Printing them makes
    the parity contract observable from the command line: six thresholds of
    ``-1``, the ``ALPHABETICAL`` sort order and the include pattern, verbatim.

    The include pattern is the one leading-wildcard string the migration allows to
    survive, and it survives strictly AS DATA. It is reported here and asserted by
    the parity suite; it is never expanded against the filesystem, never handed to
    a pattern-matching or regular-expression engine, and never rewritten. Matching
    files is the CI publisher's business, which is why this script addresses the
    one report by its path constant instead.

    Returns:
        The section's lines, heading first.
    """
    lines = ["Publisher configuration [Jenkins:L15]"]
    lines.extend(_field(name, limit) for name, limit in PUBLISHER_THRESHOLDS.items())
    lines.append(_field("sortingMethod", thresholds.REPORT_SORTING_METHOD))
    lines.append(_field("fileIncludePattern", thresholds.REPORT_FILE_INCLUDE_PATTERN))
    lines.append(
        _field("quality gate", "none -- every threshold is -1, so nothing gates the build")
    )
    return lines


def documented_command_lines() -> list[str]:
    """Render the two documented source commands beside the actions that port them.

    The commands are quoted with the EN DASH corrected to an ASCII double hyphen,
    which is the deliberate Rule T5 correction explained in this module's
    docstring, and neither string hard-codes a path: both are assembled from the
    plugin declaration each adapter owns.

    Returns:
        The section's lines, heading first.
    """
    return [
        "Documented source commands [README.md:L157, L161]",
        _field(f"--artifact {ACTION_HTML}", DOCUMENTED_HTML_COMMAND),
        _field(f"--artifact {ACTION_RERUN}", DOCUMENTED_TXT_COMMAND),
        _field(f"--artifact {ACTION_PRETTY}", PRETTY_PLUGIN_DECLARATION),
    ]


def artifact_location_lines(base_dir: Path | None = None) -> list[str]:
    """Render every artifact location the layout module owns.

    Args:
        base_dir: Optional directory the artifact root sits inside, which
            re-roots the whole layout at once.

    Returns:
        The section's lines, heading first. Paths are rendered with forward
        slashes on every platform, so the CI publisher's include pattern stays
        valid against them.
    """
    lines = ["Artifact locations (app/utils/paths.py)"]
    lines.extend(_field(name, to_posix(path)) for name, path in artifact_paths(base_dir).items())
    return lines


def ensure_artifact_layout(base_dir: Path | None = None) -> bool:
    """Create the artifact tree before anything is written into it.

    This closes the port's most consequential silent failure: the Cucumber-JSON
    writer does not create its parent directory, and the artifact root is absent
    both in a fresh checkout and after the clean step. Creation is delegated to
    the layout module -- the single owner of that knowledge -- so no directory is
    ever created from here, and nothing is ever deleted from here either: wiping
    the tree belongs to the clean target.

    Args:
        base_dir: Optional directory the artifact root should sit inside.

    Returns:
        ``True`` when every managed directory exists. A partial result is
        reported to the user and logged, but is not treated as a failure of this
        script: each adapter reports its own write outcome, so a genuinely
        unwritable location surfaces there with a precise reason.
    """
    expected = managed_directories(base_dir)
    created = ensure_target_layout(base_dir)
    if len(created) == len(expected):
        _LOGGER.debug("Artifact layout ready: %d directories", len(created))
        return True
    _write(
        _field(
            "layout",
            f"WARNING only {len(created)} of {len(expected)} artifact directories are available",
        )
    )
    return False


# =============================================================================
# How usable is the report? Classified once, from the adapter's own verdict.
#
# Three states matter, and conflating any two of them would change observable
# behaviour:
#
#   * ABSENT -- the report has not been generated yet. The ordinary state, and
#     never an error in itself.
#   * UNUSABLE -- the report exists but yielded nothing: empty, not JSON, or JSON
#     that is not an array of feature objects. That is a genuine failure.
#   * USABLE -- everything else, including a well-formed report that recorded no
#     scenario (the ordinary outcome of the preserved default tag expression) and
#     an off-schema report that still carries scenarios, which the adapters
#     publish anyway because the source system would have published it too.
# =============================================================================


def source_is_absent(report: CucumberJsonReport) -> bool:
    """Whether the Cucumber JSON report has not been generated yet."""
    return report.is_absent


def source_is_usable(report: CucumberJsonReport) -> bool:
    """Whether anything can be derived from the Cucumber JSON report.

    An off-schema report that still carries scenarios counts as usable: the
    adapters derive from it and record the schema finding, because withholding
    real data would diverge from the non-gating source system. Only a report that
    yielded nothing at all is unusable.
    """
    if report.is_absent:
        return False
    return not report.is_invalid or report.summary.scenario_count > 0


def _is_producer_failure(*, source_absent: bool, explicitly_requested: bool) -> bool:
    """Whether a producible artifact that was not produced is a failure.

    "Not generated yet" is the normal state, so a default run over an absent
    report is a success -- that is what keeps the wired-up ``make report`` target
    working before the suite has ever run. Asking for a derived artifact by name
    and not getting it is a failure, and so is any other reason for not producing
    one (an unusable report, or a filesystem that refused the write).

    Args:
        source_absent: Whether the Cucumber JSON report has not been generated yet.
        explicitly_requested: Whether the user named this artifact on the command
            line rather than taking the everything-obtainable default.

    Returns:
        ``True`` when the outcome should make this script exit non-zero.
    """
    return explicitly_requested or not source_absent


# =============================================================================
# The four artifact actions. Each one delegates to the adapter that owns its
# artifact and reports what came back; none of them reimplements an adapter.
# =============================================================================

_RUN_TESTS_HINT: Final[str] = (
    "run the test suite first (make test) -- note that the preserved default tag "
    "expression selects no scenario, so a default run records none"
)
"""Guidance printed whenever the Cucumber JSON report has not been generated yet."""


def report_cucumber_json(report: CucumberJsonReport) -> bool:
    """Report what the Cucumber JSON report recorded.

    The report is loaded, validated and normalized by
    ``app/reporting/cucumber_json.py``, which owns the frozen three-level schema,
    merges the several feature objects a parallel run emits for one feature, and
    reports absence, unreadability, malformed content and schema findings as
    outcomes instead of raising. This function only presents that verdict: it
    never parses the document itself, never restates the schema, and above all
    never writes the report -- the run's native Cucumber-JSON writer does.

    Features are listed in the publisher's ALPHABETICAL order and scenarios by
    their ``<uri>:<line>`` coordinate, never by name (preserved defect D9 makes
    names non-unique). Step text is printed exactly as recorded, which is what
    preserves defects D1, D4 and D5.

    Args:
        report: The already-loaded report, so that one run reads the document once.

    Returns:
        ``True`` unless the report exists but yielded nothing usable, which is the
        malformed-input case and a genuine failure.
    """
    _write("Cucumber JSON report -- written by the test run's Cucumber-JSON writer")
    _write(_field("path", to_posix(report.path)))
    _write(_field("status", report.status))

    if source_is_absent(report):
        _write(_field("detail", report.reason or "not generated yet"))
        _write(_field("next step", _RUN_TESTS_HINT))
        return True

    if not source_is_usable(report):
        detail = report.reason or "the report yielded no features"
        _write(_field("detail", detail))
        _write_error(f"[{PROGRAM_NAME}] unusable Cucumber JSON report: {detail}")
        _LOGGER.warning("Unusable Cucumber JSON report at %s: %s", to_posix(report.path), detail)
        return False

    if report.reason is not None:
        # Off-schema but still carrying scenarios. The adapters publish it anyway,
        # so the finding is surfaced rather than hidden or treated as fatal.
        _write(_field("schema finding", report.reason))

    summary = report.summary
    _write(_field("features", summary.feature_count))
    _write(_field("scenarios", _tally(summary.scenario_count, summary.scenario_status_counts)))
    _write(_field("steps", _tally(summary.step_count, summary.step_status_counts)))
    _write(_field("failed scenarios", summary.failed_scenario_count))
    _write(_field("tags", _tags(summary.tags)))
    _write(
        _field(
            "sorting",
            f"{thresholds.REPORT_SORTING_METHOD} -- features listed by name",
        )
    )

    if summary.is_empty:
        _write(_field("detail", "the report recorded no scenario, which is the expected"))
        _write(_field("", "outcome of the preserved default tag expression"))
        return True

    for feature in sort_features(report.normalized.features):
        _write(f"{_INDENT}{feature.keyword or 'Feature'}: {feature.name}")
        _write(
            f"{_INDENT}  {feature.uri}:{feature.line}  {feature.status}  "
            f"[{_tags(feature.tags)}]"
        )
        for scenario in feature.scenarios:
            # Keyed and labelled by coordinate, never by name: five scenarios
            # legitimately share one name (preserved defect D9).
            _write(f"{_INDENT}  {scenario.location}  {scenario.status}  {scenario.name}")
            if scenario.tags:
                _write(f"{_INDENT}    tags: {_tags(scenario.tags)}")
            for step in scenario.failed_steps:
                _write(f"{_INDENT}    failed step: {_step_text(step)}")
    return True


def report_html_artifact(base_dir: Path | None = None) -> bool:
    """Locate and describe the Cucumber HTML report. Never generates it.

    This is the boundary the whole reporting design turns on: the HTML report is
    served as a static artifact and is NOT re-rendered, because re-rendering it
    would risk diverging from the source's output. The adapter this delegates to
    is a locator and describer with no generation code at all, and this function
    adds none: it reports presence, size, modification time and content type, and
    says which part of the toolchain produces the file. The documented source
    command produced it as part of the test run too, which is exactly what the
    ported test configuration reproduces.

    Args:
        base_dir: Optional directory the artifact root sits inside.

    Returns:
        Always ``True``. This script cannot produce this artifact, so its absence
        can never be a failure of this script -- it is reported, not fabricated.
    """
    artifact: HtmlReportArtifact = describe_html_report(base_dir)
    _write(f"Cucumber HTML report -- written by the test run ({HTML_PRODUCER_DISTRIBUTION})")
    _write(_field("path", artifact.posix_path))
    _write(_field("status", artifact.status))
    _write(_field("detail", artifact.detail))
    if artifact.exists:
        _write(_field("bytes", artifact.size_bytes))
        _write(_field("content type", artifact.content_type))
        _write(_field("self contained", artifact.self_contained))
        if artifact.modified_at is not None:
            _write(_field("modified", artifact.modified_at.isoformat()))
    else:
        _write(_field("next step", _RUN_TESTS_HINT))
    _write(_field("note", "never generated or re-rendered here; served as-is"))
    return True


def produce_rerun_manifest(source: Path, destination: Path) -> bool:
    """Produce the rerun manifest, by delegating to the adapter that owns it.

    No test-runner option emits this artifact, so if this is not called the file
    never appears. The adapter derives one ``<uri>:<line>`` coordinate per scenario
    containing a failed step -- failed only, never skipped, pending or undefined --
    de-duplicated, deterministically ordered and LF-terminated, and writes a
    zero-byte file when nothing failed. None of that logic is repeated here: a
    second derivation would be a second source of truth that could drift.

    Args:
        source: The Cucumber JSON report to derive from.
        destination: Where the manifest should be written.

    Returns:
        Whether a manifest file exists as a result -- ``True`` for a written
        manifest and for the zero-byte one that a run without failures produces.
    """
    manifest: RerunManifest = generate_rerun_manifest(source=source, destination=destination)
    _write(f"Rerun manifest -- {RERUN_PLUGIN_DECLARATION}")
    _write(_field("path", manifest.posix_path))
    _write(_field("status", manifest.status))
    _write(_field("detail", manifest.detail))

    if manifest.wrote_file:
        _write(_field("lines", manifest.line_count))
        _write(_field("bytes", manifest.byte_count))
        for location in manifest.locations:
            _write(f"{_INDENT}{location}")
        return True

    if manifest.reason is not None:
        _write(_field("reason", manifest.reason))
    if not manifest.has_source:
        _write(_field("next step", _RUN_TESTS_HINT))
    return False


def produce_pretty_reports(
    source: Path,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
) -> bool:
    """Produce the PrettyReports directory, by delegating to the adapter that owns it.

    No test-runner option emits this artifact either: the reporter that would have
    done so cannot run alongside the parallel execution the port preserves, so the
    directory is rendered from the JSON report -- which is what the source's own
    reporting plugin effectively did as well. The adapter is deterministic and
    idempotent, escapes every piece of report content so that literal placeholder
    text stays visible rather than becoming markup (preserved defect D1), names its
    documents positionally rather than after a scenario name (preserved defect D9),
    embeds no clock reading, host name, absolute path or run identifier, and
    refuses any output location outside the artifact root. Nothing here renders,
    writes or deletes a single byte of it.

    Args:
        source: The Cucumber JSON report to post-process.
        output_dir: Optional explicit output directory, which must resolve inside
            the artifact root.
        base_dir: Optional directory the artifact root sits inside.

    Returns:
        Whether the directory was produced -- ``True`` for a rendered directory and
        for the valid, browsable one a report with no scenarios yields.
    """
    result: PrettyReportsResult = generate_pretty_reports(
        source=source,
        output_dir=output_dir,
        base_dir=base_dir,
    )
    _write(f"PrettyReports directory -- {PRETTY_PLUGIN_DECLARATION}")
    _write(_field("path", to_posix(result.output_dir)))
    _write(_field("status", result.status))
    _write(_field("detail", result.description))

    if result.successful:
        _write(_field("documents", len(result.files)))
        for name in result.files:
            _write(f"{_INDENT}{name}")
        if result.removed:
            _write(_field("pruned", ", ".join(result.removed)))
        return True

    if result.reason is not None:
        _write(_field("reason", result.reason))
    _write(_field("next step", _RUN_TESTS_HINT))
    return False


# =============================================================================
# The command-line interface.
# =============================================================================


def resolve_actions(selected: Sequence[str] | None) -> tuple[tuple[str, ...], frozenset[str]]:
    """Work out which artifact actions to run, and which were named explicitly.

    Args:
        selected: The raw ``--artifact`` values, or ``None`` when the option was
            not given.

    Returns:
        ``(actions, explicit)``. *actions* is always in the canonical execution
        order, so output is stable however the options were ordered on the command
        line. *explicit* holds the artifact names the user actually typed, which is
        what distinguishes "produce everything obtainable" from "produce this
        artifact, and tell me if you cannot" -- the whole basis of the exit-code
        policy.
    """
    if not selected:
        return ARTIFACT_ACTIONS, frozenset()
    named = frozenset(selected)
    if ACTION_ALL in named:
        return ARTIFACT_ACTIONS, named - {ACTION_ALL}
    return tuple(action for action in ARTIFACT_ACTIONS if action in named), named


def _epilog() -> str:
    """Compose the ``--help`` epilog from the ported source values.

    Every command and declaration below is assembled from the adapters' own
    constants, so the help text cannot drift from the artifacts it describes.
    """
    return "\n".join(
        (
            "artifact actions:",
            f"  {ACTION_ALL:<7} report on all four artifacts and produce the two that can be",
            f"  {'':<7} produced. This is the default, and what 'make report' runs.",
            f"  {ACTION_JSON:<7} read, validate and summarise the Cucumber JSON report. Written by",
            f"  {'':<7} the test run, never by this script.",
            f"  {ACTION_HTML:<7} locate and describe the Cucumber HTML report -- the artifact the",
            f"  {'':<7} first documented command below produced. Never generated or",
            f"  {'':<7} re-rendered here: it is served exactly as the test run wrote it.",
            f"  {ACTION_RERUN:<7} produce the rerun manifest -- the artifact the second documented",
            f"  {'':<7} command below produced. No test-runner option emits it.",
            f"  {ACTION_PRETTY:<7} produce the PrettyReports directory for the declaration",
            f"  {'':<7} '{PRETTY_PLUGIN_DECLARATION}'.",
            f"  {'':<7} No test-runner option emits it either.",
            "",
            "documented source commands [README.md:L157, L161], EN DASH corrected to --:",
            f"  {DOCUMENTED_HTML_COMMAND}",
            f"  {DOCUMENTED_TXT_COMMAND}",
            "",
            "exit codes:",
            "  0  success. This includes every 'not generated yet' outcome, a report",
            "     that recorded no scenario at all, and a report full of failing",
            "     scenarios: publishing reports is non-gating, exactly as the source",
            "     pipeline was.",
            "  1  a genuine failure of this script: a report that exists but yielded",
            "     nothing usable, a write the filesystem refused, or an explicitly",
            "     requested artifact that could not be produced.",
            "",
            "This script never runs the tests. Produce the artifacts with 'make test'",
            "(or scripts/run_tests.sh), then post-process them here.",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser whose ``--help`` documents every action, quotes both documented
        source commands with the Rule T5 correction applied, and states the
        exit-code policy.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Report on the four Cucumber report artifacts and produce the two that no "
            "test-runner option can produce. Ports the two report commands documented "
            "at [README.md:L157, L161]."
        ),
        epilog=_epilog(),
    )
    parser.add_argument(
        "-a",
        "--artifact",
        action="append",
        choices=ACTION_CHOICES,
        help=(
            "artifact to act on; repeatable. Defaults to every artifact, which "
            "reports on all four and produces the two that can be produced."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        metavar="PATH",
        help=(
            "Cucumber JSON report to read and derive from. Defaults to the location "
            "app/utils/paths.py owns."
        ),
    )
    parser.add_argument(
        "--rerun-output",
        type=Path,
        metavar="PATH",
        help="where to write the rerun manifest. Defaults to the location app/utils/paths.py owns.",
    )
    parser.add_argument(
        "--pretty-output",
        type=Path,
        metavar="DIR",
        help=(
            "where to write the PrettyReports directory. Defaults to the location "
            "app/utils/paths.py owns; a location outside the artifact root is refused."
        ),
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        metavar="DIR",
        help=(
            "directory the artifact root sits inside, which re-roots every default "
            "location at once. The root itself is always named 'target'."
        ),
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help=(
            "print the publisher settings and every artifact location, then stop "
            "without reading, writing or creating anything."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    The whole flow is: resolve the requested actions and locations, print the
    publisher settings so the parity contract is observable, make sure the
    artifact tree exists, read the report once, then hand each artifact to the
    adapter that owns it and present what came back.

    Args:
        argv: Argument list to parse. ``None`` -- the default -- reads the process
            arguments, which is what an ordinary invocation does; a test passes an
            explicit list.

    Returns:
        A process exit code: ``0`` on success, ``1`` on a genuine failure of this
        script. A report that records failing scenarios is a success: report
        publication is non-gating, so nothing about the tests' outcome reaches
        this value.
    """
    args = build_parser().parse_args(argv)

    base_dir: Path | None = args.base_dir
    actions, explicit = resolve_actions(args.artifact)
    # Every default location comes from the layout the utility module owns, so the
    # artifact root is never spelled out here.
    source: Path = report_path(base_dir) if args.source is None else Path(args.source)
    rerun_output: Path = (
        rerun_manifest_path(base_dir) if args.rerun_output is None else Path(args.rerun_output)
    )
    pretty_output: Path | None = None if args.pretty_output is None else Path(args.pretty_output)

    _write(f"{PROGRAM_NAME} -- Cucumber report artifacts")
    _write()
    _write_all(publisher_configuration_lines())
    _write()
    _write_all(documented_command_lines())
    _write()

    if args.show_config:
        # Pure introspection: nothing is read, written or created on this path.
        _write_all(artifact_location_lines(base_dir))
        return EXIT_SUCCESS

    # Criterion V7: the artifact tree must exist before anything is written into
    # it, because the Cucumber-JSON writer does not create its own parent
    # directory and the tree is absent in a fresh checkout.
    ensure_artifact_layout(base_dir)

    # Read the document once, through the adapter that owns the schema and the
    # graceful-absence semantics, and classify it once. The producing adapters
    # reload it themselves -- they are the authority on their own inputs -- and
    # this copy is what decides whether an unproduced artifact is a failure.
    report = load_report(source)
    absent = source_is_absent(report)
    unproduced: list[str] = []

    for action in actions:
        if action == ACTION_JSON and not report_cucumber_json(report):
            unproduced.append(action)
        if action == ACTION_HTML:
            report_html_artifact(base_dir)
        if action == ACTION_RERUN and not produce_rerun_manifest(source, rerun_output):
            if _is_producer_failure(
                source_absent=absent,
                explicitly_requested=action in explicit,
            ):
                unproduced.append(action)
        if action == ACTION_PRETTY and not produce_pretty_reports(source, pretty_output, base_dir):
            if _is_producer_failure(
                source_absent=absent,
                explicitly_requested=action in explicit,
            ):
                unproduced.append(action)
        _write()

    if not absent and report.summary.has_failures:
        # PRESERVED DEFECT D3: the source build could not fail and its report stage
        # ran unconditionally after the test stage, so failing scenarios are
        # published and change nothing about this script's verdict.
        _write(
            "The report records failing scenarios. Publication is non-gating, so this "
            "run is a success."
        )

    if unproduced:
        summary = ", ".join(unproduced)
        _write(f"{PROGRAM_NAME}: could not complete: {summary}")
        _write_error(f"[{PROGRAM_NAME}] could not complete: {summary}")
        _LOGGER.warning("Report generation could not complete: %s", summary)
        return EXIT_FAILURE

    _write(f"{PROGRAM_NAME}: every requested artifact is accounted for.")
    return EXIT_SUCCESS


if __name__ == "__main__":
    # The only place a process exit code is produced. Helpers return values; they
    # never exit, which is what keeps every one of them callable from a test.
    raise SystemExit(main())
