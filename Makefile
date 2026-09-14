# Makefile - Testinium-QA (Python 3.14 port)
#
# Optional developer convenience, and never a CI dependency: the pipeline
# selects scripts/run_tests.sh or scripts/run_tests.ps1 itself and never
# invokes make.

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
# override, and any override of the variables below, is still held to the
# version check in the pre-flight - it selects WHICH environment is used, it
# does not relax the pin.
#
# EVERY value here is caller-influenced: make imports the environment as
# variables, so an ambient PYTHON is an override just as much as one written on
# the command line, and both are accepted. So none of them is ever interpolated
# into recipe text, where a quote-breaking value would become a second command
# rather than a path. They are `export`ed instead and each recipe reads them as
# quoted shell parameter expansions - "$${PYTHON}" and not $(PYTHON) - which
# gives the shell one word whatever it contains, and each is validated against
# a conservative allowlist before it is used at all. The only make-interpolated
# text left in any recipe below is a literal from this file, a variable NAME
# among them: $(1) in the canned recipes expands to a name this file wrote, and
# only the VALUE comes from the caller.
#
# That is not enough on its own, and this is the reason each of the four is
# settled the long way below rather than with `?=`. A caller's value is MAKE
# SOURCE before it is ever shell text: `make unit PYTHON='$(shell ...)'` is
# expanded by make itself when the value is exported for a recipe, which
# happens before any recipe line runs and therefore before the shell-side
# allowlist can see it. Measured on GNU Make 4.4.1: with `?=`, that override
# ran its command for all four targets - `clean` included, which has no
# pre-flight at all - and with VENV, VENV_BIN and RUN_TESTS as the carrier
# just the same. ARGS was immune, and the mechanism that made it immune is
# what every one of them now uses:
#
#   * `$(origin VAR)` distinguishes a value that came from the caller - the
#     command line, or the environment - from this file's own default.
#   * For a caller's value, `override VAR := $(value VAR)` stores the text
#     VERBATIM: `$(value)` reads it unexpanded and `:=` keeps it that way, so
#     make never evaluates a function, a variable reference or a `$(shell)`
#     inside it, and `export` hands those literal characters to the recipe.
#   * `override` also settles it: a command-line assignment beats a file
#     assignment in make, so without it this capture could itself be bypassed.
#   * The allowlist then sees the RAW text and refuses it - it rejects `$`,
#     `(` and `)` - so a function reference becomes a refusal instead of an
#     execution. A default derived from a refused value carries the same
#     characters and is refused for the same reason.
# ---------------------------------------------------------------------------

# The sanctioned environment directory. Overridable, captured raw.
ifneq ($(filter command line environment environment override,$(origin VENV)),)
override VENV := $(value VENV)
else
override VENV := .venv
endif

# Where its executables live: bin on POSIX, Scripts on Windows. Derived from
# VENV only when the caller did not name it directly.
ifneq ($(filter command line environment environment override,$(origin VENV_BIN)),)
override VENV_BIN := $(value VENV_BIN)
else
override VENV_BIN := $(VENV)/bin
endif

# The pinned interpreter version, exact, and the only place this file states
# it. Its other homes are .python-version, pyproject.toml and the two runner
# scripts. String equality is the test, so no other 3.14.x satisfies it.
#
# `override` because `:=` alone does not settle it: a command-line assignment
# beats a file assignment in make, so `make unit REQUIRED_PYTHON_VERSION=3.13.7`
# would otherwise relax the one check that cannot be relaxed. With `override`
# neither the command line nor the environment can reach it.
override REQUIRED_PYTHON_VERSION := 3.14.6

# The interpreter and the console script, the same way: the caller's text
# verbatim, or this file's own default derived from the two above.
ifneq ($(filter command line environment environment override,$(origin PYTHON)),)
override PYTHON := $(value PYTHON)
else
override PYTHON := $(VENV_BIN)/python
endif

ifneq ($(filter command line environment environment override,$(origin RUN_TESTS)),)
override RUN_TESTS := $(value RUN_TESTS)
else
override RUN_TESTS := $(VENV_BIN)/run-tests
endif

# The values recipes read as "$${NAME}". Exporting is what carries them to the
# recipe shell as DATA; nothing below interpolates them as text.
export VENV
export VENV_BIN
export PYTHON
export RUN_TESTS
export REQUIRED_PYTHON_VERSION

# Extra options forwarded to the suite runner, e.g.
#     make test ARGS=--dry-run
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
# is NOT how the runner is reached. RUN_TESTS, the console script from the
# virtual environment, is still the only thing executed as the runner - reached
# as "$${RUN_TESTS}" for the reason the variable block above gives - never the
# Flask CLI, never `python -m app.cli`.
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

# Fail unless the value of the variable NAMED in $(1) is a path this file will
# use at all. The name is a literal from this file and the value never leaves
# the shell's hands, so what make interpolates here cannot come from a caller.
#
# The allowlist is deliberately narrow - letters, digits, space and . _ - / + :
# @ - because the values are paths to an interpreter and a console script, and
# nothing else belongs in one. A value carrying a quote, a semicolon, a
# backquote, a newline or any other control character is REFUSED rather than
# printed or passed on, which is also why the refusal renders it through the
# same bound-and-strip filter the runner scripts use for environment-derived
# text: 200 characters, printable ASCII only.
define require_safe_path
make_value="$${$(1)}"; \
make_rest=$$(printf '%s' "$$make_value" | tr -d 'A-Za-z0-9._/+:@ -'); \
if [ -z "$$make_value" ] || [ -n "$$make_rest" ]; then \
    printf '%s\n' \
        'make: the $(1) value is not a path this Makefile will use.' \
        "  $(1) : $$(printf '%s' "$$make_value" | tr -d '\000' | tr -c '\040-\176' '[?*]' | cut -c1-200)" \
        '' \
        'A path here may hold letters, digits, space and . _ - / + : @ only,' \
        'and may not be empty. Every value this file uses - PYTHON, RUN_TESTS,' \
        'VENV and VENV_BIN - can be set by the caller or inherited from the' \
        'environment, so each is checked before it is used and none of them is' \
        'ever pasted into a command line.' >&2; \
    exit 1; \
fi
endef

# Fail unless the program the variable NAMED in $(1) points at exists and is
# executable, naming the bootstrap that creates it. Messages avoid the
# apostrophe so the single-quoted forms below stay valid POSIX shell, and the
# value is read as a quoted shell expansion rather than interpolated.
define require_executable
if [ ! -x "$${$(1)}" ]; then \
    printf '%s\n' \
        'make: the $(1) program is missing or not executable.' \
        "  $(1) : $$(printf '%s' "$${$(1)}" | tr -d '\000' | tr -c '\040-\176' '[?*]' | cut -c1-200)" \
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
        '    python3.14 -m venv .venv' \
        '    .venv/bin/python -m pip install -r requirements.txt -r requirements-test.txt' \
        '    .venv/bin/python -m pip install -e .' \
        '' \
        'The last install is what creates .venv/bin/run-tests, which' \
        'pyproject.toml declares under [project.scripts]. A VENV or VENV_BIN' \
        'override puts the same three commands elsewhere.' >&2; \
    exit 1; \
fi
endef

# Fail unless the interpreter PYTHON points at reports EXACTLY the pinned
# version. This is what makes an override of PYTHON, VENV or VENV_BIN incapable
# of relaxing the pin: it chooses which interpreter is used, and this check
# still applies. The version literal is `override`-protected above, so the
# comparison is against this file's own value and not against anything a caller
# can reach; what the interpreter printed is compared EXACTLY and only the
# printed copy of it is bound and stripped.
define require_pinned_python
make_pinned_version=$$("$${PYTHON}" -I -S -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null); \
if [ "$$make_pinned_version" != "$${REQUIRED_PYTHON_VERSION}" ]; then \
    printf '%s\n' \
        'make: the interpreter is not the pinned one.' \
        "  required : $${REQUIRED_PYTHON_VERSION}" \
        "  PYTHON   : $$(printf '%s' "$${PYTHON}" | tr -d '\000' | tr -c '\040-\176' '[?*]' | cut -c1-200)" \
        "  reported : $$(printf '%s' "$${make_pinned_version:-no version reported (missing or not runnable)}" | tr -d '\000' | tr -c '\040-\176' '[?*]' | cut -c1-200)" \
        '' \
        "This project is pinned to $${REQUIRED_PYTHON_VERSION} exactly by" \
        '.python-version and by requires-python = "==3.14.*" in' \
        'pyproject.toml. An environment built on another interpreter is the' \
        'CI-versus-development drift the pin exists to prevent, so it is' \
        'refused rather than used, and it is deliberately not deleted for' \
        'you. Remove the environment and re-run the bootstrap:' \
        '' \
        '    rm -rf .venv' \
        '    sh scripts/run_tests.sh' >&2; \
    exit 1; \
fi
endef

.DEFAULT_GOAL := test

.PHONY: test unit coverage clean

test:
	@$(call require_safe_path,PYTHON)
	@$(call require_safe_path,RUN_TESTS)
	@$(call require_executable,RUN_TESTS)
	@$(call require_pinned_python)
	printf '%s\n' \
	    'import os, shlex, subprocess, sys' \
	    'try:' \
	    '    argv = shlex.split(os.environ.get("ARGS", ""))' \
	    'except ValueError as exc:' \
	    '    sys.exit("make: ARGS is not a valid quoted argument list: %s" % exc)' \
	    'sys.exit(subprocess.call([sys.argv[1]] + argv))' \
	    | "$${PYTHON}" - "$${RUN_TESTS}"

# This port's own unit suite. Plain and unmeasured: discovery, collection
# patterns and options all come from pytest.ini (testpaths = tests), so no
# path argument and no coverage flag is restated here. A non-zero status
# propagates - this is a real quality gate, the same one both runner scripts
# apply ahead of the browser run.
#
# The two pytest variables are removed from the recipe's environment first, and
# that is part of the gate rather than tidiness: PYTEST_ADDOPTS is prepended to
# the command line, so an ambient value can load a plugin with -p or move a
# threshold, and PYTEST_PLUGINS names modules pytest imports at startup - which
# it does even under the --disable-plugin-autoload pytest.ini sets, because that
# switch governs entry-point discovery and not this variable. Both runner
# scripts drop the same two before their own gates. `unset` leaves the status of
# the pytest run as the status of the line, so nothing about propagation
# changes.
unit:
	@$(call require_safe_path,PYTHON)
	@$(call require_executable,PYTHON)
	@$(call require_pinned_python)
	unset PYTEST_ADDOPTS PYTEST_PLUGINS; "$${PYTHON}" -m pytest

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
# The two pytest variables are removed from each gate's environment for the
# reason the `unit` target gives, and per line rather than once for the target
# because every recipe line is its own shell.
coverage:
	@$(call require_safe_path,PYTHON)
	@$(call require_executable,PYTHON)
	@$(call require_pinned_python)
	unset PYTEST_ADDOPTS PYTEST_PLUGINS; "$${PYTHON}" -m pytest -p pytest_cov --cov=app/utils --cov-fail-under=90
	unset PYTEST_ADDOPTS PYTEST_PLUGINS; "$${PYTHON}" -m pytest -p pytest_cov --cov=app/pages --cov-fail-under=85
	unset PYTEST_ADDOPTS PYTEST_PLUGINS; "$${PYTHON}" -m pytest -p pytest_cov --cov=app/automation --cov-fail-under=80
	unset PYTEST_ADDOPTS PYTEST_PLUGINS; "$${PYTHON}" -m pytest -p pytest_cov --cov=app/reporting --cov-fail-under=80

# Removes generated output only, tolerating its absence, and names each path
# rather than sweeping the tree, so a developer's git-ignored
# configuration.properties is never deleted. No pre-flight, so it works on a
# checkout with no virtual environment. Both bytecode sweeps prune .git and
# the virtual environments and remove via `-exec rm ... +`, not find's own
# delete action, which forces depth-first traversal and would make the prune
# list inoperative.
clean:
	rm -rf target .pytest_cache
	rm -f .coverage
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . \( -name .git -o -name .venv -o -name venv \) -prune -o -type f -name '*.pyc' -exec rm -f {} +
