#!/bin/sh
# ===========================================================================
# scripts/run_tests.sh - POSIX entry point for the Testinium-QA test run.
#
# WHAT THIS FILE IS
# -----------------
# The payload of the Jenkins pipeline's POSIX branch. Jenkins:6-12 declares
#
#     stage('Run tests'){
#         if(isUnix()){
#             sh "mvn clean test"      <- Jenkins:8, replaced by THIS script
#         } else {
#             bat "mvn clean test"     <- Jenkins:10, scripts/run_tests.ps1
#         }
#     }
#
# and after the Python port that first branch reads
#
#     sh "sh scripts/run_tests.sh"
#
# The branch itself, the stage names and their order are the pipeline's and
# are unchanged: shell selection stays with isUnix(), so this file is a
# payload and not a platform abstraction. The report publisher is a separate
# later stage (Jenkins:15) and owns all thresholding - its six thresholds are
# -1 and its sorting is ALPHABETICAL - so nothing here thresholds, sorts,
# inspects or post-processes a report.
#
# HOW IT IS INVOKED, AND THE TWO CONSEQUENCES
# -------------------------------------------
# In `sh "sh scripts/run_tests.sh"` the outer sh is the Jenkins step and the
# inner one runs this file as an ARGUMENT to /bin/sh. Therefore:
#   1. No executable bit is required or assumed. This file runs correctly at
#      mode 644, which is exactly why the pipeline spells the command that
#      way: the checkout needs no chmod.
#   2. The shebang above does NOT select the interpreter - /bin/sh does,
#      whatever /bin/sh happens to be. On Debian and Ubuntu agents it is
#      dash, so this file is strict POSIX sh with no bashisms anywhere. The
#      shebang is kept for documentary value and for a developer running
#      ./scripts/run_tests.sh directly.
#
# CONFIGURATION SURFACE: one environment variable, and no options of its own
# --------------------------------------------------------------------------
#   PYTHON  Optional. An interpreter to probe FIRST when locating the pinned
#           runtime in step 1, for example
#               PYTHON=/usr/local/bin/python3.14 sh scripts/run_tests.sh
#           It chooses which interpreter is tried first. It does NOT relax
#           the version pin: the identical exact-version check is applied to
#           it, and a non-matching PYTHON is reported and rejected like any
#           other candidate.
#
# Every argument this script receives is forwarded verbatim to the run-tests
# console script in step 5, and nowhere else. No option is defined, defaulted
# or interpreted here, so with no arguments - exactly how Jenkins invokes it -
# the behaviour is identical to invoking run-tests bare: the @Smoke default
# from behave.ini and the --clean default from app/cli.py stay in force.
#
# THE FIVE STEPS, IN ORDER
# ------------------------
#   1. Locate a Python 3.14.6 interpreter, or fail loudly.
#   2. Create .venv with that interpreter if it is missing; refuse a drifted
#      one rather than replacing it.
#   3. Install the pinned dependencies, then this project itself (editable).
#   4. Run the pytest unit gate.
#   5. exec the run-tests console script out of .venv/bin.
#
# EXIT STATUS: two different semantics, deliberately not blurred
# -------------------------------------------------------------
#   * Bootstrap failures of this script - the working directory, steps 1 to 3
#     and a missing entry point in step 5 - exit 1. app/cli.py never returns
#     1: its published set is 0, 2, 3, 4 and 5, with 1 left out on purpose.
#     So a 1 from this stage always means the bootstrap failed and never that
#     the suite reported something.
#   * The unit gate in step 4 PROPAGATES pytest's own status. It is a real
#     quality gate. pytest's exit code 5, "no tests collected", is forwarded
#     unchanged too, because a unit gate that collects nothing is a real
#     problem rather than a pass.
#   * The suite run in step 5 propagates the run-tests status UNALTERED.
#     A test outcome never reaches it: pom.xml:25 sets
#     <testFailureIgnore>true</testFailureIgnore> and all six publisher
#     thresholds on Jenkins:15 are -1, so failing scenarios, errors,
#     undefined or skipped steps, a browser that fails to start, an
#     unrecognised browser value, a feature that fails to parse, a missing or
#     malformed rerun manifest and a tag expression that selects nothing all
#     exit 0 with the artifacts written. A non-zero status there means a
#     usage error, a dead worker, an empty merge or a failed writer - exactly
#     the classes that must reach Jenkins. Nothing in this file suppresses,
#     swallows, remaps or adds to that status.
#
# `set -e` is deliberately NOT used: under it the idiomatic `cmd; s=$?`
# aborts before the assignment, which pushes authors towards `set +e` around
# the very command whose status has to survive. Every command's status is
# checked explicitly instead, which keeps the two semantics above visible in
# the code rather than implied by shell options. `set -u` is likewise unused:
# every environment variable used below carries its own ${VAR:-} default, so
# it would add no protection, and it carries a needless empty-"$@" hazard in
# older shells.
#
# DELIBERATELY ABSENT
# -------------------
# No Maven invocation - there is no Maven build after the port and pom.xml is
# retained as historical reference only. No make: it is optional developer
# convenience and CI must never depend on it, as the Makefile's own header
# states. No report artifact path of any kind - app/utils/paths.py is their
# sole owner - and no emptying or inspection of the build output directory,
# which is the runner's --clean, on by default. No --tags, --browser,
# --workers or any other option value. No git command: the pipeline performs
# its own checkout. No browser or driver installation: the browser is an
# operator prerequisite and webdriver-manager provisions the driver from
# inside the application at run time. Nothing creates, copies or overwrites
# configuration.properties, which is operator-supplied and git-ignored, with
# configuration.properties.example the committed template. No container, no
# production WSGI server and no HTTP surface: the Flask viewer is read-only
# and started separately.
# ===========================================================================

# The pinned interpreter version, exact. Its other two homes are
# .python-version (3.14.6) and pyproject.toml (requires-python = "==3.14.*");
# this literal is the third and last place it appears. It is compared with
# string equality on purpose, so that no other 3.14.x and no 3.15 can satisfy
# it - the support range is narrow by design, so that CI and development
# cannot drift apart.
REQUIRED_PYTHON_VERSION='3.14.6'

# Accumulates one line per interpreter candidate probed in step 1, for the
# diagnostic printed when none of them matches.
PROBE_REPORT=''


# --------------------------------------------------------------------------
# Helpers. POSIX shell functions only: no `local`, which is not in POSIX, so
# every variable a helper sets is script-scoped and prefixed to keep it
# distinguishable from the caller's own.
# --------------------------------------------------------------------------

# Print "major.minor.micro" for the interpreter named in $1, or print nothing
# if it cannot be run. stderr is discarded so that a broken candidate - a
# stale symlink, a wrapper that emits a warning - contributes noise neither
# to the captured value nor to the log.
interpreter_version() {
    "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null
}

# Append one already-indented line to the candidate report.
probe_note() {
    if [ -z "$PROBE_REPORT" ]; then
        PROBE_REPORT="  $1"
    else
        PROBE_REPORT="$PROBE_REPORT
  $1"
    fi
}

# Return 0 if the interpreter named in $1 resolves AND reports exactly the
# required version; otherwise record what it actually reported and return 1.
# This one test is applied to every candidate including PYTHON, which is what
# makes the override incapable of relaxing the pin.
probe_interpreter() {
    if ! command -v "$1" >/dev/null 2>&1; then
        probe_note "$1: not found on PATH"
        return 1
    fi
    probe_resolved=$(command -v "$1")
    probe_version=$(interpreter_version "$1")
    if [ -z "$probe_version" ]; then
        probe_note "$1 ($probe_resolved): found, but reported no version"
        return 1
    fi
    if [ "$probe_version" = "$REQUIRED_PYTHON_VERSION" ]; then
        return 0
    fi
    probe_note "$1 ($probe_resolved): reported $probe_version"
    return 1
}


# --------------------------------------------------------------------------
# Working directory: the repository root, derived from this script's own
# location so that the run is identical whether Jenkins invokes it from the
# workspace root or a developer invokes it from a subdirectory.
#
# This is load-bearing rather than cosmetic. app/utils/properties.py opens
# configuration.properties by BARE RELATIVE FILENAME, reproducing the
# working-directory semantics of ConfigurationReader.java:14, so the run has
# to happen with the repository root as the working directory. The manifests
# and the project directory named in step 3 are relative for the same reason.
# --------------------------------------------------------------------------
script_dir=$(dirname "$0")
case "$script_dir" in
    # Guard the pathological case of a leading dash, which cd would treat as
    # an option. `--` is avoided because not every sh builtin accepts it.
    -*) script_dir="./$script_dir" ;;
    # Every other shape - an absolute path, a relative path, or the "." that
    # dirname returns when the script is invoked from its own directory - is
    # usable as it stands.
    *) ;;
esac

cd "$script_dir/.." || {
    printf '%s\n' \
        "run_tests.sh: cannot change to the repository root." \
        "  script location : $0" \
        "  directory tried : $script_dir/.." \
        "Run this script from a complete checkout, as either" \
        "  sh scripts/run_tests.sh" \
        "from the repository root or with any path that reaches it." >&2
    exit 1
}

# Pre-flight: the three files step 3 installs from. Checking them here turns
# an obscure installer error into an actionable one, and confirms that the
# directory reached above really is the repository root.
missing_manifest=''
for manifest in pyproject.toml requirements.txt requirements-test.txt; do
    if [ ! -f "$manifest" ]; then
        missing_manifest="$missing_manifest $manifest"
    fi
done
if [ -n "$missing_manifest" ]; then
    printf '%s\n' \
        "run_tests.sh: this does not look like a complete checkout." \
        "  working directory : $(pwd)" \
        "  missing file(s)   :$missing_manifest" \
        "All three are tracked in the repository and are required to install" \
        "the pinned dependencies and this project itself. Restore the" \
        "checkout and re-run." >&2
    exit 1
fi


# --------------------------------------------------------------------------
# Step 1 - locate a Python 3.14.6 interpreter, or fail loudly.
#
# Candidates in order: PYTHON when it is set and non-empty, then python3.14,
# python3, python. The first EXACT match wins. A silent fall back to whatever
# python3 resolves to is the specific failure this step exists to prevent, so
# a near miss is reported and rejected, never used.
# --------------------------------------------------------------------------
PYTHON_BIN=''

if [ -n "${PYTHON:-}" ]; then
    if probe_interpreter "$PYTHON"; then
        PYTHON_BIN="$PYTHON"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    for candidate in python3.14 python3 python; do
        # Skip a name already probed as PYTHON, so the report lists it once.
        if [ "$candidate" = "${PYTHON:-}" ]; then
            continue
        fi
        if probe_interpreter "$candidate"; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    printf '%s\n' \
        "run_tests.sh: no Python $REQUIRED_PYTHON_VERSION interpreter found." \
        "" \
        "This project is pinned to Python $REQUIRED_PYTHON_VERSION exactly:" \
        "  .python-version   $REQUIRED_PYTHON_VERSION" \
        "  pyproject.toml    requires-python = \"==3.14.*\"" \
        "" \
        "Interpreters probed, in order, and what each reported:" \
        "$PROBE_REPORT" \
        "" \
        "No fallback to a different interpreter is performed. That is" \
        "deliberate: falling back would let CI and development drift apart" \
        "silently, which is the one thing the pin exists to prevent." \
        "" \
        "To fix this, either" \
        "  * install Python $REQUIRED_PYTHON_VERSION and put it on PATH, or" \
        "  * point the PYTHON environment variable at an absolute path to a" \
        "    $REQUIRED_PYTHON_VERSION interpreter, for example" \
        "        PYTHON=/usr/local/bin/python3.14 sh scripts/run_tests.sh" \
        "    The same exact-version check applies to PYTHON: it selects" \
        "    which interpreter is tried first, it does not relax the pin." >&2
    exit 1
fi

printf '%s\n' "run_tests.sh: using $(command -v "$PYTHON_BIN") ($REQUIRED_PYTHON_VERSION)"


# --------------------------------------------------------------------------
# Step 2 - the virtual environment: create it when it is missing, refuse a
# drifted one.
#
# .venv at the repository root is the sanctioned location: .gitignore
# excludes both .venv/ and venv/, so creating it here leaves git status clean
# by design.
#
# An existing .venv built by some other interpreter is precisely the
# CI-versus-development drift the pin exists to prevent, so it is rejected.
# It is NOT deleted: silently destroying a developer's environment would be a
# destructive act nobody asked for, so the operator is told what to remove
# and the run stops.
# --------------------------------------------------------------------------
if [ -e .venv ] && [ ! -d .venv ]; then
    printf '%s\n' \
        "run_tests.sh: .venv exists but is not a directory." \
        "  path : $(pwd)/.venv" \
        "The virtual environment has to live there. Remove or rename that" \
        "entry and re-run; this script will not delete it for you." >&2
    exit 1
fi

if [ -d .venv ]; then
    venv_version=$(interpreter_version .venv/bin/python)
    if [ "$venv_version" != "$REQUIRED_PYTHON_VERSION" ]; then
        printf '%s\n' \
            "run_tests.sh: the existing .venv is not usable for this project." \
            "  required interpreter : $REQUIRED_PYTHON_VERSION" \
            "  .venv/bin/python     : ${venv_version:-no version reported (missing or not runnable)}" \
            "" \
            "A virtual environment built on another interpreter is exactly" \
            "the drift the version pin exists to prevent, so it is refused" \
            "rather than used. It is deliberately not deleted for you:" \
            "" \
            "    rm -rf .venv" \
            "" \
            "then re-run this script, which will rebuild it from the" \
            "$REQUIRED_PYTHON_VERSION interpreter located in step 1." >&2
        exit 1
    fi
    printf '%s\n' "run_tests.sh: reusing .venv ($REQUIRED_PYTHON_VERSION)"
else
    printf '%s\n' "run_tests.sh: creating .venv"
    "$PYTHON_BIN" -m venv .venv
    venv_status=$?
    if [ "$venv_status" -ne 0 ]; then
        printf '%s\n' \
            "run_tests.sh: failed to create the .venv virtual environment." \
            "  interpreter : $(command -v "$PYTHON_BIN")" \
            "  exit status : $venv_status" \
            "" \
            "On some distributions the venv and ensurepip modules ship" \
            "separately from the interpreter and have to be installed before" \
            "this works - the Debian and Ubuntu package is python3-venv." \
            "The error printed above this message comes from the" \
            "interpreter itself and names the missing piece." >&2
        exit 1
    fi
    # A venv that was created but carries no runnable interpreter would fail
    # later with a far less obvious error, so it is caught here.
    venv_version=$(interpreter_version .venv/bin/python)
    if [ "$venv_version" != "$REQUIRED_PYTHON_VERSION" ]; then
        printf '%s\n' \
            "run_tests.sh: .venv was created but has no usable interpreter." \
            "  required interpreter : $REQUIRED_PYTHON_VERSION" \
            "  .venv/bin/python     : ${venv_version:-no version reported (missing or not runnable)}" \
            "" \
            "Creation reported success, so this points at the environment" \
            "rather than at this script. Remove .venv with 'rm -rf .venv'," \
            "check the interpreter above, and re-run." >&2
        exit 1
    fi
fi


# --------------------------------------------------------------------------
# Step 3 - install the pinned dependencies, then this project itself.
#
# TWO separate installs, and both are required.
#
# The first is the dependency set: requirements.txt (the seven runtime pins)
# and requirements-test.txt (pytest and pytest-cov) in a SINGLE pip
# invocation, so the resolver sees both manifests at once and cannot pick a
# combination that satisfies one and breaks the other. Every version is
# exact-pinned in those files, which is why no --upgrade appears here and pip
# itself is not pre-upgraded: this step installs what the repository pins and
# nothing else. No index URL is set either, so pip uses whatever the agent is
# configured for.
#
# The second install is the project distribution, and step 5 cannot run
# without it: installing -r manifests installs DEPENDENCIES ONLY, while the
# run-tests console script declared in pyproject.toml under
#     [project.scripts] run-tests = "app.cli:run_tests"
# is materialised into .venv/bin/run-tests only when the distribution itself
# is installed. Editable (-e .) specifically, never a plain '.': CI has to
# execute the code in the checked-out workspace, whereas a non-editable
# install copies a snapshot into site-packages and could then run stale code
# against a fresh checkout. This install enables the specified behaviour; it
# does not extend it.
#
# pip is non-interactive by default. --quiet and --disable-pip-version-check
# keep the CI log to the point. No other environment variable is set here:
# output buffering on the run itself is app/cli.py's contract, not this
# script's.
# --------------------------------------------------------------------------
printf '%s\n' "run_tests.sh: installing pinned dependencies"
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt -r requirements-test.txt
deps_status=$?
if [ "$deps_status" -ne 0 ]; then
    printf '%s\n' \
        "run_tests.sh: installing the pinned dependencies failed." \
        "  manifests   : requirements.txt, requirements-test.txt" \
        "  exit status : $deps_status" \
        "" \
        "pip's own output above names the distribution that could not be" \
        "installed. Every version in both manifests is exact-pinned, so the" \
        "usual causes are an unreachable package index or a pin with no" \
        "distribution for this platform." >&2
    exit 1
fi

printf '%s\n' "run_tests.sh: installing the project (editable)"
.venv/bin/python -m pip install --quiet --disable-pip-version-check -e .
project_status=$?
if [ "$project_status" -ne 0 ]; then
    printf '%s\n' \
        "run_tests.sh: installing this project in editable mode failed." \
        "  project     : . (pyproject.toml in $(pwd))" \
        "  exit status : $project_status" \
        "" \
        "This install is what creates the .venv/bin/run-tests console" \
        "script, so the run cannot proceed without it. A failure here" \
        "usually means pyproject.toml's [build-system] section is missing" \
        "or misdeclares its build backend; that file is maintained" \
        "separately from this script." >&2
    exit 1
fi


# --------------------------------------------------------------------------
# Step 4 - the unit gate: this port's own test suite, and a real gate.
#
# Bare on purpose. pytest.ini owns test selection - testpaths = tests - which
# is what keeps the behave step definitions under features/steps/ out of the
# unit suite: they are glue matched by phrase at scenario run time and define
# no pytest tests. So no path argument is passed here, and no coverage flag
# either: the four per-package coverage gates (app/utils 90, app/pages 85,
# app/automation 80, app/reporting 80) live in the Makefile's coverage target
# and in exactly one place, which is not this file. make is not invoked from
# here for any target - CI must not depend on it being installed.
#
# The status PROPAGATES, unlike step 5's. pytest's exit code 5, "no tests
# collected", propagates as well: a unit gate that collects nothing has not
# passed. On failure the suite run is not started.
# --------------------------------------------------------------------------
printf '%s\n' "run_tests.sh: running the unit gate"
.venv/bin/python -m pytest
pytest_status=$?
if [ "$pytest_status" -ne 0 ]; then
    printf '%s\n' \
        "run_tests.sh: the unit gate failed (pytest exit status $pytest_status)." \
        "The test suite was NOT started, and this stage fails with pytest's" \
        "own status. pytest's output above identifies the failing tests;" \
        "exit status 5 means it collected no tests at all, which is a" \
        "failure of the gate rather than a pass." >&2
    exit "$pytest_status"
fi


# --------------------------------------------------------------------------
# Step 5 - the suite run, through the one sanctioned entry point.
#
# The run-tests console script from the virtual environment's bin directory,
# never the Flask CLI and never python -m: pyproject.toml declares this entry
# point and every caller - this script, scripts/run_tests.ps1, the Makefile
# and the README - reaches the runner through it.
#
# Its absence is a bootstrap failure rather than a test outcome, so it is
# checked first and reported as such.
# --------------------------------------------------------------------------
if [ ! -f .venv/bin/run-tests ] || [ ! -x .venv/bin/run-tests ]; then
    printf '%s\n' \
        "run_tests.sh: .venv/bin/run-tests is missing or not executable." \
        "  expected : $(pwd)/.venv/bin/run-tests" \
        "" \
        "That console script is declared in pyproject.toml as" \
        "    [project.scripts] run-tests = \"app.cli:run_tests\"" \
        "and is created by the editable install in step 3, which reported" \
        "success. Check that declaration and the packaging configuration" \
        "around it, then re-run." \
        "This is a bootstrap failure, not a test result." >&2
    exit 1
fi

# exec replaces this shell with the runner, which is the cleanest possible
# propagation of its exit status: the status Jenkins sees is the runner's own,
# with nothing after it that could alter, mask or add to it. The arguments are
# forwarded exactly as received - none is added, removed or defaulted here.
# Nothing may follow this line.
exec .venv/bin/run-tests "$@"
