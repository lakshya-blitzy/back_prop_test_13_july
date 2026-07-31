 # :fallen_leaf: :leaves: Testinium-QA :leaves: :fallen_leaf:
Automating the Testinium browser  (PYTHON, Flask, Selenium, pytest-bdd, Jira, Jenkins)

### Tools

<p align="left">

<a href="https://www.python.org" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/python/python-original.svg" alt="python" width="60" height="60"/>
</a>

<a href="https://flask.palletsprojects.com" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/flask/flask-original.svg" alt="flask" width="60" height="60"/>
</a>

<a href="https://www.selenium.dev" target="_blank" rel="noreferrer">
  <img src="https://selenium.dev/images/selenium_logo_square_green.png" alt="selenium" width="60" height="60"/>
</a>

<a href="https://docs.pytest.org" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/pytest/pytest-original.svg" alt="pytest-bdd" width="60" height="60"/>
</a>
<a href="https://www.atlassian.com/software/jira" target="_blank" rel="noreferrer">
  <img src="https://i0.wp.com/invotra.com/wp-content/uploads/2019/09/jira_software_logo-e1571063680300.png?fit=768%2C216&ssl=1" alt="jira" width="160" height="60"/>
</a>
<a href="https://www.jenkins.io" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/jenkins/jenkins-original.svg" alt="jenkins" width="60" height="60"/>
</a>
</p>

* PYTHON
* FLASK
* SELENIUM
* PYTEST-BDD
* JIRA
* JENKINS

### Testinium-QA

This repository contains a collection of sample `Testinium-QA` projects and libraries that demonstrate how to
use the tool and develop automation script using the pytest-bdd BDD framework with Python as programming language.
It generates JSON, HTML and Txt reporters as well. It also generates `screen shots` for your tests if you enable it and
also generates `error shots` for your failed test cases as well.

A fourth report artifact joins those three: `target/cucumber/`, the PrettyReports-shaped report directory. Every
artifact still lands under `target/`, and every one is now additionally retrievable over HTTP from the Flask
service - see [Jenkins Cucumber Reports](#jenkins-cucumber-reports).

#### What changed, and what deliberately did not

The project was a Java SE 8 / Maven / Cucumber-JVM automation skeleton. It is now a CPython 3.14.6 / Flask
application in the same repository. Every construct was mapped one-to-one rather than redesigned:

| Concern | Was | Is now |
|---|---|---|
| Language / runtime | Java SE 8 (`maven.compiler.source` / `maven.compiler.target` = 8) | CPython 3.14.6, pinned in `.python-version`; `requires-python = ">=3.14"` |
| Build / dependencies | Maven POM, `org.example:testinium-qa:1.0-SNAPSHOT` | `pyproject.toml` (`testinium-qa`, `1.0.0.dev0`), `requirements.txt`, `requirements-test.txt`, `Makefile` |
| Orchestration | Groovy scripted pipeline with three stages (`Jenkins`) | Flask application factory (`app/__init__.py`) plus one service module per stage under `app/services/` |
| Test runner | `CukesRunner` with `@RunWith(Cucumber.class)` | `tests/step_defs/test_cukes_runner.py`, configured by `pytest.ini` |
| Test engine | Cucumber-JVM 7.2.3 + JUnit 4.13.2 | `pytest==9.1.1` + `pytest-bdd==8.1.0` |
| Parallelism | Surefire `parallel=methods`, `useUnlimitedThreads=true` | `pytest-xdist==3.8.0` with `-n logical` |
| Scenario source | Gherkin embedded in this README | `tests/features/login.feature`, byte-identical to that block |
| Step definitions | `/step_definitions/LoginSD.java` | `tests/step_defs/login_sd.py` plus Page Objects under `tests/pages/` |
| Browser driver | selenium-java 3.141.59 + WebDriverManager 5.1.0 | `selenium==4.46.0` + `webdriver-manager==4.1.2` via `tests/support/driver_factory.py` |
| Test data | JavaFaker 1.0.2 | `Faker==40.36.0` via `tests/support/data_factory.py` |
| Reporting | Four Cucumber plugins + `me.jvt.cucumber:reporting-plugin:7.2.0` | `app/reporting/*` adapters over pytest-bdd's native Cucumber JSON writer and `pytest-html` |
| Runtime configuration | git-ignored `configuration.properties` | `app/config.py` + `app/utils/properties.py`, with committed `.env.example` and `configuration.properties.example` templates |
| Artifact root | `target/`, wiped by `mvn clean` | `target/`, wiped by `make clean` - the name is retained on purpose (see below) |

`pom.xml` stays in the tree as a **read-only parity contract**: it is the authoritative record of every version,
plugin setting and threshold that the Python manifests mirror. It is never compiled and never edited, no Java
toolchain is needed to install, test, run or ship anything here, and no `.java` file is authored anywhere.

The `target/` directory name is retained deliberately. The Jenkins `cucumber` publisher matches reports with
`fileIncludePattern: '**/*.json'`; keeping the artifacts where they always were means that contract needs no
change at all. `target/` is git-ignored and is wiped and recreated on every run.

#### Preserved defects - read this before you trust a green run

The rewrite had to match the behaviour *and the logic* of the previous implementation, so its known defects are
part of the specification. Each one below is preserved as the **default** and made explicitly overridable
through configuration; none is silently corrected. `docs/migration-parity.md` is the authoritative register.

| ID | Defect | Status |
|---|---|---|
| D1 | Both `Examples` tables bind only to the **third** `Scenario Outline` (`@UPGN-288`). `@UPGN-286` and `@UPGN-287` have none, so `@UPGN-286` runs with the literal text `<username>` / `<password>` - and passes. | Preserved |
| D2 | The documented runner's tag expression `@LogOut` matches no scenario in the feature file, so the default run selects **zero** scenarios. | Preserved as the default, overridable |
| D3 | Nothing can fail the build: `testFailureIgnore=true` plus six `-1` publisher thresholds mean there is no build-time quality gate. | Preserved behind an ignore-failures switch that defaults to on |
| D4 | `@UPGN-288` feeds the value of the **password** column into the **username** field. | Preserved byte-for-byte |
| D5 | The comment above `@UPGN-288` says the expected message is the English "Please fill out this field"; the assertion expects the French `Veuillez renseigner ce champ.` The assertion is the executable truth. | Preserved exactly, never translated |
| D6 | `io.cucumber:cucumber-junit` was declared twice, at 7.2.3 and 7.3.4. | **Corrected - the only one.** A Python requirements file cannot express two versions of one distribution, and Maven already resolved to the last declaration alone, so the single pinned `pytest-bdd==8.1.0` reproduces the *resolved* behaviour exactly; only the redundant declaration disappears |
| D7 | Two contradictory repository URLs: the pipeline clones `Upgenix-QA`, this README instructs cloning `Testinium-QA`. | Preserved - both strings retained, the pipeline value is the runtime default |
| D8 | Cosmetic breakage: the CI file is named `Jenkins` rather than `Jenkinsfile` so it is not auto-discovered; the two `./image/*.png` links point at a directory that does not exist; the Gherkin fence carries no language hint; `Scenario Outline:Users` is missing the space after its colon. | Preserved and documented, with two exceptions: a discoverable `Jenkinsfile` alias is added, and the two report commands below have their U+2013 EN DASH replaced by a real `--` so that they parse |
| D9 | The second and third `Scenario Outline`s parse to the **identical** scenario name, and pytest-bdd derives the generated test-function name from the scenario name, so the third silently overwrites the second and `@UPGN-287` becomes unreachable. Collection yields exactly **six** tests and none of them is `@UPGN-287`'s. | Preserved |

Three consequences follow, and they are stated plainly here rather than left to be discovered:

* **Run exactly as documented, the suite executes nothing.** D2 deselects every scenario. With the tag filter
  removed, six of the seven authored scenario instances execute and `@UPGN-287` is silently dropped by D9.
* **The build can never fail.** D3 is intact, so a failing scenario does not fail the stage - and report
  generation still runs after a failed test stage.
* **Effective coverage is almost nil, by design.** Preserving D1, D2, D4 and D9 leaves the ported suite with
  very little real coverage. That is intentional parity, not a porting bug, which is exactly why
  `tests/parity/test_defect_preservation.py` asserts each defect explicitly. Every available fix is recorded in
  `docs/migration-parity.md` as a follow-up the owners may elect; none is applied here.

#### Exit-code policy

`testFailureIgnore=true` does not translate to "ignore everything". pytest reports outcomes through distinct
exit codes, and each is mapped deliberately by `app/services/test_runner_service.py` and by `make test`:

| pytest exit code | Meaning | Ported verdict |
|---|---|---|
| `0` | every selected scenario passed | success |
| `1` | scenarios ran and some failed | **success, non-gating** - the direct port of `testFailureIgnore=true`; the reports are still produced |
| `2` | run interrupted | hard failure |
| `3` | internal error | hard failure |
| `4` | usage or command-line error | hard failure |
| `5` | nothing was collected, or everything was deselected | **success over zero scenarios** - the default outcome under D2 |

Exit code `5` is the trap worth calling out: the previous pipeline went green in exactly that situation, so
treating a non-zero code as failure would make the port fail where the original succeeded.

#### Dependency mapping

Every declared Maven dependency has exactly one pinned Python counterpart. Nothing is declared as a range and
nothing floats:

| Maven coordinate (`pom.xml`, read-only) | Version | Python counterpart |
|---|---|---|
| `org.seleniumhq.selenium:selenium-java` | 3.141.59 | `selenium==4.46.0` |
| `io.github.bonigarcia:webdrivermanager` | 5.1.0 | `webdriver-manager==4.1.2` |
| `com.github.javafaker:javafaker` | 1.0.2 | `Faker==40.36.0` |
| `io.cucumber:cucumber-java` | 7.2.3 | `pytest-bdd==8.1.0` |
| `io.cucumber:cucumber-junit` (scope `test`) | 7.2.3 | `pytest-bdd==8.1.0` + `pytest==9.1.1` |
| `me.jvt.cucumber:reporting-plugin` | 7.2.0 | pytest-bdd's native Cucumber JSON writer + `pytest-html==4.2.0` |
| `junit:junit` | 4.13.2 | `pytest==9.1.1` (plain `assert`, with pytest's assertion rewriting) |
| `io.cucumber:cucumber-junit` (no scope) | 7.3.4 | collapses into the single pinned `pytest-bdd==8.1.0` - defect D6 |
| `org.apache.maven.plugins:maven-surefire-plugin` | 3.0.0-M5 | `pytest==9.1.1` + `pytest-xdist==3.8.0` + `pytest.ini` |

Two notes on that table, because neither is a like-for-like swap:

* **Selenium 3 to Selenium 4 is a major-version uplift.** The API surface changed materially and there is no
  supported Python binding equivalent to the Selenium 3 Java API. This is a deliberate, documented consequence
  of the migration. All browser interaction is confined to `tests/pages/*` and `tests/support/driver_factory.py`,
  so the surface affected by the difference is small and isolated.
* **`Faker==40.36.0` is declared and wired, and deliberately left unexercised.** JavaFaker was declared in the
  POM but never exercised by committed code, and the `Examples` tables supply static data. Exercising Faker
  would *add* behaviour, which parity forbids, so `tests/support/data_factory.py` exists and stays unused by
  default.

The third-party Cucumber reporting plugin has no counterpart at all, and needs none: pytest-bdd ships a native
Cucumber JSON writer that emits the same schema the Jenkins publisher consumes.

### Installation (pre-requisites)

1. Python 3.14.6
2. pip
3. venv
4. Any IDE
5. Browser driver (make sure you have your desired browser driver and class path is set)

`.python-version` pins `3.14.6` - the analogue of the POM's `maven.compiler.source` / `maven.compiler.target`
language level - and `pyproject.toml` declares `requires-python = ">=3.14"`. `pip` and `venv` both ship with
CPython; no separate build tool is required, and neither a JDK nor Maven is needed for anything in this
repository.

On item 5: Selenium 4 ships **Selenium Manager**, which resolves and downloads a matching driver automatically,
so no manual driver installation or `PATH` entry is normally needed. `webdriver-manager==4.1.2` is retained as
the one-to-one counterpart of the POM's WebDriverManager dependency, and the `DRIVER_MANAGER` environment
variable (`driver.manager` in `configuration.properties`) selects which of the two provisions the driver.

### Framework set up

Git:

    git clone https://github.com/BalamiRR/Testinium-QA.git

Manually :

Fork / Clone repository from [here](https://github.com/BalamiRR/Testinium-QA/archive/main.zip) or download zip and set
it up in your local workspace.

#### Two clone URLs, both kept (preserved defect D7)

The command above is the human clone instruction and is unchanged. The CI pipeline, however, clones a
**different** repository - `https://github.com/BalamiRR/Upgenix-QA.git` - and that is the value the ported
`app/services/clone_service.py` uses as its **runtime default**, because executable configuration outranks prose
when the two disagree and because the `UPGN` Jira key prefix used by the scenarios agrees with it.

Both strings are retained rather than silently unified: neither carries more information than the other, and
erasing either would lose it. One configuration key selects which is used - `CLONE_URL` in the environment or
`clone.url` in `configuration.properties` - and the README value is additionally kept as
`CLONE_URL_DOCUMENTED` / `clone.url.documented` so the discrepancy stays machine-readable and the
configuration endpoint can report both. This is defect D7, preserved deliberately; it is not a documentation
error. `.env.example` and `docs/configuration.md` spell it out key by key.

#### Python environment

    python3.14 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -r requirements-test.txt

`make venv` followed by `make install` does exactly the same thing with the pinned interpreter, and
`make install-dev` adds the optional quality tooling (`ruff`, `black`, `mypy`, `pytest-cov`). `requirements.txt`
holds the Flask runtime, `requirements-test.txt` holds the BDD harness, and every line in both is an exact
`==` pin.

#### Runtime configuration

    cp .env.example .env
    cp configuration.properties.example configuration.properties

Both copies are optional. A fresh checkout has neither file, that is the normal case rather than an error, and
every setting already has the same value hard-coded as a default. The real `configuration.properties` is
git-ignored, exactly as it always was, and so is `.env`; only the two `.example` templates are committed. They
document **keys**, never real credentials - never commit a secret to either of them.

Settings resolve through one precedence chain, highest first:

    explicit constructor argument -> environment variable -> .env file -> configuration.properties -> hard-coded default

The hard-coded defaults are the previous implementation's values, not invented ones:

| Setting | Default | Came from | Key (environment / properties) |
|---|---|---|---|
| Clone URL | `https://github.com/BalamiRR/Upgenix-QA.git` | the pipeline's clone stage | `CLONE_URL` / `clone.url` |
| Documented clone URL | `https://github.com/BalamiRR/Testinium-QA.git` | this README | `CLONE_URL_DOCUMENTED` / `clone.url.documented` |
| Tag expression | `LogOut` - pytest-bdd strips the leading `@` | the documented runner's `tags` option | `TAG_EXPRESSION` / `tag.expression` |
| Ignore test failures | `true` | Surefire `testFailureIgnore` | `IGNORE_TEST_FAILURES` / `ignore.test.failures` |
| Workers | `logical` (unlimited); `4` retained but disabled | Surefire `parallel` / `useUnlimitedThreads` / commented `threadCount` | `PYTEST_WORKERS` / `pytest.workers` |
| All six publisher thresholds | `-1` | the `cucumber` publisher step | `REPORT_FAILED_FEATURES_NUMBER`, `REPORT_FAILED_SCENARIOS_NUMBER`, `REPORT_FAILED_STEPS_NUMBER`, `REPORT_PENDING_STEPS_NUMBER`, `REPORT_SKIPPED_STEPS_NUMBER`, `REPORT_UNDEFINED_STEPS_NUMBER` |
| Report sorting | `ALPHABETICAL` | the publisher step | `REPORT_SORTING_METHOD` / `report.sorting.method` |
| Report include pattern | `**/*.json` | the publisher step | `REPORT_FILE_INCLUDE_PATTERN` / `report.file.include.pattern` |
| Artifact root | `target/` | the four Cucumber plugin paths | `TARGET_DIR` / `target.dir` |
| Expected empty-field message | `Veuillez renseigner ce champ.` | the third outline's assertion (D5) | `EXPECTED_EMPTY_FIELD_MESSAGE` / `expected.empty.field.message` |

Set `TAG_EXPRESSION` to an empty value to make a service-driven run execute the whole suite instead of the
preserved zero-selection default; a direct `pytest` invocation drops the filter with `-m ""`.

#### Command surface

| Command | What it does |
|---|---|
| `make venv` | create `.venv` with the pinned interpreter |
| `make install` | install `requirements.txt` and `requirements-test.txt` at their exact pins |
| `make install-dev` | additionally install `ruff`, `black`, `mypy` and `pytest-cov` |
| `make clean` | the port of `mvn clean`: wipe `target/` and every Python cache |
| `make dirs` | create the `target/` tree that a run needs to already exist |
| `make test` | the port of `mvn clean test`: clean, recreate `target/`, run pytest in parallel, apply the non-gating exit-code policy |
| `make test-pretty` | the same run serially, with pytest-bdd's Gherkin terminal reporter |
| `make test-unit`, `make test-integration`, `make test-parity`, `make test-suites` | run the Python suites with the preserved tag filter overridden |
| `make report` | regenerate the four report artifacts from `target/cucumber.json` |
| `make feature` | regenerate `tests/features/login.feature` byte-exactly from this README |
| `make lint` | `ruff check --no-fix` plus `black --check` - never rewrites source |
| `make typecheck` | `mypy` over `app/`, `tests/` and `scripts/` |
| `make verify` | the full gate: lint, typecheck, the Python suites, then the ported BDD run |
| `make run` | Flask development server |
| `make serve` | production WSGI server (gunicorn) |
| `make docker-build`, `make docker-up`, `make docker-down` | container lifecycle |
| `make info` | print the resolved toolchain versions |

`make verify` is what proves the port: it runs the thirteen executable validation criteria, of which the
parity suite under `tests/parity/` is the acceptance gate for behavioural fidelity.

#### Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | target architecture and module map |
| [docs/migration-parity.md](docs/migration-parity.md) | the authoritative D1-D9 defect register and the full POM-to-Python mapping |
| [docs/configuration.md](docs/configuration.md) | the precedence chain and every setting's source value |
| [docs/testing.md](docs/testing.md) | authoring and running scenarios |
| [docs/reporting.md](docs/reporting.md) | the four artifacts and how to obtain them |
| [docs/api.md](docs/api.md) | HTTP route reference |
| [docs/ci.md](docs/ci.md) | pipeline behaviour and the non-gating property |

#### Continuous integration

`Jenkins` is updated in place and keeps its `node {}` container, all three stage names, its `isUnix()` shell and
batch split, and its entire `cucumber` publisher invocation with all six `-1` thresholds, the
`fileIncludePattern: '**/*.json'` glob and `sortingMethod: 'ALPHABETICAL'`. Only the two build command strings
changed, from the Maven invocation to the Python one - `scripts/run_tests.sh` on Unix and
`scripts/run_tests.bat` on Windows, both of which are `make test` by another name.

`Jenkinsfile` is added as a discoverable alias with identical content, which addresses the half of defect D8
that would otherwise keep the pipeline from being auto-detected. `.github/workflows/ci.yml` is additive: it
gives the repository standalone verification without a Jenkins controller.

#### Container

`Dockerfile` builds on `python:3.14.6-slim` and starts the service through
`gunicorn -c gunicorn.conf.py wsgi:app`. `.dockerignore` keeps the build context lean, and
`docker-compose.yml` runs the application plus, behind an opt-in profile, a Selenium service for
browser-driven runs. `make docker-up` brings the stack up.

### Using canned test in the project:

The documented Java runner is now a Python module. pytest-bdd cannot execute a `.feature` file directly, so a
module has to bind the scenarios - which makes this module the exact counterpart of `CukesRunner`, and the
target that collection picks up:

```python
"""Ported CukesRunner: the collected target for the Gherkin specification."""

from pytest_bdd import scenarios

# `glue = "com/testinium/step_definitions"` has no pytest-bdd equivalent: step
# discovery is import-driven, so the step-definition module is imported outright.
from tests.step_defs import login_sd  # noqa: F401

# `features = "src/main/resources/features"` becomes this path argument, resolved
# against `bdd_features_base_dir` from pytest.ini -> tests/features/login.feature.
scenarios("login.feature")
```

Everything the `@CucumberOptions` annotation carried lives in `pytest.ini`, which is the single source of test
configuration - `pyproject.toml` deliberately contains no pytest table, so it can never silently supersede it:

```ini
[pytest]
bdd_features_base_dir = tests/features

addopts =
    -ra
    -m "LogOut"
    --cucumberjson=target/cucumber.json
    --html=target/cucumber-reports.html
    --self-contained-html
    --junitxml=target/surefire-reports/TEST-CukesRunner.xml
    -n logical
#   Surefire's commented-out tuning default is preserved here, disabled:
#   -n 4

markers =
    Login: Feature-level tag of the Testinium app login feature.
    UPGN-286: Jira traceability tag - users log in with valid credentials.
    UPGN-287: Jira traceability tag - invalid email / invalid password credentials.
    UPGN-288: Jira traceability tag - empty username or password field.
    SalesManager: Examples-table tag - SalesManager account data set.
    PosManager: Examples-table tag - PosManager account data set.
```

Every runner option has exactly one destination:

| Java runner option | Python target |
|---|---|
| `@RunWith(Cucumber.class)` | the `scenarios()` call in `tests/step_defs/test_cukes_runner.py` - that module *is* the collected target |
| `plugin = "html:target/cucumber-reports.html"` | `--html=target/cucumber-reports.html --self-contained-html` (`pytest-html`) |
| `plugin = "json:target/cucumber.json"` | `--cucumberjson=target/cucumber.json` (pytest-bdd's native writer) |
| `plugin = "rerun:target/rerun.txt"` | the rerun writer in `app/reporting/rerun_report.py`, derived from the JSON report |
| `plugin = "me.jvt.cucumber.report.PrettyReports:target/cucumber"` | `app/reporting/pretty_reports.py`, post-processing `target/cucumber.json` |
| `features = "src/main/resources/features"` | the `scenarios()` path argument, resolved to `tests/features/login.feature` |
| `glue = "com/testinium/step_definitions"` | ordinary Python imports - pytest-bdd has no glue mechanism |
| `dryRun = false` | the absence of `--collect-only` |
| `tags = "@LogOut"` | `addopts = -m "LogOut"` in `pytest.ini` |

The Maven Surefire configuration ports the same way:

| Surefire setting | Python target |
|---|---|
| `<parallel>methods</parallel>` + `<useUnlimitedThreads>true</useUnlimitedThreads>` | `-n logical` (pytest-xdist) |
| `<!-- <threadCount>4</threadCount> -->` | a **commented** `# -n 4` line in `pytest.ini`, preserved as a disabled tuning default |
| `<testFailureIgnore>true</testFailureIgnore>` | the non-gating exit-code policy above |
| `<includes>**/CukesRunner*.java</includes>` | collection of `tests/step_defs/test_cukes_runner.py` |

#### The default run selects ZERO scenarios (preserved defect D2)

`tags = "@LogOut"` is reproduced faithfully as `-m "LogOut"`, and **no scenario carries that tag**. The feature
file's only tags are `@Login`, `@UPGN-286`, `@UPGN-287`, `@UPGN-288`, `@SalesManager` and `@PosManager`, so a
default run reports `6 deselected, 0 selected`, pytest exits with code `5`, and that outcome is reported as
**SUCCESS** - a successful zero-scenario run. This is preserved behaviour, not a misconfiguration: the previous
pipeline went green in precisely this situation, because Surefire simply found nothing matching the tag filter
and the publisher's all-`-1` thresholds gated nothing.

To run the scenarios anyway, override the tag expression rather than editing `pytest.ini`:

    pytest -m ""

A command-line `-m` wins over the one carried by `addopts`, so that single flag drops the filter for a direct
run. For runs driven through the service layer instead - `POST /api/v1/runs`, or
`app/services/test_runner_service.py` - set the `TAG_EXPRESSION` environment variable (or `tag.expression` in
`configuration.properties`) to an empty value, which is the configuration key that feeds the same filter.

The Python suites under `tests/unit`, `tests/integration` and `tests/parity` are not part of the ported
Cucumber run and override the filter themselves; `make test-suites` runs all three.

### Develop automation scripts using BDD approach - Cucumber-Java

The heading keeps its original wording because the documented capability is unchanged - only the engine
underneath it moved from Cucumber-JVM to pytest-bdd.

There are already many predefined StepDefinitions which is packaged under `tests/step_defs/login_sd.py` will help you speed
up your automation development that support both your favorite workaday helpers methods.

Those step definitions hold no selectors. Locators and page behaviour live in the Page Object layer -
`tests/pages/base_page.py` (explicit waits), `tests/pages/login_page.py` and `tests/pages/dashboard_page.py` -
and the shared machinery lives in the support layer: `tests/support/driver_factory.py` (driver provisioning),
`tests/support/data_factory.py` (the Faker wrapper), `tests/support/screenshots.py` (screen shots and error
shots) and `tests/support/config_reader.py` (cached `configuration.properties` access). Fixtures in
`tests/conftest.py` and `tests/step_defs/conftest.py` inject the driver and the page objects, replacing
Cucumber's field injection.

Tests are written in the pytest-bdd framework using the Gherkin Syntax.
Here is one of the scenarios:

```
@Login
Feature: Testinium app login feature
  User Story:
  As a user, I should be able to login with correct credentials to different accounts.

  Accounts are: PosManager, SalesManager

  Background: For the scenarios in the feature file, user is expected to be on login page
    Given User is on the Testinium login page

  #1-Users can log in with valid credentials (We have 5 types of users but will test only 2 user: PosManager, SalesManager)
  @UPGN-286
  Scenario Outline: Users log in with valid credentials
    When User enters "<username>" username
    And User enters "<password>" password
    And User clicks the login button
    Then User should see the dashboard
  
  #2-"Wrong login/password" should be displayed for invalid (valid username-invalid password and invalid username-valid password) credentials
  @UPGN-287
  Scenario Outline: Users log in with invalid email or invalid password credentials
    When User enters "<username>" username
    And User enters "<password>" password
    And User clicks the login button
    Then User sees error message
    
  #3- "Please fill out this field" message should be displayed if the password or username is empty
  @UPGN-288
  Scenario Outline:Users log in with invalid email or invalid password credentials
    When User enters "<password>" username
    And User clicks the login button
    Then User sees "Veuillez renseigner ce champ." message

    @SalesManager
    Examples: SalesManager's username and password
      |username               |password    |
      |salesmanager7@info.com |salesmanager|
      |salesmanager8@info.com |salesmanager|
      |salesmanager9@info.com |salesmanager|
      
    @PosManager
    Examples: PosManager's username and password
      |username               |password  |
      |posmanager5@info.com   |posmanager|
      |posmanager6@info.com   |posmanager|
```

That block is the project's only executable specification, so it is reproduced in
`tests/features/login.feature` **byte for byte**: 1864 bytes, 45 lines, pure ASCII, three of which contain
nothing but spaces. Byte-exactness is not cosmetic - it is what guarantees the ported suite behaves
identically, defects included. `scripts/extract_feature_from_readme.py` (or `make feature`) regenerates the
feature file from this very block, so the copy is reproducible rather than a one-off, and
`tests/parity/test_feature_file_byte_parity.py` asserts that the bytes still match.

What is wrong with that specification, and stays wrong:

* **D1 - the `Examples` tables bind only to the third outline.** Gherkin attaches an `Examples` table to the
  outline immediately preceding it, so both tables belong to `@UPGN-288`. `@UPGN-286` and `@UPGN-287` have
  none, which means `@UPGN-286` executes with the literal text `<username>` and `<password>` - and passes. The
  step definitions must not "helpfully" bind the tables to the earlier outlines.
* **D4 - the password goes into the username field.** `When User enters "<password>" username` reads from the
  password column, so the generated Cucumber JSON records `User enters "salesmanager" username` and
  `User enters "posmanager" username`, never the email addresses. The defect is observable in the published
  report, and `tests/parity/test_defect_preservation.py` asserts it.
* **D5 - the comment and the assertion disagree.** The comment describes an English message; the assertion
  expects the French `Veuillez renseigner ce champ.` The assertion is the executable truth, so that 29-byte
  pure-ASCII string is compared exactly - never translated, never normalised, never stripped of its trailing
  period.
* **D8 - `Scenario Outline:Users` is missing the space after its colon**, and the fence above opens with no
  language hint. Both are preserved.
* **D9 - `@UPGN-287` is unreachable.** Gherkin treats the colon purely as a keyword separator, so the second
  and third outlines parse to the *identical* scenario name. pytest-bdd derives the generated test-function
  name from the scenario name, so both bind the same symbol and the later definition silently replaces the
  earlier one. Collection therefore yields exactly **six** tests - one for `@UPGN-286` plus five
  parametrisations of the shared name - and none of them is `@UPGN-287`'s. This was proven, not inferred: an
  always-failing assertion injected into `@UPGN-287`'s final step never executed, and renaming only that
  scenario in an otherwise identical copy produced seven tests and made the injected failure fire.
  `tests/parity/test_defect_preservation.py` asserts the six-test count so the silent defect is auditable, and
  the one-line fix is recorded in `docs/migration-parity.md` as a follow-up that is explicitly not applied.

Put together with D2: run exactly as documented, the suite executes nothing at all. Remove the tag filter and
six of the seven authored scenario instances execute, with `@UPGN-287` silently dropped.

### Jenkins Cucumber Reports
![alt text](./image/Jenkins-Cucumber-Reports.png)

That screenshot is not in the repository - the `image/` directory has never existed, and the link is left
exactly as it was (defect D8, preserved). The artifacts themselves are real, and all of them land under
`target/`:

| Artifact | Ports |
|---|---|
| `target/cucumber.json` | `json:target/cucumber.json` - pytest-bdd's native `--cucumberjson` writer; this is the file the publisher's `**/*.json` pattern matches |
| `target/cucumber-reports.html` | `html:target/cucumber-reports.html` - a self-contained `pytest-html` report |
| `target/rerun.txt` | `rerun:target/rerun.txt` - one `<feature-uri>:<scenario-line>` line per failing scenario, for example `features/login.feature:21`, derived deterministically from the JSON |
| `target/cucumber/` | `me.jvt.cucumber.report.PrettyReports:target/cucumber` - rendered by post-processing `target/cucumber.json`, just as the Java plugin rendered a directory from the JSON |
| `target/screenshots/` | `screen shots`, written when they are enabled |
| `target/error-shots/` | `error shots`, written for failed test cases |
| `target/surefire-reports/` | the Surefire report directory, retained by name for report-consumer parity and populated with JUnit XML |

Two mechanical details matter. `target/` must exist **before** a run: the Cucumber JSON writer does not create
its parent directory and fails at session finish if it is missing, where Maven used to create it implicitly.
Creation is guaranteed in three places - `app/utils/paths.py`, the `Makefile` test target and
`tests/conftest.py`. And pytest-bdd's Gherkin terminal reporter cannot coexist with pytest-xdist: it raises
during configuration and aborts the run. The default invocation is therefore the parallel one - `-n logical`,
the port of `parallel=methods` with unlimited threads - and it excludes that reporter; `make test-pretty` is
the serial escape hatch. Parallelism costs nothing in report fidelity: a parallel run emits a complete Cucumber
JSON containing every scenario.

Reports are also retrievable over HTTP, which is what the Flask service adds to the previous behaviour:

| Method and path | Serves |
|---|---|
| `GET /health` | liveness probe - the one additive endpoint |
| `GET /api/v1/config` | the preserved constants: both clone URLs, the tag expression, the six thresholds, the sorting method, the include pattern |
| `POST /api/v1/clone` | the `'Clone code'` stage |
| `POST /api/v1/runs` | the `'Run tests'` stage |
| `GET /api/v1/runs/<run_id>` | run status and summary, derived from the JSON report |
| `POST /api/v1/reports` | the `'Generate report'` stage |
| `GET /api/v1/reports/<run_id>/cucumber.json` | `target/cucumber.json` |
| `GET /api/v1/reports/<run_id>/rerun.txt` | `target/rerun.txt` |
| `GET /api/v1/reports/<run_id>/screenshots` | the screen shots and error shots |
| `GET /` and `GET /reports` | a rendered index over the generated artifacts |

##### HTML Report:

To generate HTML report use  `python scripts/generate_reports.py --plugin html:target/cucumber-reports.html`

##### Txt Report:

To generate a Txt report Use `python scripts/generate_reports.py --plugin rerun:target/rerun.txt`

Both commands are direct ports of the two documented Maven ones, whose `plugin` switch was printed with a
U+2013 EN DASH instead of a double hyphen and so could not have parsed as written; the ASCII `--` above is one
of only two cosmetic corrections this migration makes. `make report` regenerates all four artifacts from
`target/cucumber.json` in one step, and a full `make test` produces them as part of the run.

### Jira Test Execution

  ![alt text](./image/Jira-Test-Exectuion.png)

That image is absent for the same reason as the one above, and the link is preserved as-is (defect D8).

Traceability survives through **tag names alone**. `@UPGN-286`, `@UPGN-287` and `@UPGN-288` remain in the
feature file and become pytest markers with identical names, the leading `@` stripped by pytest-bdd, so a
scenario is still selectable by its Jira key:

    pytest -m "UPGN-288"

All six tags are registered as markers in `pytest.ini` - `Login`, `UPGN-286`, `UPGN-287`, `UPGN-288`,
`SalesManager` and `PosManager` - plus the runner's own `LogOut` selector. Registration is not optional:
an unregistered mark raises `PytestUnknownMarkWarning` on every run, and each xdist worker warns
independently, so what is eight warnings serially becomes a flood under `-n logical`. Hyphenated marker names
are legal in the markers list and remain selectable by tag expression.

No Jira client is introduced. The Jira instance, its projects and its issues are outside this repository; the
convention is the whole of the integration, exactly as before.

### THE END
