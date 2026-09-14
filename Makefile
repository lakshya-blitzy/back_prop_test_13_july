# Makefile - Testinium-QA (Python 3.14 port)
#
# Developer command surface for the ported project: four targets wrapping the
# `run-tests` console script and pytest. It stands in for the Maven command
# surface of the Java build - pom.xml is retained as historical reference only
# and is no longer a supported build configuration - so the semantics behind
# two of these targets come from there:
#   pom.xml:25     surefire testFailureIgnore=true  -> the runner exit contract
#                                                      described under `test`,
#                                                      which is reproduced by
#                                                      the runner and never
#                                                      suppressed here
#   pom.xml:21-29  surefire parallel configuration  -> the runner --workers
#                                                      default (CPU count)
#   `clean test`   the lifecycle developers invoked -> `make clean` plus the
#                                                      runner --clean default
#
# make is OPTIONAL developer convenience and must never become a CI
# dependency. The pipeline (Jenkins, stage "Run tests") selects
# scripts/run_tests.sh or scripts/run_tests.ps1 through its own isUnix()
# branch and never invokes make. Those two scripts are self-bootstrapping, and
# this file deliberately offers no bootstrap target for anything to depend on.
#
# Targets - EXACTLY these four, which is the whole public surface:
#   test      run the Gherkin browser suite through the run-tests entry point
#             (the default goal, mirroring what `mvn test` was)
#   unit      run the pytest suite covering this port's own code
#   coverage  run the four per-package coverage gates; first miss fails
#   clean     remove generated output and caches
#
# Recipe lines are indented with a literal TAB, which make requires.
#
# Absent on purpose: no `help` target, because the four above are the whole
# surface the specification defines and a fifth public target is surface this
# file is not entitled to add; no bootstrap step the pipeline could come to
# depend on; no target that rebuilds report artifacts from stored results,
# which is not part of this port; no static-analysis or type-checking target,
# since no such tool is a pinned dependency (requirements-test.txt pins pytest
# and pytest-cov and nothing else); no container or deployment target; and no
# report artifact path literal, because those paths have exactly one owner,
# app/utils/paths.py. Removing the generated target/ directory wholesale is
# both the correct and the sufficient form.

# ---------------------------------------------------------------------------
# Interpreter and entry points
#
# The interpreter is Python 3.14.6 exactly, per .python-version and
# pyproject.toml (requires-python = "==3.14.*").
#
# Every command runs out of the project virtual environment, and ONLY out of
# it. There is deliberately no fall back to whatever `python3` or `run-tests`
# the PATH happens to provide: such a fall back is silent, and on a host whose
# python3 is some other version it runs the suite on an unsupported
# interpreter or an unrelated package of the same name. Each target that needs
# the environment instead pre-flights it and fails with an actionable message
# naming the bootstrap that creates it.
#
# The POSIX layout is the default; a Windows environment keeps its executables
# in Scripts, so point the location at it on the command line:
#     make unit VENV_BIN=.venv/Scripts
# (on Windows the pipeline runs scripts/run_tests.ps1, not make). That
# override, and any override of the three variables below, is still held to
# the version check in the pre-flight - it selects WHICH environment is used,
# it does not relax the pin.
# ---------------------------------------------------------------------------
VENV      ?= .venv
VENV_BIN  ?= $(VENV)/bin

# The pinned interpreter version, exact, and the only place this file states
# it. Its other homes are .python-version, pyproject.toml and the two runner
# scripts. String equality is the test, so no other 3.14.x satisfies it.
REQUIRED_PYTHON_VERSION := 3.14.6

PYTHON    ?= $(VENV_BIN)/python
PYTEST    ?= $(PYTHON) -m pytest
RUN_TESTS ?= $(VENV_BIN)/run-tests

# Extra options forwarded to the suite runner, e.g.
#     make test ARGS=--dry-run
#     make test ARGS='--workers 1 --dry-run'
#     make test ARGS="--tags '@Smoke and not @Wip'"
#
# ARGS is caller-controlled text, so it is handled as DATA from end to end and
# is never shell source, never a make expression, and never split by the
# shell. Three mechanisms give it a real argv boundary:
#
#   1. `$(value ARGS)` captures the caller's text unexpanded and `override :=`
#      stores it verbatim, so make itself never evaluates a function, a
#      variable reference or anything else inside it.
#   2. `export` hands that text to the recipe through the ENVIRONMENT. It is
#      never interpolated into the recipe as `$(ARGS)`, so there is no point
#      at which a `;`, `&&`, `|` or backquote in it could be parsed as a
#      shell operator.
#   3. The `test` recipe reads it in the pinned interpreter and splits it with
#      `shlex.split`, whose grammar is POSIX shell QUOTING without any of
#      shell's evaluation - no operators, no substitution, no globbing - then
#      hands the resulting list to the runner as a real argv array. Plain
#      field splitting would not do: it would break `--tags '@Smoke and not
#      @Wip'` into five arguments where the CLI needs the expression as one
#      value, which is the whole reason a quoting grammar is used here.
#
# Measured, with the runner replaced by an argv printer:
#     ARGS=--dry-run                         -> 1 argument
#     ARGS='--workers 1 --dry-run'           -> 3 arguments
#     ARGS="--tags '@Smoke and not @Wip'"    -> 2, the second being
#                                               [@Smoke and not @Wip]
#     ARGS='--dry-run; printf INJECTED'      -> 3 arguments, no second command
#     ARGS='*'                               -> 1 argument, still [*]
#     ARGS= (unset or empty)                 -> no arguments at all
# An unbalanced quote is reported as a usage error rather than guessed at.
#
# The interpreter here is a quoting helper that builds argv and hands over; it
# is NOT how the runner is reached. `$(RUN_TESTS)`, the console script from the
# virtual environment, is still the only thing executed as the runner - never
# the Flask CLI, never `python -m app.cli`.
override ARGS := $(value ARGS)
export ARGS

# ---------------------------------------------------------------------------
# Pre-flight checks, used by the three targets that need the environment.
#
# These are canned recipes rather than targets: a fifth public target is
# surface this file is not entitled to add, and nothing - least of all CI -
# may come to depend on a bootstrap step here. `clean` deliberately uses
# neither, so it works on a checkout that has no environment at all.
# ---------------------------------------------------------------------------

# Fail unless $(1) exists and is executable, naming the bootstrap that creates
# it. Messages avoid the apostrophe so the single-quoted forms below stay
# valid POSIX shell.
define require_executable
if [ ! -x '$(1)' ]; then \
    printf '%s\n' \
        'make: $(1) is missing or not executable.' \
        '' \
        'Every target here runs out of the project virtual environment and' \
        'never falls back to a PATH command, so this is a hard stop rather' \
        'than a warning. Create the environment with the same bootstrap CI' \
        'uses, which is idempotent and safe to repeat:' \
        '' \
        '    sh scripts/run_tests.sh' \
        '' \
        'or, to build it without starting a suite run:' \
        '' \
        '    python3.14 -m venv $(VENV)' \
        '    $(VENV_BIN)/python -m pip install -r requirements.txt -r requirements-test.txt' \
        '    $(VENV_BIN)/python -m pip install -e .' \
        '' \
        'The last install is what creates $(VENV_BIN)/run-tests, which' \
        'pyproject.toml declares under [project.scripts].' >&2; \
    exit 1; \
fi
endef

# Fail unless $(PYTHON) reports EXACTLY the pinned version. This is what makes
# an override of PYTHON, PYTEST, VENV or VENV_BIN incapable of relaxing the
# pin: it chooses which interpreter is used, and this check still applies.
define require_pinned_python
make_pinned_version=$$('$(PYTHON)' -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null); \
if [ "$$make_pinned_version" != '$(REQUIRED_PYTHON_VERSION)' ]; then \
    printf '%s\n' \
        'make: the interpreter is not the pinned one.' \
        '  required : $(REQUIRED_PYTHON_VERSION)' \
        "  $(PYTHON) : $${make_pinned_version:-no version reported (missing or not runnable)}" \
        '' \
        'This project is pinned to $(REQUIRED_PYTHON_VERSION) exactly by' \
        '.python-version and by requires-python = "==3.14.*" in' \
        'pyproject.toml. An environment built on another interpreter is the' \
        'CI-versus-development drift the pin exists to prevent, so it is' \
        'refused rather than used, and it is deliberately not deleted for' \
        'you. Remove $(VENV) and re-run the bootstrap:' \
        '' \
        '    rm -rf $(VENV)' \
        '    sh scripts/run_tests.sh' >&2; \
    exit 1; \
fi
endef

.DEFAULT_GOAL := test

.PHONY: test unit coverage clean

# The Gherkin suite, through one entry point only: the `run-tests` console
# script declared in pyproject.toml under [project.scripts], taken from the
# virtual environment. Never the Flask CLI, never the BDD engine invoked
# directly, never `python -m app.cli`.
#
# No option value is hard-coded, so the runner defaults stay in force, and no
# status is suppressed - the recipe carries no leading dash and no fallback
# command that would discard a failing status. The runner already guarantees
# that a test outcome exits 0: scenario failures, errors, undefined or
# skipped steps, a browser that fails to start, an unknown browser value, a
# feature that fails to parse, a missing rerun manifest and an empty tag
# selection all exit 0, reproducing surefire
# testFailureIgnore=true (pom.xml:25) and the six -1 publisher thresholds in
# Jenkins. Suppressing status here would add nothing and would hide the three
# classes that must propagate: a usage error, a dead worker, and a merge or
# writer failure.
#
# A real run needs a browser and a populated configuration.properties; the
# repository supplies configuration.properties.example only.
test:
	@$(call require_executable,$(RUN_TESTS))
	@$(call require_pinned_python)
	printf '%s\n' \
	    'import os, shlex, subprocess, sys' \
	    'try:' \
	    '    argv = shlex.split(os.environ.get("ARGS", ""))' \
	    'except ValueError as exc:' \
	    '    sys.exit("make: ARGS is not a valid quoted argument list: %s" % exc)' \
	    'sys.exit(subprocess.call([sys.argv[1]] + argv))' \
	    | '$(PYTHON)' - '$(RUN_TESTS)'

# This port's own unit suite. Plain and unmeasured: discovery, collection
# patterns and options all come from pytest.ini (testpaths = tests), so no
# path argument and no coverage flag is restated here. A non-zero status
# propagates - this is a real quality gate, the same one both runner scripts
# apply ahead of the browser run.
unit:
	@$(call require_executable,$(PYTHON))
	@$(call require_pinned_python)
	$(PYTEST)

# The coverage gates. A single --cov-fail-under cannot express four different
# per-package thresholds, so pytest runs once per scope and each run measures
# and gates only its own package. Each line is its own shell and make stops at
# the first non-zero one, so the target fails on the first scope that misses.
# Four scopes exactly: no fifth scope, no aggregate threshold across app as a
# whole, and nothing here that could gate the plain `unit` target.
#
# This target is the canonical declaration of the four thresholds. Both runner
# scripts run the same four scoped commands in the same order ahead of the
# browser run, because CI must not depend on make being installed - so the
# developer command and the CI command are the same command, and if a
# threshold ever changes it changes in all three files together.
coverage:
	@$(call require_executable,$(PYTHON))
	@$(call require_pinned_python)
	$(PYTEST) --cov=app/utils --cov-fail-under=90
	$(PYTEST) --cov=app/pages --cov-fail-under=85
	$(PYTEST) --cov=app/automation --cov-fail-under=80
	$(PYTEST) --cov=app/reporting --cov-fail-under=80

# Remove generated output only, tolerating its absence, so `make clean` on a
# fresh checkout succeeds and repeating it is harmless. The set is exactly
# what .gitignore excludes: the generated target/ tree, which holds the four
# report artifacts and the per-worker intermediates beneath it; the Python
# bytecode caches; the pytest cache; and the coverage data file.
#
# This target runs no pre-flight, deliberately: it must work on a checkout
# that has no virtual environment, which is exactly when a developer reaches
# for it.
#
# Never removed, which is why this recipe names paths instead of sweeping the
# tree: configuration.properties, a developer's local git-ignored credentials
# file whose deletion would be data loss; configuration.properties.example;
# the virtual environment; and anything tracked by git. The bytecode sweeps
# prune the git directory and the virtual environments for the same reason,
# and both remove through `-exec rm ... +` rather than through find's own
# delete action: that action forces depth-first traversal, which makes a
# prune list inoperative, and GNU find rejects the combination outright - so
# the shorter spelling would fail this target rather than protect anything.
#
# The runner has its own --clean/--no-clean option, on by default, which
# empties target/ before a run and is ignored under --rerun so that the
# manifest it reads survives. This target is the standalone equivalent of
# that option, not a replacement for it.
clean:
	rm -rf target .pytest_cache
	rm -f .coverage
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type f -name '*.pyc' -exec rm -f {} +
