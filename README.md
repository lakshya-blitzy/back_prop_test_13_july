 # :fallen_leaf: :leaves: Testinium-QA :leaves: :fallen_leaf:
Automating the Testinium browser  (Python, Flask, behave, Selenium, pytest, Jenkins)

### Tools

<p align="left"> 

<a href="https://www.python.org" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/python/python-original.svg" alt="python" width="60" height="60"/>
</a>

<a href="https://www.selenium.dev" target="_blank" rel="noreferrer">
  <img src="https://selenium.dev/images/selenium_logo_square_green.png" alt="selenium" width="60" height="60"/> 
</a>    

<a href="https://flask.palletsprojects.com" target="_blank" rel="noreferrer">
  <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/flask/flask-original.svg" alt="flask" width="60" height="60"/>
</a>

<a href="https://www.jenkins.io" target="_blank" rel="noreferrer">
  <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/e/e9/Jenkins_logo.svg/1200px-Jenkins_logo.svg.png" alt="jenkins" width="50" height="80"/>
</a>
</p>

* **PYTHON** - the language and runtime, pinned to 3.14.6 exactly
* **FLASK** - a read-only viewer over the report artifacts a run has already produced
* **BEHAVE** - the Gherkin engine that runs the feature files in `features/`
* **SELENIUM** - browser automation, Chrome and Firefox
* **PYTEST** - the unit suite covering this project's own code
* **JENKINS** - the three-stage pipeline and the Cucumber report publisher
* **JIRA** - historical documentation only. There is no Jira integration in this
  project: no code here reads from or writes to Jira. The `Jira Test Execution`
  section further down is kept for historical context only.

### Testinium-QA

This repository contains the `Testinium-QA` browser test-automation suite and the libraries it is built from:
Gherkin feature files driven by behave, page objects that drive Chrome or Firefox through Selenium, and a
read-only Flask viewer over the results. Every run writes the same four report artifacts - a JSON report, two
HTML reports and a Txt rerun manifest.

`Screen shots` are captured for **failed scenarios only**. A failing scenario has its screenshot taken once,
while the browser session is still live and before the driver is quit. There is **no setting that enables or
disables this**: passing scenarios are never captured, and a failing one always is. The PNG is embedded in
`target/cucumber.json`, under the failed scenario's `after` array, as

    "embeddings": [{"mime_type": "image/png", "data": "<base64 PNG>", "name": "<scenario name>"}]

and both HTML reports render it as a `data:` URI behind a lightbox. If the capture itself fails - a browser
session that has already died, for instance - the error is logged to stderr, no embedding is written, and the
scenario's status is left exactly as it was. A failed capture never changes a test outcome.

### Installation (pre-requisites)

1. **Python 3.14.6** - required, and pinned to exactly that version. `.python-version` declares `3.14.6` and
   `pyproject.toml` declares `requires-python = "==3.14.*"`. Both runner scripts locate a 3.14.6 interpreter
   and fail with a clear message rather than falling back to whatever `python3` happens to resolve to. The
   supported range is deliberately narrow rather than open-ended, so CI and development cannot drift apart.
2. **Chrome or Firefox**, matching the `browser` property - required to run scenarios. `webdriver-manager`
   provisions the matching driver binary for you, but the browser itself has to be installed. These two are
   the only browsers the driver constructs; there is no default branch, so any other value simply fails at
   the first driver call. Internet Explorer is not supported.
3. **Git** - required by the pipeline's checkout stage.
4. **A POSIX shell or PowerShell** - one or the other, whichever the pipeline's `isUnix()` branch selects:
   `scripts/run_tests.sh` on Unix, `scripts/run_tests.ps1` on Windows.
5. **`make`** - optional, developer convenience only. The pipeline calls the two scripts directly and never
   invokes make.
6. **A reachable Odoo instance and a populated `configuration.properties`** - required to run scenarios, and
   supplied by neither this repository nor the original one. What ships here is
   `configuration.properties.example`, a template carrying the six keys with no values, so the suite does
   **not** pass out of the box: point it at your own instance first. Everything that does not drive a
   browser - the unit suite, the four report writers, the report viewer and `--dry-run` - works without it.

### Build system

The build is `pyproject.toml` together with the pinned `requirements.txt` and `requirements-test.txt`, and it
is the only supported build. The pins are exact, never floating:

| File | Pins |
|---|---|
| `requirements.txt` | `Flask==3.1.3`, `Jinja2==3.1.6`, `click==8.5.0`, `behave==1.3.3`, `cucumber-tag-expressions==11.0.1`, `selenium==4.48.0`, `webdriver-manager==4.1.2` |
| `requirements-test.txt` | `pytest==9.1.1`, `pytest-cov==7.0.0` |
| `pyproject.toml` | `requires-python = "==3.14.*"`, and `[project.scripts] run-tests` - the one console script |
| `.python-version` | `3.14.6` |

`pom.xml` is still in the tree, and it is **historical reference only - not a supported build
configuration**. There is no Maven build here any more: nothing in this project, in the scripts or in the
pipeline invokes one. It is kept because it records the execution semantics this suite reproduces, and for
no other reason, so please do not read it as the live build definition.

### Framework set up

Git:

    git clone <this repository>
    cd <the checkout>

Then, from the repository root:

1. Create and activate a virtual environment on Python 3.14.6:

        python3.14 -m venv .venv
        . .venv/bin/activate

   On Windows the activation script is `.venv\Scripts\Activate.ps1` and the executables live in
   `.venv\Scripts` rather than `.venv/bin`.

2. Install the pinned dependencies, then this project itself:

        python -m pip install -r requirements.txt -r requirements-test.txt
        python -m pip install -e .

   The second command is not optional: the editable install is what materialises the `run-tests` console
   script in `.venv/bin` (`.venv\Scripts` on Windows). Without it there is no `run-tests` to call.

3. Copy the configuration template and fill in the six values:

        cp configuration.properties.example configuration.properties

   `configuration.properties` has to sit in the **process working directory**, which is the repository root
   when the commands below are run from there: the reader opens the bare relative name
   `configuration.properties`, exactly as the original implementation did. The real file is git-ignored and
   `configuration.properties.example` is the committed template. See [Configuration](#configuration) for the
   six keys and the file format.

4. Run the suite - see [Running the suite](#running-the-suite):

        .venv/bin/run-tests

`scripts/run_tests.sh` and `scripts/run_tests.ps1` do all of the above automatically and non-interactively:
probe for a 3.14.6 interpreter, create or reuse `.venv`, install the pins and the editable install, run
pytest as a unit gate, then invoke `run-tests` with whatever arguments they were given. That is what CI runs,
and it is the quickest way to get a clean checkout running:

    sh scripts/run_tests.sh
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1

### Running the suite

One command, `run-tests`, because the original had exactly one entry point that produced artifacts. It is a
console script declared in `pyproject.toml` under `[project.scripts]` and implemented in `app/cli.py`, and
every caller invokes it from the virtual environment's `bin` directory (`Scripts` on Windows): both runner
scripts, the `Makefile`, and the examples below. Nothing invokes it through the Flask command line.

    .venv/bin/run-tests                                   # the defaults - @Smoke, browser from the properties file
    .venv/bin/run-tests --tags '@Login and not @wip'
    .venv/bin/run-tests --browser firefox --workers 4
    .venv/bin/run-tests --dry-run                         # resolve every step, start no browser
    .venv/bin/run-tests --rerun                           # only the scenarios that failed last time

There is no `generate-reports` command, and none is planned. The original's two documented report commands
re-ran the tests with a chosen plugin rather than rebuilding anything, so rebuilding reports from stored
results would be an addition nobody asked for, carrying its own durable-state contract. The run writes the
reports.

#### Options

| Option | Default | Behaviour |
|---|---|---|
| `--tags EXPR` | `@Smoke` | Tag expression selecting what to run, in the full grammar - `@Smoke`, `@Login and not @wip`, `@UPGN-286 or @UPGN-287`. `@Smoke` is the default the original runner declared. Rejected together with `--rerun` |
| `--browser NAME` | the `browser` property | `chrome` or `firefox`. Forwarded to every worker as behave userdata (`-D browser=...`) and read back through `app/config.py`, whose precedence is **userdata first, then the properties file**. This is the only override path: there is no environment-variable layer. The value is not validated here, so anything else fails at the first driver call, exactly as it did before |
| `--workers N` | the CPU count | How many worker processes share the selected scenarios, sharded one scenario at a time. `--workers 1` runs sequentially. Never exceeds the number of scenarios selected |
| `--dry-run` | off | Resolve every step without executing it and report the artifacts accordingly. No browser is started |
| `--rerun` | off | Re-run only the scenarios `target/rerun.txt` recorded as failing. **Applies no tag filter at all** - the original rerun runner declared none, and applying the default would silently skip failures from features that are not tagged `@Smoke`. `--rerun` together with `--tags` is a usage error. It writes **no** artifacts and leaves the existing ones untouched |
| `--clean` / `--no-clean` | `--clean` | Empty `target/` before running, as `clean test` did in the old build. **Ignored under `--rerun`**, which must not delete the manifest it reads |

#### What the defaults actually select

The default tag expression is `@Smoke`, and `@Smoke` occurs exactly once in the whole suite - at
`Crm.feature:1`. **A default run therefore executes the CRM feature and nothing else.** That is the
original runner's behaviour, preserved deliberately; pass `--tags` to select anything wider.

Five features carry no feature-level tag at all - `Contact`, `Inventory`, `Notes`, `Sales` and `Session` -
so no positive expression over feature tags reaches them, while a negative one such as `--tags 'not @Smoke'`
does. Several of their scenarios carry their own tags and can be selected directly.

#### Artifacts

Every run writes the same four artifacts, at the same paths the original plugin list named:

| Path | What it is | Who reads it |
|---|---|---|
| `target/cucumber.json` | The Cucumber JSON report | The Jenkins Cucumber publisher, which matches this exact path |
| `target/cucumber-reports.html` | A single self-contained HTML page | People |
| `target/rerun.txt` | The rerun manifest - one line per feature with its failing line numbers | `--rerun`, which re-runs exactly those scenarios |
| `target/cucumber/` | The multi-page HTML report tree, entered at `cucumber/cucumber-html-reports/overview-features.html` | People |

Per-worker intermediate results live under `target/.workers/` while a run is in progress and are removed
before the command returns, so nothing intermediate is ever left for the publisher to pick up.

#### Exit status

**A test outcome never affects the exit status.** Scenario failures, errors, undefined or skipped steps, a
browser that fails to start, an unrecognised `browser` value, a feature that fails to parse, a missing or
malformed rerun manifest, and a tag expression that selects nothing all exit `0` with all four artifacts
written from whatever executed. That is the old build's `testFailureIgnore` behaviour and the six `-1`
publisher thresholds, carried over unchanged, and it is why a red suite still leaves a green stage.

Only failures outside test execution are non-zero:

| Status | Situation | Artifacts |
|---|---|---|
| `0` | Any test outcome, including an empty selection | All four written - except under `--rerun`, which writes none by design |
| `2` | An unknown or conflicting option, including `--rerun` with `--tags` | None; nothing executed |
| `3` | A worker process produced no results | Written from the shards that completed; the incomplete shard is named on stderr |
| `4` | The merge produced no result set at all | None |
| `5` | A report writer failed | Those written before the failure are retained; the failing writer is named on stderr |

Progress goes to stdout and diagnostics to stderr, both line-buffered, so a CI log stays live.

#### Developer targets, and what CI runs

`make` is optional convenience and wraps the same entry points:

| Target | Does |
|---|---|
| `make test` | Runs the Gherkin suite through `run-tests`; forward options with `ARGS`, e.g. `make test ARGS=--dry-run` |
| `make unit` | Runs the pytest suite; selection comes from `pytest.ini` |
| `make coverage` | Runs the four per-package coverage gates - `app/utils` 90, `app/pages` 85, `app/automation` 80, `app/reporting` 80 - stopping at the first miss |
| `make clean` | Removes the generated `target/` tree and the Python, pytest and coverage caches. Never touches `configuration.properties` |

CI runs neither `make` nor `run-tests` directly. The pipeline's `isUnix()` branch selects
`scripts/run_tests.sh` or `scripts/run_tests.ps1`, and each of those runs `pytest` as a status-propagating
unit gate before invoking `run-tests`: a failing unit suite fails the stage, while the suite run keeps the
exit contract above.

### Configuration

Six keys, read from `configuration.properties` in the process working directory - the repository root, when
the commands above are run from there. These six are the whole surface: nothing reads any other name, so a
seventh key added to the file simply has no effect.

| Key | Purpose |
|---|---|
| `browser` | `chrome` or `firefox`; selects the driver |
| `web.table.url` | The login URL |
| `url` | The Employee module URL |
| `username` | Shared-precondition login name |
| `password` | Shared-precondition password |
| `EmplTitle` | Expected page title in the Employee flow |

`--browser` on the command line outranks the `browser` property for that run, and is the only override path
there is; the other five keys come from the file alone.

**Nothing is required at startup.** A missing `configuration.properties` is logged and the run continues, and
a missing key yields no value at all, so a failure surfaces at the point of use rather than at startup. That
tolerance is the original behaviour, preserved deliberately, and it is what lets the unit suite, the four
report writers, the report viewer and `--dry-run` work with no configuration file whatsoever. The file is
read and cached on first access in each worker process and never re-read, so editing it while a run is in
progress changes nothing for a reader that has already initialised.

`configuration.properties.example` is the committed template: the same six keys, with **empty values** and a
comment each. No values are supplied here, because the right ones belong to the instance being tested and no
populated file exists in either revision of this project. Copy it, fill it in, and leave the copy out of
version control - `configuration.properties` is git-ignored, and it holds sign-in credentials.

The format is `java.util.Properties`, not INI: no section headers; `=`, `:` or whitespace separating a key
from its value; `#` or `!` starting a comment; a trailing backslash continuing a line; `\uXXXX` escapes
decoded; and ISO-8859-1 assumed for the bytes. Key names are case-sensitive and are read by exactly the
names above - note that `web.table.url` contains dots, and that Python's `configparser` rejects a
section-less file outright, which is why `app/utils/properties.py` implements the format directly instead.




### Using canned test in the project: `behave.ini` and `run-tests`

What the original runner class fixed in annotations now lives in two places: `behave.ini` holds the engine's
own defaults, and the `run-tests` options above cover everything a caller may want to change.

```ini
[behave]
paths = features
default_tags = @Smoke
dry_run = false
show_timings = true
junit = false
```

`behave.ini` declares **no formatter and no outfile key**, on purpose. A static file cannot give each worker
its own output path, so the formatter and its `-o <path>` are passed per worker on the command line, from the
one module that owns artifact paths, `app/utils/paths.py`. Nothing generates `behave.ini`; it is maintained
by hand, and so is `pytest.ini` beside it, which holds test selection for the unit suite and no thresholds.

One note for anyone comparing against the original runner block this section used to show: the tag
expression it advertised - the logout tag - was never the one the implementation ran with. `@Smoke` is the
real default, as above.

### Develop automation scripts using BDD approach - behave

The step definitions are already written, and they are the quickest way into the project. They live under
`features/steps/` as ten modules, one per feature area, which between them cover signing in, navigating the
menus, filling forms, keyboard and action-chain interaction, and the assertions the features make:

    features/steps/calendar_steps.py     features/steps/login_steps.py
    features/steps/contacts_steps.py     features/steps/logout_steps.py
    features/steps/crm_steps.py          features/steps/notes_steps.py
    features/steps/employee_steps.py     features/steps/sales_steps.py
    features/steps/inventory_steps.py    features/steps/session_steps.py

Around them:

* `features/environment.py` owns the scenario lifecycle: it creates a driver before each scenario and, after
  it, captures a screenshot when the scenario failed and quits the driver either way. Exactly one live
  browser session exists per worker at a time, and every scenario gets a fresh one.
* `app/pages/` holds the page objects - one per feature area plus a shared `base_page.py`. Each declares
  locator constants only and resolves them lazily, on access, so a page object can be built before the
  browser has navigated anywhere.
* `app/automation/` is the only package that imports Selenium. Step modules and page objects reach the
  driver, the explicit waits and the keyboard and action-chain helpers through it, never directly.
* Every step definition registers with behave's `@step`, which matches on the step text regardless of the
  `Given` / `When` / `Then` / `And` keyword that invoked it - the way the original engine matched.

Tests are written in Gherkin. All ten feature files live in `features/`, under the names they have always
had, because those names appear in the JSON report's `uri`, in the rerun manifest and in your own commands:

    features/Calendar.feature
    features/Contact.feature
    features/Crm.feature
    features/EmployeeFc.feature
    features/Inventory.feature
    features/Login.feature
    features/Logout.feature
    features/Notes.feature
    features/Sales.feature
    features/Session.feature

Read the scenarios there rather than here. A Gherkin excerpt copied into a README goes stale - the one this
section used to carry had drifted from the feature file it was copied from - so `features/` is the only
place the scenarios are written down.

### Report viewer

A small Flask application serves the artifacts a run has already produced. It is **read-only**: every route
reads, none writes, and **no route starts a test run** - execution stays on the command line.

    .venv/bin/python run.py                  # development server on http://127.0.0.1:5000

| Route | Response | Shows |
|---|---|---|
| `GET /` | HTML | Whether each of the four artifacts exists and when it was last modified, with links. Answers 200 even before the first run |
| `GET /reports` | HTML | The overview built from `target/cucumber.json` |
| `GET /reports/features/<int:findex>` | HTML | One feature, keyed by its zero-based position in the JSON rather than by its id, because two pairs of features share an id |
| `GET /reports/features/<int:findex>/scenarios/<int:sindex>` | HTML | One scenario of that feature, by its zero-based position among the feature's elements |
| `GET /reports/summary` | JSON | Counts of features, scenarios and steps by status, plus the run's earliest `start_timestamp` |
| `GET /artifacts/<path:name>` | File | An allowlisted artifact only - `cucumber-reports.html`, `cucumber.json`, `rerun.txt`, or a path under `cucumber/`, where a request for the directory itself serves the tree's overview page. Everything else is 404 |

If `target/cucumber.json` is absent, unreadable or unparseable, the four report routes answer **404** - the
same answer for all three causes, because a run has not produced usable results and deciding which of the
three it was is not the viewer's job. `/` still answers 200.

`run.py` is the development entry point: it listens on `127.0.0.1:5000`, `FLASK_PORT` moves the port when
several checkouts have to serve at once, and `FLASK_DEBUG` opts into the reloader and debugger, both off by
default. `wsgi.py` publishes the same application - as `app` and as `application` - for a WSGI server. There
is no container image and no production server configuration in this repository, deliberately.


### Jenkins Cucumber Reports
![alt text](./image/Jenkins-Cucumber-Reports.png)

##### HTML Report:

Both HTML reports are written by every run, with no extra command and no plugin to select:
`target/cucumber-reports.html` is a single self-contained page, and `target/cucumber/` is the multi-page
tree - open `target/cucumber/cucumber-html-reports/overview-features.html`. Each renders a failed
scenario's screenshot inline, behind a lightbox.

##### Txt Report:

The Txt report is `target/rerun.txt`, the rerun manifest: one line per feature, carrying the line numbers of
the scenarios that failed. `run-tests --rerun` reads it back and re-runs exactly those scenarios, writing no
artifacts of its own.

### Jira Test Execution

  ![alt text](./image/Jira-Test-Exectuion.png)
  

  

### THE END

