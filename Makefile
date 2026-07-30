# =============================================================================
# Makefile - the Python build surface. Ports the Maven lifecycle invocations
# used by the CI pipeline (Jenkins stages 'Run tests' / 'Generate report'):
#
#     sh  "mvn clean test"   ->  make test        (clean + recreate target/ + pytest)
#     bat "mvn clean test"   ->  make test        (same target; see scripts/run_tests.bat)
#     mvn clean              ->  make clean       (wipes the ephemeral target/ tree)
#
# NON-GATING SEMANTICS (ported from <testFailureIgnore>true</testFailureIgnore>
# in pom.xml and from the all `-1` thresholds of the Jenkins cucumber publisher):
#   pytest exit 0 -> success                (all selected tests passed)
#   pytest exit 1 -> success, NON-GATING    (tests failed; reports still produced)
#   pytest exit 5 -> success, ZERO SCENARIOS(everything deselected by the
#                                            preserved `-m "LogOut"` filter)
#   pytest exit 2/3/4 -> hard failure       (interrupted / internal / usage error)
#
# PARALLEL AGENT / MULTI-CLONE USE:
#   Set CLONE_INDEX=<n> to shift the host port and the compose project name so
#   several clones can run their own instance side by side, e.g.
#       CLONE_INDEX=1 make docker-up     # host port 8001, project testinium-qa-1
#       CLONE_INDEX=1 make run           # dev server on port 8001
# =============================================================================

SHELL           := /bin/bash
.DEFAULT_GOAL   := help

# --- Interpreter / virtual environment ---------------------------------------
BASE_PYTHON     ?= python3.14
VENV            ?= .venv
PY              := $(VENV)/bin/python
PIP             := $(VENV)/bin/python -m pip
PYTEST          := $(VENV)/bin/python -m pytest
RUFF            := $(VENV)/bin/ruff
BLACK           := $(VENV)/bin/black
MYPY            := $(VENV)/bin/mypy
GUNICORN        := $(VENV)/bin/gunicorn

# --- Ephemeral artifact root (name deliberately retained from Maven) ----------
TARGET_DIR      ?= target
TARGET_SUBDIRS  := $(TARGET_DIR)/cucumber $(TARGET_DIR)/screenshots \
                   $(TARGET_DIR)/error-shots $(TARGET_DIR)/surefire-reports

# --- Runtime / service knobs (parameterised for parallel clones) -------------
CLONE_INDEX     ?= 0
APP_PORT        ?= $(shell echo $$((8000 + $(CLONE_INDEX))))
COMPOSE_PROJECT ?= testinium-qa-$(CLONE_INDEX)
DOCKER_IMAGE    ?= testinium-qa:1.0.0.dev0

# Suites that are NOT part of the ported Cucumber run must override the
# preserved `-m "LogOut"` tag filter from pytest.ini (a command-line -m wins).
NO_TAG_FILTER   := -m ""

.PHONY: help venv install install-dev clean dirs test test-pretty test-unit \
        test-integration test-parity test-suites verify lint format typecheck \
        report feature run serve docker-build docker-up docker-down info

help: ## Show the available targets
	@echo "testinium-qa - available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment with the pinned interpreter (3.14.6)
	$(BASE_PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade "pip==26.2" "setuptools==83.0.0" "wheel==0.47.0"

install: ## Install runtime + harness dependencies (exact pins)
	$(PIP) install -r requirements.txt -r requirements-test.txt

install-dev: install ## Install the optional quality tooling as well
	$(PIP) install "ruff==0.16.0" "black==26.5.1" "mypy==2.3.0" "pytest-cov==7.1.0"

clean: ## Port of `mvn clean`: wipe target/ and every Python cache
	rm -rf $(TARGET_DIR)
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -path ./$(VENV) -prune -o -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -path ./$(VENV) -prune -o -type f -name '*.py[cod]' -delete 2>/dev/null || true

dirs: ## Create the target/ tree (the cucumber JSON writer does NOT mkdir)
	mkdir -p $(TARGET_DIR) $(TARGET_SUBDIRS)

test: clean dirs ## Port of `mvn clean test` (parallel, non-gating)
	@set +e; $(PYTEST); code=$$?; set -e; \
	case $$code in \
	  0) echo "[make test] pytest exit 0 - all selected scenarios passed";; \
	  1) echo "[make test] pytest exit 1 - test failures IGNORED (testFailureIgnore=true parity)";; \
	  5) echo "[make test] pytest exit 5 - zero scenarios selected by the preserved '-m LogOut' filter: SUCCESS";; \
	  *) echo "[make test] pytest exit $$code - hard failure"; exit $$code;; \
	esac

# Serial execution is requested with `-n 0`, NOT with `-p no:xdist`. Deactivating
# the plugin outright would leave the `-n logical` carried by the `addopts` line
# of pytest.ini unclaimed, and pytest then aborts with "unrecognized arguments:
# -n" (usage error, exit 4) before a single scenario is collected. `-n 0` keeps
# xdist loaded but switches distribution off, which is also what satisfies
# pytest-bdd's own guard: its Gherkin reporter refuses to install only when the
# xdist `dsession` plugin is registered, and `dsession` appears exclusively when
# the worker count is greater than zero. The preserved `-n logical` default in
# pytest.ini therefore stays untouched.
test-pretty: clean dirs ## Serial run with the Gherkin terminal reporter (no xdist)
	@echo "[make test-pretty] serial only: --gherkin-terminal-reporter is incompatible with xdist"
	@set +e; $(PYTEST) -n 0 --gherkin-terminal-reporter; code=$$?; set -e; \
	case $$code in 0|1|5) exit 0;; *) exit $$code;; esac

test-unit: dirs ## Run the unit suite (tag filter overridden)
	$(PYTEST) $(NO_TAG_FILTER) tests/unit

test-integration: dirs ## Run the integration suite (tag filter overridden)
	$(PYTEST) $(NO_TAG_FILTER) tests/integration

test-parity: dirs ## Run the parity suite - the behavioural acceptance gate
	$(PYTEST) $(NO_TAG_FILTER) tests/parity

test-suites: dirs ## Run unit + integration + parity in one session
	$(PYTEST) $(NO_TAG_FILTER) tests/unit tests/integration tests/parity

lint: ## ruff (no autofix) + black in check-only mode
	$(RUFF) check --no-fix .
	$(BLACK) --check .

format: ## Apply ruff and black formatting (never used by the verify gate)
	$(RUFF) check --fix .
	$(BLACK) .

typecheck: ## mypy static type checking
# A source root is only handed to mypy once it actually holds a type-checkable
# file. Testing mere directory existence is not enough: mypy exits 2 with "There
# are no .py[i] files in directory" when pointed at a directory that exists but
# contains none, which happens whenever the first file to land under a root is a
# non-Python asset - app/static/css/main.css, tests/features/login.feature or
# scripts/run_tests.sh, for example. Once every root holds Python this selects
# all three, exactly as before.
	@paths=""; for d in app tests scripts; do \
	  if [ -d "$$d" ] && [ -n "$$(find "$$d" \( -name '*.py' -o -name '*.pyi' \) -print 2>/dev/null | head -n 1)" ]; then \
	    paths="$$paths $$d"; \
	  fi; \
	done; \
	if [ -z "$$paths" ]; then \
	  echo "[make typecheck] no type-checkable sources under app/ tests/ scripts/ yet - nothing to type-check"; \
	else \
	  echo "[make typecheck] mypy$$paths"; $(MYPY) $$paths; \
	fi

verify: lint typecheck test-suites test ## Full verification gate
	@echo "[make verify] lint + typecheck + unit/integration/parity + ported BDD run complete"

report: dirs ## Regenerate the four report artifacts from target/cucumber.json
	$(PY) scripts/generate_reports.py

feature: ## Regenerate tests/features/login.feature byte-exactly from README.md
	$(PY) scripts/extract_feature_from_readme.py

run: dirs ## Start the Flask development server (port $(APP_PORT))
	APP_PORT=$(APP_PORT) $(PY) run.py

serve: dirs ## Start the production WSGI server (gunicorn)
	APP_PORT=$(APP_PORT) $(GUNICORN) -c gunicorn.conf.py wsgi:app

docker-build: ## Build the container image
	docker build -t $(DOCKER_IMAGE) .

docker-up: ## Start the service with docker compose (host port $(APP_PORT))
	CLONE_INDEX=$(CLONE_INDEX) APP_PORT=$(APP_PORT) \
	  docker compose -p $(COMPOSE_PROJECT) up -d --build

docker-down: ## Stop and remove the compose stack
	CLONE_INDEX=$(CLONE_INDEX) APP_PORT=$(APP_PORT) \
	  docker compose -p $(COMPOSE_PROJECT) down -v

info: ## Print the resolved toolchain versions
	@$(PY) --version
	@$(PIP) --version
	@$(PYTEST) --version
	@echo "APP_PORT=$(APP_PORT) COMPOSE_PROJECT=$(COMPOSE_PROJECT) TARGET_DIR=$(TARGET_DIR)"
