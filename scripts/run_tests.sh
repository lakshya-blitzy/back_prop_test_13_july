#!/bin/sh
# =============================================================================
# scripts/run_tests.sh -- the POSIX branch of the pipeline's isUnix() dispatch.
#
# WHAT THIS SCRIPT PORTS
# ----------------------
# The source project ran its tests from a Groovy scripted pipeline whose test
# stage dispatched on the agent's platform and then ran the very same command
# string on either side of that dispatch -- only the executor differed:
#
#     [Jenkins:L6-L11]  stage('Run tests'){ if(isUnix()){ sh "mvn clean test" }
#                                           else { bat "..." } }
#
# This file is the POSIX side, [Jenkins:L8]. Its sibling scripts/run_tests.bat is
# the Windows side, [Jenkins:L10]. Because the two source branches carried an
# identical command, the two scripts must stay functionally identical: same clean
# set, same five-directory layout, same test invocation, same exit-code mapping.
#
# The updated pipeline (and its discoverable Jenkinsfile alias) invokes this file
# as `sh "./scripts/run_tests.sh"`, so the executable bit is mandatory: the file
# is committed with mode 100755. `.gitattributes` pins `*.sh text eol=lf`, so the
# file is LF-only; a CRLF copy would fail at run time with a "bad interpreter"
# error that has nothing to do with the logic below.
#
# `app/utils/platform_exec.py` holds the same mapping as data --
# POSIX_TEST_COMMAND = ("./scripts/run_tests.sh",) -- and hands it to the service
# layer as an argument list, appending any extra arguments verbatim. That is the
# contract behind the `"$@"` forwarding in the run stage below.
#
# WHAT IT DOES, IN ORDER
# ----------------------
#   1. CLEAN     wipe the `target/` artifact root and every Python cache.
#   2. RECREATE  make the five-directory `target/` layout.
#   3. RUN       invoke pytest, adding nothing that pytest.ini already governs.
#   4. MAP       translate pytest's exit code through the six-row table below.
#
# THE EXIT-CODE MAPPING -- the most consequential decision in the whole port
# -------------------------------------------------------------------------
#   0  every selected test passed              -> SUCCESS          (exit 0)
#   1  tests ran and some of them failed       -> SUCCESS, NON-GATED (exit 0)
#   2  the run was interrupted                 -> FAILURE   (propagate the code)
#   3  an internal error occurred              -> FAILURE   (propagate the code)
#   4  pytest usage / command-line error        -> FAILURE   (propagate the code)
#   5  nothing collected, or all deselected    -> SUCCESS, ZERO SCENARIOS (exit 0)
#   *  anything else                           -> FAILURE   (conservative default)
#
# Two rows of that table are deliberately preserved defects. Both are recorded in
# docs/migration-parity.md, which is the authoritative register, and both are
# INTENTIONALLY PRESERVED here rather than quietly corrected:
#
#   D3  INTENTIONALLY PRESERVED -- exit code 1 is reported as success. This is the
#       whole of <testFailureIgnore>true</testFailureIgnore> [pom.xml:L25], and it
#       is corroborated by the report publisher's six all-`-1` thresholds
#       [Jenkins:L15], which gate nothing. The source build never went red for a
#       failing test, so neither does this script.
#
#   D2  INTENTIONALLY PRESERVED -- exit code 5 is reported as success. pytest.ini
#       carries the `LogOut` marker selector, the faithful port of the documented
#       runner's tags = "@LogOut" [README.md:L87]. No scenario carries that tag,
#       so a default run deselects everything and pytest exits 5. The source
#       pipeline went green in exactly that situation. A naive
#       "non-zero means failure" mapping would make the ported system fail where
#       the original succeeded, which is the opposite of parity.
#
# Consequently no status-swallowing catch-all appears anywhere in this file: no
# always-succeeding operator appended to the test invocation, and no
# unconditional success at the end. Such a catch-all looks like it implements the
# ignore-failures setting, but it also swallows codes 2, 3 and 4, hiding genuine
# breakage behind a permanently green build. The explicit `case` in the map stage
# is the only correct implementation, and its default arm fails closed so an exit
# code introduced by some future release is never mistaken for success. Apart from
# the pre-flight guards, which all fail with a non-zero status of their own, the
# script leaves through a single `exit` at the very end carrying the mapped
# status -- there is no other way out and no literal success status anywhere.
#
# The literal spelling of each prohibited construct is deliberately kept out of
# this file, so that grepping for one stays an exact guard instead of also
# matching the comment explaining its absence. pytest.ini keeps the Gherkin
# reporter's flag out of itself for the same reason.
#
# WHY /bin/sh AND WHY NO `set -e`
# -------------------------------
# The shebang is `/bin/sh` and the body is strictly POSIX: no arrays, no `[[ ]]`,
# no `local`, no `pipefail`. Nothing here needs a bash feature, and staying
# portable means the script behaves identically under dash (the /bin/sh of the
# Debian-based container image this project ships) and under bash. `set -o
# pipefail` is not POSIX and is therefore not used; there is no pipeline whose
# intermediate status matters.
#
# `set -e` is deliberately NOT used. It would abort the script the instant pytest
# returned a non-zero code, which is every interesting case in the table above,
# defeating the non-gating behaviour entirely before the mapping could run. Each
# step that must be fatal is therefore checked explicitly. `set -u` is on, so a
# typo in a variable name is a loud error rather than an empty expansion feeding
# a removal.
#
# WHAT THIS SCRIPT MUST NOT DO
# ----------------------------
# pytest.ini is the single authority on test options. It already carries the
# marker selector, the report writers, the worker count, the traceback style, the
# registered markers and the warning filters. Duplicating any of them here would
# create a second source of truth that eventually disagrees, so the run stage
# passes only what the caller forwarded. In particular the Gherkin terminal
# reporter's flag never appears here: it is mutually exclusive with parallel
# execution, and the serial escape hatch is the separate `make test-pretty` rule.
#
# Report generation is likewise out of scope -- that is scripts/generate_reports.py
# and the Makefile `report` rule. The artifacts that appear under `target/` after
# this script runs are a side effect of the writers pytest.ini configures.
#
# Overriding the preserved marker selector is a caller's decision, not this
# script's: forward your own arguments (they win over the ones pytest.ini
# carries), or set TAG_EXPRESSION, which app/config.py and
# app/services/test_runner_service.py consume through the documented
# configuration precedence chain. This script deliberately adds no third
# mechanism of its own, so the default run keeps selecting zero scenarios and D2
# stays preserved.
#
# USAGE
# -----
#   ./scripts/run_tests.sh                 # the ported default run
#   ./scripts/run_tests.sh -k login        # forward extra arguments to pytest
#   PYTHON=/usr/local/bin/python3.14 ./scripts/run_tests.sh
#
# The script's own exit status is either the mapped verdict above, or one of two
# pre-flight failure codes: 1 for an environment or safety-guard failure, and 127
# when no usable interpreter can be found. Both are non-zero by design, so a
# broken environment can never be mistaken for a green run.
# =============================================================================

# `set -u` guards against unset variables; `set -e` is intentionally absent (see
# the header). IFS is reset to its default so field splitting cannot be inherited
# from a caller's environment.
set -u
IFS=' 	
'

# Cleared so that a caller's CDPATH can never redirect any `cd` below -- neither
# the one that locates the repository root nor the ones inside the command
# substitutions that compute it.
CDPATH=''

LOG_PREFIX='[run_tests]'

# The script's own statuses. pytest's codes are never remapped onto these; they
# are propagated as themselves so an operator can read the raw cause.
EXIT_SUCCESS=0
EXIT_ENVIRONMENT=1
EXIT_NO_INTERPRETER=127

# The ephemeral artifact root. The name is retained from the source build on
# purpose: the report publisher matches artifacts with
# fileIncludePattern: '**/*.json' [Jenkins:L15], and keeping output under
# `target/` means that contract needs no change at all. Renaming it would
# silently break report publication in CI.
TARGET_DIR='target'

# The five directories, in creation order. Identical to TARGET_SUBDIR_NAMES in
# app/utils/paths.py and to TARGET_SUBDIRS in the Makefile: `cucumber` for the
# pretty-report tree, `screenshots` (one word) and `error-shots` (hyphenated) for
# the two shot directories, and `surefire-reports` keeping its original name so
# any downstream consumer keyed on that path still finds content. This set is a
# cross-module contract -- do not add or drop an entry.
TARGET_LAYOUT="$TARGET_DIR
$TARGET_DIR/cucumber
$TARGET_DIR/screenshots
$TARGET_DIR/error-shots
$TARGET_DIR/surefire-reports"

# The caches removed alongside `target/`. Removing them is a correctness
# requirement rather than tidiness: a stale bytecode cache once produced a false
# experimental result while the duplicate-scenario defect was being analysed, and
# that incident is why these paths are ignored by git and wiped here.
CACHE_PATHS='.pytest_cache
.mypy_cache
.ruff_cache'


# -----------------------------------------------------------------------------
# Output helpers. A shell script's natural interface is its console output, and
# the Jenkins console log is where an operator reads why a run with failures was
# still reported green. Application logging is owned by app/logging_config.py and
# is deliberately not written to from here.
# -----------------------------------------------------------------------------
log() {
    printf '%s %s\n' "$LOG_PREFIX" "$*"
}

error() {
    printf '%s %s\n' "$LOG_PREFIX" "$*" >&2
}


# -----------------------------------------------------------------------------
# Repository root resolution.
#
# Every path this script touches is derived from the script's own location, never
# from the caller's working directory and never from a caller-supplied variable,
# so running it from anywhere -- the pipeline workspace, a developer's shell, or
# /tmp -- acts on this repository and only on this repository. `pwd -P` resolves
# symbolic links to a canonical physical path.
# -----------------------------------------------------------------------------
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
if [ -z "$SCRIPT_DIR" ]; then
    error "cannot locate the directory holding this script (invoked as '$0')"
    exit "$EXIT_ENVIRONMENT"
fi

REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
if [ -z "$REPO_ROOT" ]; then
    error "cannot locate the repository root above '$SCRIPT_DIR'"
    exit "$EXIT_ENVIRONMENT"
fi

# A safety interlock, not a formality. The clean stage removes whole trees, so it
# must be impossible for it to run anywhere other than this repository: the root
# has to be a real directory, must not be the filesystem root, and must carry two
# markers -- the test configuration this run depends on, and this very script. If
# any check fails, the script stops before deleting anything at all.
if [ ! -d "$REPO_ROOT" ]; then
    error "the computed repository root is not a directory: $REPO_ROOT"
    exit "$EXIT_ENVIRONMENT"
fi

if [ "$REPO_ROOT" = '/' ]; then
    error 'the computed repository root is the filesystem root; refusing to continue'
    exit "$EXIT_ENVIRONMENT"
fi

for marker in 'pytest.ini' 'scripts/run_tests.sh'; do
    if [ ! -f "$REPO_ROOT/$marker" ]; then
        error "'$REPO_ROOT' does not look like this repository: $marker is missing"
        error 'run the script by its real path so its own location can be resolved'
        exit "$EXIT_ENVIRONMENT"
    fi
done

# pytest resolves pytest.ini, `testpaths` and the `app` package relative to the
# directory it starts in, so the whole run happens from the repository root.
if ! cd -- "$REPO_ROOT"; then
    error "cannot enter the repository root: $REPO_ROOT"
    exit "$EXIT_ENVIRONMENT"
fi

log "repository root: $REPO_ROOT"


# -----------------------------------------------------------------------------
# Guarded removal.
#
# Accepts a repository-relative path only. An empty argument, an absolute path or
# anything containing `..` is refused outright rather than expanded into a
# removal, because an empty expansion is exactly how a clean step turns into a
# catastrophe. Absent paths are reported and skipped, which makes the whole clean
# stage idempotent.
# -----------------------------------------------------------------------------
remove_repo_path() {
    rrp_rel="$1"
    rrp_label="$2"

    if [ -z "$rrp_rel" ]; then
        error 'clean: refusing to remove an empty path'
        return 1
    fi

    case "$rrp_rel" in
        /*)
            error "clean: refusing an absolute path: $rrp_rel"
            return 1
            ;;
        *..*)
            error "clean: refusing a path containing '..': $rrp_rel"
            return 1
            ;;
    esac

    if [ -z "$REPO_ROOT" ]; then
        error 'clean: the repository root is unset'
        return 1
    fi

    rrp_abs="$REPO_ROOT/$rrp_rel"

    # `-e` alone would report a dangling symbolic link as absent and leave it in
    # place, which would then defeat the recreate stage.
    if [ ! -e "$rrp_abs" ] && [ ! -L "$rrp_abs" ]; then
        log "clean: $rrp_label already absent ($rrp_rel)"
        return 0
    fi

    rm -rf -- "$rrp_abs"

    if [ -e "$rrp_abs" ] || [ -L "$rrp_abs" ]; then
        error "clean: could not remove $rrp_rel"
        return 1
    fi

    log "clean: removed $rrp_label ($rrp_rel)"
    return 0
}


# -----------------------------------------------------------------------------
# Bytecode-cache removal.
#
# `.git` and both conventional virtual-environment directory names are pruned, so
# neither git's internals nor the bytecode of installed dependencies is ever
# touched. `-prune` on the match itself stops find from descending into a
# directory it is about to delete. `-exec ... +` passes the paths as arguments, so
# unusual characters in a path are handled correctly.
# -----------------------------------------------------------------------------
find_pycache_dirs() {
    find . \
        -name '.git' -prune -o \
        -name '.venv' -prune -o \
        -name 'venv' -prune -o \
        -type d -name '__pycache__' -print
}

remove_pycache_dirs() {
    rpd_found=$(find_pycache_dirs)

    if [ -z "$rpd_found" ]; then
        log 'clean: no __pycache__ directories present'
        return 0
    fi

    # A count for the log only; the removal itself never relies on this split.
    rpd_count=$(printf '%s\n' "$rpd_found" | wc -l | tr -d ' ')

    find . \
        -name '.git' -prune -o \
        -name '.venv' -prune -o \
        -name 'venv' -prune -o \
        -type d -name '__pycache__' -prune -exec rm -rf -- '{}' +

    rpd_left=$(find_pycache_dirs)
    if [ -z "$rpd_left" ]; then
        log "clean: removed __pycache__ directories ($rpd_count)"
        return 0
    fi

    error 'clean: some __pycache__ directories could not be removed'
    return 1
}


# -----------------------------------------------------------------------------
# Stage 1 -- CLEAN. The analogue of the source build's clean step.
#
# Removing the caches is a correctness requirement rather than housekeeping: a
# stale bytecode cache once produced a false experimental result during the
# duplicate-scenario analysis recorded in docs/migration-parity.md.
#
# What this stage never touches, by construction: any tracked file, the
# git-ignored configuration.properties and .env runtime configuration, any *.log
# file, and any virtual environment. Nothing is removed except the paths named
# above -- there is no wholesale purge of ignored or untracked files, because that
# would destroy legitimate developer-created runtime files along with the caches.
# -----------------------------------------------------------------------------
log 'stage: clean'

clean_failed=0

if ! remove_repo_path "$TARGET_DIR" 'the artifact root'; then
    clean_failed=1
fi

for cache_path in $CACHE_PATHS; do
    if ! remove_repo_path "$cache_path" 'a tool cache'; then
        clean_failed=1
    fi
done

if ! remove_pycache_dirs; then
    clean_failed=1
fi

if [ "$clean_failed" -eq 1 ]; then
    error 'clean stage failed; refusing to test against a partially cleaned tree'
    error 'the source build behaved the same way: a failing clean aborted the run'
    exit "$EXIT_ENVIRONMENT"
fi


# -----------------------------------------------------------------------------
# Stage 2 -- RECREATE the artifact layout.
#
# This is not optional tidiness either. pytest-bdd's Cucumber-JSON writer opens
# its output file directly and does not create the parent directory, so a run
# started with the artifact root missing fails at session finish with a
# file-not-found error. The source build got that directory implicitly; the port
# creates it explicitly, here as well as in app/utils/paths.py, the Makefile and
# tests/conftest.py, so it cannot be missed.
#
# `mkdir -p` makes the stage idempotent and race-safe under parallel invocation.
# -----------------------------------------------------------------------------
log 'stage: recreate artifact layout'

for layout_dir in $TARGET_LAYOUT; do
    mkdir -p -- "$REPO_ROOT/$layout_dir"
    if [ ! -d "$REPO_ROOT/$layout_dir" ]; then
        error "cannot create the artifact directory: $layout_dir"
        exit "$EXIT_ENVIRONMENT"
    fi
    log "layout: $layout_dir/"
done


# -----------------------------------------------------------------------------
# Interpreter resolution.
#
# pytest is always invoked as a module of an explicitly resolved interpreter, so
# the run is deterministic instead of depending on whichever console script
# happens to be first on PATH. Resolution order, most specific first:
#
#   1. PYTHON, when an operator has set it. Used as the command name of an
#      argument list -- never concatenated into a command string, never evaluated.
#   2. The project virtual environment inside this repository, which is what the
#      Makefile uses and where the pinned interpreter named in .python-version
#      lives.
#   3. An already-activated environment somewhere else.
#   4. python3 on PATH -- the documented fallback, and the correct answer inside
#      the container image, where the pinned interpreter is the system one.
# -----------------------------------------------------------------------------
resolve_interpreter() {
    case "${PYTHON-}" in
        '') ;;
        *)
            printf '%s\n' "$PYTHON"
            return 0
            ;;
    esac

    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        printf '%s\n' "$REPO_ROOT/.venv/bin/python"
        return 0
    fi

    case "${VIRTUAL_ENV-}" in
        '') ;;
        *)
            if [ -x "$VIRTUAL_ENV/bin/python" ]; then
                printf '%s\n' "$VIRTUAL_ENV/bin/python"
                return 0
            fi
            ;;
    esac

    if command -v python3 > /dev/null 2>&1; then
        printf '%s\n' 'python3'
        return 0
    fi

    return 1
}

PY_BIN=$(resolve_interpreter)
if [ -z "$PY_BIN" ]; then
    error 'no usable interpreter found'
    error 'set PYTHON, activate the project environment, or put python3 on PATH'
    exit "$EXIT_NO_INTERPRETER"
fi

if ! command -v "$PY_BIN" > /dev/null 2>&1; then
    error "the resolved interpreter is not executable: $PY_BIN"
    exit "$EXIT_NO_INTERPRETER"
fi

# Without this check a missing harness would exit 1 ("No module named pytest"),
# which the mapping below would then report as an ignored test failure -- a green
# run against an environment that never ran anything. An unusable environment is
# a hard failure, distinct from any pytest outcome. Nothing is installed here.
if ! "$PY_BIN" -c 'import pytest' > /dev/null 2>&1; then
    error "the pytest module is not importable with: $PY_BIN"
    error 'install the pinned harness dependencies from requirements-test.txt'
    exit "$EXIT_ENVIRONMENT"
fi

PY_VERSION=$("$PY_BIN" --version 2>&1)
log "interpreter: $PY_BIN ($PY_VERSION)"


# -----------------------------------------------------------------------------
# Stage 3 -- RUN the tests.
#
# The invocation carries no option of its own. Everything about selection,
# parallel execution, report writing, traceback style, marker strictness and
# warning filtering already lives in pytest.ini, and adding any of it here would
# create a second source of truth. Caller arguments are forwarded as separate
# words, exactly as received, so no quoting is lost and nothing is re-parsed by a
# shell; a forwarded option wins over the equivalent one pytest.ini carries,
# which is the supported way to run a selection other than the ported default.
# -----------------------------------------------------------------------------
log 'stage: run tests'

if [ "$#" -eq 0 ]; then
    log 'pytest arguments: none -- pytest.ini governs the entire run'
else
    log "pytest arguments forwarded by the caller: $*"
fi

# The exit code is captured on the very next line: any command in between, even an
# echo, would overwrite it.
"$PY_BIN" -m pytest "$@"
rc=$?


# -----------------------------------------------------------------------------
# Stage 4 -- MAP the exit code.
#
# Every code is handled by an explicit arm, and the default arm fails closed. See
# the table in the header for the reasoning behind each row, and
# docs/migration-parity.md for the register of preserved defects.
# -----------------------------------------------------------------------------
verdict=''
detail=''
parity=''
script_status="$EXIT_SUCCESS"

case "$rc" in
    0)
        verdict='SUCCESS'
        detail='every selected scenario passed'
        script_status="$EXIT_SUCCESS"
        ;;
    1)
        verdict='SUCCESS (test failures ignored)'
        detail='tests ran and some of them failed; the run is not gated on them'
        parity='defect D3, INTENTIONALLY PRESERVED -- the port of <testFailureIgnore>true</testFailureIgnore> [pom.xml:L25], corroborated by the six all-minus-one publisher thresholds [Jenkins:L15]; see docs/migration-parity.md'
        script_status="$EXIT_SUCCESS"
        ;;
    2)
        verdict='FAILURE'
        detail='the run was interrupted before it finished'
        script_status="$rc"
        ;;
    3)
        verdict='FAILURE'
        detail='an internal error occurred inside pytest'
        script_status="$rc"
        ;;
    4)
        verdict='FAILURE'
        detail='pytest rejected its command line; check the forwarded arguments and the plugins pytest.ini requires'
        script_status="$rc"
        ;;
    5)
        verdict='SUCCESS (zero scenarios)'
        detail='nothing was collected, or every scenario was deselected; no test failed'
        parity='defect D2, INTENTIONALLY PRESERVED -- the preserved LogOut marker selector from the documented runner matches no scenario, and the source pipeline went green in exactly this situation; see docs/migration-parity.md'
        script_status="$EXIT_SUCCESS"
        ;;
    *)
        # Unreachable for the codes above, so this arm can never see 0 and can
        # therefore never exit successfully. Any code introduced by a future
        # release is a failure until it has been examined and given its own arm.
        verdict='FAILURE'
        detail='unrecognised pytest exit code; treated as a failure by default'
        script_status="$rc"
        ;;
esac

log "pytest exit code: $rc"
log "verdict: $verdict"
log "reason: $detail"

case "$parity" in
    '') ;;
    *) log "parity: $parity" ;;
esac

log "script exit code: $script_status"

exit "$script_status"

