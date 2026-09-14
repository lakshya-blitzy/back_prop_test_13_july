#Requires -Version 5.1
<#
.SYNOPSIS
    Windows entry point for the Testinium-QA test run: bootstrap the pinned
    Python environment, gate on the unit suite, then run the Gherkin suite.

.DESCRIPTION
    WHAT THIS FILE IS
    -----------------
    The payload of the Jenkins pipeline's Windows branch. Jenkins:6-12
    declares

        stage('Run tests'){
            if(isUnix()){
                sh "..."      <- Jenkins:8,  scripts/run_tests.sh
            } else {
                bat "..."     <- Jenkins:10, replaced by THIS script
            }
        }

    and after the Python port that second branch reads

        bat "powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1"

    The branch itself, the stage names and their order are the pipeline's and
    are unchanged: shell selection stays with isUnix(), so this file is a
    payload and not a platform abstraction. It performs no platform detection
    of its own and never delegates to its POSIX counterpart. The report
    publisher is a separate later stage (Jenkins:15) which owns all REPORT
    thresholding - its six thresholds are -1 and its sorting is ALPHABETICAL -
    so nothing here thresholds, sorts, inspects or post-processes a report
    artifact.

    The coverage thresholds step 5 applies are a different thing entirely and
    must not be confused with those six: they gate the line coverage of this
    port's own Python code, the Makefile's `coverage` target is their canonical
    declaration, and they say nothing about a scenario, a feature or a report.

    THE BEHAVIOURAL MIRROR OF scripts/run_tests.sh
    ----------------------------------------------
    That file is the POSIX branch's payload and this one is the Windows
    branch's. The two are held to step-for-step parity: the same six steps in
    the same order, the same failure conditions, the same exit semantics and
    the same message content. Only platform mechanics differ - a Scripts
    directory with .exe shims instead of bin, the Windows Python launcher
    among the interpreter candidates, a reparse-point test where POSIX tests
    for a symbolic link, the native-command wrapper the next section explains,
    and an explicit exit where the POSIX file can hand its process over to the
    runner. A divergence beyond that is a defect in whichever of the two
    drifted.

    HOW IT IS INVOKED, AND THE FOUR CONSEQUENCES
    --------------------------------------------
      1. The runtime is Windows PowerShell 5.1 Desktop (powershell.exe), not
         PowerShell 7 (pwsh). Only 5.1-compatible syntax appears below: no
         null-coalescing and no null-conditional operator, no ternary
         operator, no parallel ForEach-Object, and Join-Path is called one
         child at a time because 5.1 has no -AdditionalChildPath.
      2. The execution policy is the caller's business - it is supplied on
         that command line - so this script neither inspects nor alters it.
      3. -File makes this script's exit code powershell.exe's exit code, which
         the bat step reads to pass or fail the stage. An exit that is never
         reached returns 0, which is why every path below exits explicitly.
         See EXIT STATUS and THE POWERSHELL TRAP.
      4. Arguments after the -File path reach this script, so the $args
         forwarding in step 6 works from Jenkins, from a developer shell and
         from the Makefile alike.

    CONFIGURATION SURFACE: one environment variable, and no options of its own
    -------------------------------------------------------------------------
      $env:PYTHON  Optional. An interpreter to probe FIRST when locating the
                   pinned runtime in step 1, for example

                       $env:PYTHON = 'C:\Python314\python.exe'

                   It chooses which interpreter is tried first. It does NOT
                   relax the version pin: the identical exact-version check is
                   applied to it, and a non-matching value is reported and
                   rejected like any other candidate.

    Every argument this script receives is forwarded verbatim to the run-tests
    console script in step 6, and nowhere else. No option is defined,
    defaulted or interpreted here, and the script declares no param block on
    purpose, so nothing can intercept, validate or reorder what it was given.
    With no arguments - exactly how Jenkins invokes it - the behaviour is
    identical to invoking run-tests bare: the tag default from behave.ini and
    the --clean default from app/cli.py stay in force.

    THE SIX STEPS, IN ORDER
    -----------------------
      1. Locate a Python 3.14.6 interpreter, or fail loudly.
      2. Create .venv with that interpreter if it is missing; refuse a drifted
         or redirected one rather than replacing it.
      3. Install the pinned dependencies, then this project itself (editable).
      4. Run the pytest unit gate.
      5. Run the four per-package coverage gates, in order, first miss fails.
      6. Invoke the run-tests console script out of the environment's Scripts
         directory.

    EXIT STATUS: two different semantics, deliberately not blurred
    -------------------------------------------------------------
      * Bootstrap failures of this script - the working directory, steps 1 to
        3 and a missing entry point in step 6 - exit 1. app/cli.py never
        returns 1: its published set is 0, 2, 3, 4 and 5, with 1 left out on
        purpose. So a 1 from this stage always means the bootstrap failed and
        never that the suite reported something.
      * The unit gate in step 4 PROPAGATES pytest's own status. It is a real
        quality gate. pytest's exit code 5, "no tests collected", is forwarded
        unchanged too, because a unit gate that collects nothing is a real
        problem rather than a pass.
      * The coverage gates in step 5 PROPAGATE too, with the status of the
        first scope that misses its threshold. They gate this port's own test
        work and have no bearing on the scenario exit contract below - a
        coverage miss is never a scenario outcome.
      * The suite run in step 6 propagates the run-tests status UNALTERED. A
        test outcome never reaches it: pom.xml:25 sets
        <testFailureIgnore>true</testFailureIgnore> and all six publisher
        thresholds on Jenkins:15 are -1, so failing scenarios, errors,
        undefined or skipped steps, a browser that fails to start, an
        unrecognised browser value, a feature that fails to parse, a missing
        or malformed rerun manifest and a tag expression that selects nothing
        all exit 0 with the artifacts written. A non-zero status there means a
        usage error, a dead worker, an empty merge or a failed writer -
        exactly the classes that must reach Jenkins. Nothing in this file
        suppresses, swallows, remaps or adds to that status.

    THE TWO POWERSHELL TRAPS THIS FILE IS BUILT AROUND
    --------------------------------------------------
    FIRST: a non-zero native exit status is silent. $ErrorActionPreference =
    'Stop' governs PowerShell's own error records. It does NOT cause a NATIVE
    program that returns non-zero - python.exe, pip, pytest, the runner shim -
    to terminate the script. Left implicit, this file would run to the end and
    return 0 whatever those programs reported, and Jenkins would show a green
    stage over a failed unit gate or a failed report writer. So every native
    invocation below captures $LASTEXITCODE immediately and checks it
    explicitly, and the last statement of the file is an unconditional exit of
    the status step 6 produced. Nothing here assigns a success status,
    hard-codes one, or wraps a gate in a try/catch that could swallow one.

    SECOND, and it is the exact opposite failure: on Windows PowerShell 5.1
    anything a native program writes to STDERR is turned into a PowerShell
    error record, and under a global 'Stop' that record TERMINATES the script -
    before the $LASTEXITCODE capture on the following line ever runs. pip
    writes warnings to stderr, pytest writes to stderr, and app/cli.py
    deliberately writes tolerated diagnostics to stderr while returning 0 for
    every test outcome. So a global 'Stop' around native commands would fail
    this stage on output alone, for a run the exit contract requires to pass,
    and would do it inconsistently: PowerShell 7 changed that behaviour, so
    the bug would appear only on the 5.1 runtime CI actually uses.

    Invoke-NativeCommand below is the answer to both. It saves the current
    $ErrorActionPreference, sets 'Continue' for the duration of the one native
    call so stderr cannot terminate anything, invokes the program, captures
    $LASTEXITCODE on the very next statement with nothing in between, and
    restores the preference in a finally block. Every native invocation in
    steps 2 to 6 goes through it; PowerShell cmdlets keep the file-scope
    'Stop', which is where that preference belongs. A program that cannot be
    launched at all reports no status, and that is treated as a bootstrap
    failure rather than as a pass.

    Diagnostics go to true stderr through [Console]::Error.WriteLine, which
    bypasses PowerShell's error-record formatting and cannot perturb the exit
    code; progress goes to stdout.

    DELIBERATELY ABSENT
    -------------------
    No Maven invocation - there is no Maven build after the port and pom.xml is
    retained as historical reference only. No `make`: it is optional developer
    convenience, CI must never depend on it, and it is frequently absent from
    a Windows agent altogether - which is exactly why step 5 spells the four
    coverage gates out here instead of invoking `make coverage`. No report
    artifact path of any kind -
    app/utils/paths.py is their sole owner - and no emptying or inspection of
    the generated output directory, which is the runner's --clean, on by
    default. No --tags, --browser, --workers or any other option value. No git
    command: the pipeline performs its own checkout. No browser or driver
    installation: the browser is an operator prerequisite and
    webdriver-manager provisions the driver from inside the application at run
    time. Nothing creates, copies or overwrites configuration.properties,
    which is operator-supplied and git-ignored, with
    configuration.properties.example the committed template. No container, no
    production server and no HTTP surface: the Flask viewer is read-only,
    started separately, and never the route to this runner. No interactive
    construct anywhere, because this runs unattended on a CI agent.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1

    How Jenkins invokes it: no arguments, so every default the runner and
    behave.ini declare stays in force.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1 --workers 1 --dry-run

    Arguments are forwarded verbatim to the run-tests console script, which is
    the only thing that interprets them.

.NOTES
    Runtime : Windows PowerShell 5.1 Desktop or later.
    Exit    : 1 for a bootstrap failure of this script; pytest's own status for
              a unit-gate or coverage-gate failure; the run-tests status
              verbatim otherwise.
#>

$ErrorActionPreference = 'Stop'

# Keeps the CI log to the point: the environment builder and pip both emit
# progress records that an unattended log has no use for. It affects no exit
# status and hides no error - errors are written by the code below, explicitly.
$ProgressPreference = 'SilentlyContinue'

# The pinned interpreter version, exact. Its other two homes are
# .python-version (3.14.6) and pyproject.toml (requires-python = "==3.14.*");
# this literal is the third and last place it appears. It is compared with
# string equality on purpose, so that no other 3.14.x and no 3.15 can satisfy
# it - the support range is narrow by design, so that CI and development
# cannot drift apart.
$RequiredPythonVersion = '3.14.6'

# Accumulates one already-indented line per interpreter candidate probed in
# step 1, for the diagnostic printed when none of them matches.
$ProbeReport = New-Object 'System.Collections.Generic.List[string]'

# Set by Test-PinnedInterpreter when a candidate matches, so that the progress
# line can name the interpreter actually chosen rather than the name probed.
$ResolvedInterpreterPath = ''

# Set by Invoke-NativeCommand to the exit status of the program it just ran.
# It is a script-scope variable rather than a return value on purpose: a
# returned value would be written to the success output stream and would
# interleave with the program's own stdout, which CI reads.
$LastNativeExitCode = $null


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------

function Write-Diagnostic {
    <#
    .SYNOPSIS
        Write one or more lines to true stderr.
    .DESCRIPTION
        [Console]::Error.WriteLine goes straight to the process's standard
        error handle: no PowerShell error-record formatting, no dependence on
        $ErrorActionPreference and - unlike Write-Error under 'Stop' - no
        control-flow effect and no effect on the exit status, which every
        caller sets for itself on the following line.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string[]] $Line
    )

    foreach ($text in $Line) {
        [Console]::Error.WriteLine($text)
    }
}

function Invoke-NativeCommand {
    <#
    .SYNOPSIS
        Run one native program, safely, and record its exit status in
        $script:LastNativeExitCode.
    .DESCRIPTION
        The single place this file invokes a native program outside the
        tolerant probe in Get-InterpreterVersion, and it exists for the two
        traps the file header sets out.

        Windows PowerShell 5.1 turns whatever a native program writes to
        stderr into a PowerShell error record. Under the file-scope
        $ErrorActionPreference = 'Stop' that record is TERMINATING, so pip
        writing a warning, or app/cli.py writing one of the diagnostics it
        deliberately emits while returning 0, would end this script before the
        following line could read $LASTEXITCODE - failing a stage the exit
        contract requires to pass, and only on the 5.1 runtime, since
        PowerShell 7 changed the behaviour.

        So the preference is saved, set to 'Continue' for the duration of this
        one call, and restored in a finally block. The assignment is scoped to
        this function, which is what confines the relaxation to the native
        call; the explicit restore keeps that true even if this body is ever
        moved to file scope, and it survives a terminating error in the call.
        Cmdlets elsewhere in the file keep 'Stop', which is the preference
        that should govern them.

        $LASTEXITCODE is captured on the statement IMMEDIATELY after the
        invocation, with nothing in between that could replace it - no
        cmdlet, no pipeline, no progress message. It is cleared first, so a
        program that never launched is distinguishable from one that returned
        0 rather than inheriting a stale status from an earlier call; that
        case is a bootstrap failure and exits 1 here, because no status means
        nothing ran.

        The program's own stdout flows through this function's success output
        stream to the caller's, unchanged and unbuffered by anything here.
    .OUTPUTS
        None. The exit status is left in $script:LastNativeExitCode.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $false)]
        [AllowEmptyCollection()]
        [string[]] $ArgumentList = @()
    )

    $script:LastNativeExitCode = $null
    $previousErrorActionPreference = $ErrorActionPreference
    $launchFailureReason = ''

    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = $null
        & $FilePath @ArgumentList
        $script:LastNativeExitCode = $LASTEXITCODE
    }
    catch {
        $launchFailureReason = $_.Exception.Message
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($null -eq $script:LastNativeExitCode) {
        if ([string]::IsNullOrWhiteSpace($launchFailureReason)) {
            $launchFailureReason = 'it reported no exit status, so it did not run'
        }
        Write-Diagnostic @(
            'run_tests.ps1: a required program could not be run.',
            "  program : $FilePath",
            "  reason  : $launchFailureReason",
            '',
            'Nothing ran, so there is no status to report and this is a',
            'bootstrap failure rather than a test result. Check that the path',
            'above exists and is executable by the account running this stage.'
        )
        exit 1
    }
}

function Write-CoverageGateFailure {
    <#
    .SYNOPSIS
        Report a coverage gate in step 5 that missed its threshold.
    .DESCRIPTION
        Kept in one place so the four call sites stay readable and each one
        still shows its own scope and threshold literally. It only writes a
        diagnostic: the caller sets the exit status on the following line, so
        nothing here can alter it.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Scope,

        [Parameter(Mandatory = $true)]
        [int] $Minimum,

        [Parameter(Mandatory = $true)]
        [int] $Status
    )

    Write-Diagnostic @(
        "run_tests.ps1: the coverage gate for $Scope failed (pytest exit status $Status).",
        "  scope   : $Scope",
        "  minimum : $Minimum percent",
        '',
        'pytest''s own coverage report above names every line that is not',
        'covered. The remaining gates and the suite run were NOT started, and',
        'this stage fails with pytest''s status.',
        '',
        'Raise the coverage of that package with real tests. The threshold is',
        'part of the specification and is not the thing to change: lowering',
        'it, or dropping the scope, removes the only check that this port''s',
        'own code is exercised at all.'
    )
}

function Add-ProbeNote {
    <#
    .SYNOPSIS
        Append one line to the step 1 candidate report, already indented.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Note
    )

    $script:ProbeReport.Add('  ' + $Note)
}

function Get-InterpreterVersion {
    <#
    .SYNOPSIS
        Report "major.minor.micro" for a candidate interpreter, or $null when
        it cannot be run or says nothing.
    .DESCRIPTION
        Probing is deliberately tolerant, and probing is the ONLY thing in this
        file that is: a candidate which does not exist, is a stale shim, or is
        the Windows Store python.exe app-execution-alias stub has to count as
        "no match" so that step 1 can keep looking and then report everything
        it tried. So the candidate's own stderr is discarded, a non-zero status
        is ignored, and 'Stop' is relaxed for the duration of this one call -
        which also keeps Windows PowerShell 5.1 from turning a native command's
        stderr output into a terminating NativeCommandError. That relaxation is
        function-scoped: no gate is ever probed through here, and nothing else
        in this file relaxes anything.
    .OUTPUTS
        System.String. The version the candidate printed, or $null.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Command,

        [Parameter(Mandatory = $false)]
        [AllowEmptyCollection()]
        [string[]] $Argument = @()
    )

    $ErrorActionPreference = 'Continue'

    $probeExpression = 'import sys; print("%d.%d.%d" % sys.version_info[:3])'

    try {
        $output = & $Command @Argument '-c' $probeExpression 2>$null
    }
    catch {
        return $null
    }

    if ($null -eq $output) {
        return $null
    }

    $reported = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($reported)) {
        return $null
    }

    return $reported
}

function Test-PinnedInterpreter {
    <#
    .SYNOPSIS
        Return $true when a candidate resolves AND reports exactly the pinned
        version; otherwise record what it actually reported and return $false.
    .DESCRIPTION
        This one test is applied to every candidate including $env:PYTHON,
        which is what makes that override incapable of relaxing the pin. A near
        miss is reported and rejected, never used: silently falling back to
        another interpreter is the specific failure step 1 exists to prevent.
    .OUTPUTS
        System.Boolean. $true only on an exact version match.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Label,

        [Parameter(Mandatory = $true)]
        [string] $Command,

        [Parameter(Mandatory = $false)]
        [AllowEmptyCollection()]
        [string[]] $Argument = @()
    )

    # -ErrorAction SilentlyContinue here is resolution, not suppression: an
    # absent candidate is an expected outcome of probing and is reported as
    # "not found on PATH" on the next line.
    $resolved = Get-Command -Name $Command -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $resolved) {
        Add-ProbeNote ('{0}: not found on PATH' -f $Label)
        return $false
    }

    $source = $resolved.Source
    if ([string]::IsNullOrWhiteSpace($source)) {
        $source = $resolved.Name
    }

    $reported = Get-InterpreterVersion -Command $Command -Argument $Argument
    if ([string]::IsNullOrWhiteSpace($reported)) {
        Add-ProbeNote ('{0} ({1}): found, but reported no version' -f $Label, $source)
        return $false
    }

    if ($reported -eq $RequiredPythonVersion) {
        $script:ResolvedInterpreterPath = $source
        return $true
    }

    # Flattened for the report only. The comparison above is against the exact,
    # untouched string the candidate printed.
    Add-ProbeNote ('{0} ({1}): reported {2}' -f $Label, $source, ($reported -replace '\s+', ' '))
    return $false
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
#
# $PSScriptRoot is populated for a script run from a file, which is exactly how
# the pipeline runs this one. Resolve-Path collapses the '..' into a canonical
# filesystem path, and .ProviderPath - rather than .Path - is what strips any
# PowerShell provider prefix, so what is handed to the native commands below is
# an ordinary Windows path.
# --------------------------------------------------------------------------
$repositoryRoot = ''

if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    Write-Diagnostic @(
        'run_tests.ps1: cannot determine this script''s own location.',
        '$PSScriptRoot is empty, which means the script body was run without a',
        'file behind it - dot-sourced from a prompt, or piped into the host.',
        'Run it as a file, the way the pipeline does:',
        '    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1'
    )
    exit 1
}

try {
    $repositoryRoot = (Resolve-Path -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath '..')).ProviderPath
    Set-Location -LiteralPath $repositoryRoot
}
catch {
    Write-Diagnostic @(
        'run_tests.ps1: cannot change to the repository root.',
        "  script location : $PSScriptRoot",
        "  directory tried : $(Join-Path -Path $PSScriptRoot -ChildPath '..')",
        "  reason          : $($_.Exception.Message)",
        'Run this script from a complete checkout, as either',
        '    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1',
        'from the repository root or with any path that reaches it.'
    )
    exit 1
}

# Pre-flight: the three files step 3 installs from. Checking them here turns an
# obscure installer error into an actionable one, and confirms that the
# directory reached above really is the repository root.
$missingManifest = New-Object 'System.Collections.Generic.List[string]'
foreach ($manifest in @('pyproject.toml', 'requirements.txt', 'requirements-test.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path -Path $repositoryRoot -ChildPath $manifest) -PathType Leaf)) {
        $missingManifest.Add($manifest)
    }
}
if ($missingManifest.Count -gt 0) {
    Write-Diagnostic @(
        'run_tests.ps1: this does not look like a complete checkout.',
        "  working directory : $repositoryRoot",
        "  missing file(s)   : $($missingManifest -join ', ')",
        'All three are tracked in the repository and are required to install',
        'the pinned dependencies and this project itself. Restore the checkout',
        'and re-run.'
    )
    exit 1
}


# --------------------------------------------------------------------------
# Step 1 - locate a Python 3.14.6 interpreter, or fail loudly.
#
# Candidates in order: $env:PYTHON when it is set and non-empty, then the
# Windows Python launcher asked for the 3.14 series, then python3.14, python3,
# python. The first EXACT match wins. A silent fall back to whatever python
# resolves to is the specific failure this step exists to prevent, so a near
# miss is reported and rejected, never used.
#
# The launcher comes before the bare names because it is the canonical Windows
# mechanism for asking for a particular version and, unlike a bare name, cannot
# be shadowed by whatever happens to sit first on PATH. It selects a series and
# not a patch level, so the exact check still decides: a launcher that answers
# 3.14.4 is reported and rejected like any other near miss.
# --------------------------------------------------------------------------
$interpreterCandidate = New-Object 'System.Collections.Generic.List[hashtable]'

if (-not [string]::IsNullOrWhiteSpace($env:PYTHON)) {
    $interpreterCandidate.Add(@{
        Label    = '$env:PYTHON=' + $env:PYTHON
        Command  = $env:PYTHON
        Argument = @()
    })
}

$interpreterCandidate.Add(@{ Label = 'py -3.14';   Command = 'py';         Argument = @('-3.14') })
$interpreterCandidate.Add(@{ Label = 'python3.14'; Command = 'python3.14'; Argument = @() })
$interpreterCandidate.Add(@{ Label = 'python3';    Command = 'python3';    Argument = @() })
$interpreterCandidate.Add(@{ Label = 'python';     Command = 'python';     Argument = @() })

$pythonCommand = ''
$pythonArgument = @()
$pythonLabel = ''
$probedInvocation = New-Object 'System.Collections.Generic.List[string]'

foreach ($candidate in $interpreterCandidate) {
    # Skip an invocation already probed - the case in practice is $env:PYTHON
    # naming one of the bare candidates below it - so the report lists it once.
    # The key is the command plus its arguments, so `py -3.14` and a bare `py`
    # would still be distinct probes.
    $invocationKey = ($candidate.Command + ' ' + ($candidate.Argument -join ' ')).Trim()
    if ($probedInvocation -contains $invocationKey) {
        continue
    }
    $probedInvocation.Add($invocationKey)

    if (Test-PinnedInterpreter -Label $candidate.Label -Command $candidate.Command -Argument $candidate.Argument) {
        $pythonCommand = $candidate.Command
        $pythonArgument = $candidate.Argument
        $pythonLabel = $candidate.Label
        break
    }
}

if ([string]::IsNullOrWhiteSpace($pythonCommand)) {
    Write-Diagnostic @(
        "run_tests.ps1: no Python $RequiredPythonVersion interpreter found.",
        '',
        "This project is pinned to Python $RequiredPythonVersion exactly:",
        "  .python-version   $RequiredPythonVersion",
        '  pyproject.toml    requires-python = "==3.14.*"',
        '',
        'Interpreters probed, in order, and what each reported:'
    )
    Write-Diagnostic $ProbeReport.ToArray()
    Write-Diagnostic @(
        '',
        'No fallback to a different interpreter is performed. That is',
        'deliberate: falling back would let CI and development drift apart',
        'silently, which is the one thing the pin exists to prevent.',
        '',
        'To fix this, either',
        "  * install Python $RequiredPythonVersion and put it on PATH, after which",
        '    the Windows Python launcher answers "py -3.14" with it, or',
        '  * point the PYTHON environment variable at an absolute path to a',
        "    $RequiredPythonVersion interpreter, for example",
        '        $env:PYTHON = ''C:\Python314\python.exe''',
        '    The same exact-version check applies to it: it selects which',
        '    interpreter is tried first, it does not relax the pin.'
    )
    exit 1
}

Write-Output "run_tests.ps1: using $pythonLabel [$ResolvedInterpreterPath] ($RequiredPythonVersion)"



# --------------------------------------------------------------------------
# Step 2 - the virtual environment: create it when it is missing, refuse a
# drifted or redirected one.
#
# .venv at the repository root is the sanctioned location, and step 3 installs
# into it: .gitignore excludes both .venv/ and venv/, so creating it here
# leaves git status clean by design.
#
# THREE rejections, in this order, and the order matters:
#
#   a. A REDIRECTED .venv - a directory symbolic link, an NTFS junction, a
#      mounted-folder or any other reparse point. This is checked FIRST,
#      before the container and version tests, because those follow reparse
#      points: Test-Path -PathType Container is true for a junction to a
#      directory, so a .venv pointing at a shared, profile or machine-wide
#      3.14.6 environment would pass every later check and step 3 would then
#      pip-install into that external environment, modifying something outside
#      the checkout. The install has to stay repository-local, so the
#      redirection is refused instead. This mirrors the `-L` test in
#      scripts/run_tests.sh, where POSIX -d has the same defect.
#   b. A .venv that exists but is not a directory at all.
#   c. A .venv whose interpreter is not exactly the pinned version - precisely
#      the CI-versus-development drift the pin exists to prevent.
#
# None of the three deletes anything. Silently destroying a developer's
# environment, or following a link and destroying something outside the
# checkout, would be a destructive act nobody asked for: the operator is told
# what to remove and the run stops.
#
# Every path is built with Join-Path against the resolved repository root, one
# child at a time because Windows PowerShell 5.1 has no -AdditionalChildPath,
# and the Windows layout is a Scripts directory holding .exe shims rather than
# a POSIX bin directory.
# --------------------------------------------------------------------------
$venvDirectory = Join-Path -Path $repositoryRoot -ChildPath '.venv'
$venvScriptDirectory = Join-Path -Path $venvDirectory -ChildPath 'Scripts'
$venvPython = Join-Path -Path $venvScriptDirectory -ChildPath 'python.exe'
$venvRunTests = Join-Path -Path $venvScriptDirectory -ChildPath 'run-tests.exe'

# (a) -Force so a hidden entry is still seen, and SilentlyContinue so an
# absent .venv - the ordinary first-run case - is simply $null here. The
# attribute test is the gate because it covers every reparse-point kind at
# once; LinkType and Target only enrich the diagnostic.
$venvItem = Get-Item -LiteralPath $venvDirectory -Force -ErrorAction SilentlyContinue
if ($null -ne $venvItem -and
    (($venvItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq [System.IO.FileAttributes]::ReparsePoint)) {

    $venvRedirectionKind = 'reparse point'
    if ($venvItem.PSObject.Properties.Match('LinkType').Count -gt 0 -and
        -not [string]::IsNullOrWhiteSpace($venvItem.LinkType)) {
        $venvRedirectionKind = $venvItem.LinkType
    }

    $venvRedirectionTarget = 'not reported by this PowerShell version'
    if ($venvItem.PSObject.Properties.Match('Target').Count -gt 0 -and $null -ne $venvItem.Target) {
        $reportedTarget = ($venvItem.Target | Out-String).Trim()
        if (-not [string]::IsNullOrWhiteSpace($reportedTarget)) {
            $venvRedirectionTarget = $reportedTarget
        }
    }

    Write-Diagnostic @(
        'run_tests.ps1: .venv is a link rather than a real directory, which is not accepted.',
        "  path   : $venvDirectory",
        "  kind   : $venvRedirectionKind",
        "  target : $venvRedirectionTarget",
        '',
        'The two pip installs in step 3 install INTO this path, so it has to',
        'be a real directory inside the checkout. A link, junction or mounted',
        'folder would send both installs into whatever it points at - a',
        'shared, profile or machine-wide environment - and modify something',
        'outside the repository.',
        '',
        'Remove or rename that entry and re-run; this script will not delete',
        'it for you, and it deliberately does not follow it.'
    )
    exit 1
}

# (b)
if ((Test-Path -LiteralPath $venvDirectory) -and
    -not (Test-Path -LiteralPath $venvDirectory -PathType Container)) {
    Write-Diagnostic @(
        'run_tests.ps1: .venv exists but is not a directory.',
        "  path : $venvDirectory",
        'The virtual environment has to live there. Remove or rename that',
        'entry and re-run; this script will not delete it for you.'
    )
    exit 1
}

# (c)
if (Test-Path -LiteralPath $venvDirectory -PathType Container) {
    $venvVersion = Get-InterpreterVersion -Command $venvPython
    if ($venvVersion -ne $RequiredPythonVersion) {
        $reportedVenvVersion = $venvVersion
        if ([string]::IsNullOrWhiteSpace($reportedVenvVersion)) {
            $reportedVenvVersion = 'no version reported (missing or not runnable)'
        }
        Write-Diagnostic @(
            'run_tests.ps1: the existing .venv is not usable for this project.',
            "  required interpreter     : $RequiredPythonVersion",
            "  .venv\Scripts\python.exe : $reportedVenvVersion",
            '',
            'A virtual environment built on another interpreter is exactly the',
            'drift the version pin exists to prevent, so it is refused rather',
            'than used. It is deliberately not deleted for you:',
            '',
            '    Remove-Item -Recurse -Force .venv',
            '',
            'then re-run this script, which will rebuild it from the',
            "$RequiredPythonVersion interpreter located in step 1."
        )
        exit 1
    }
    Write-Output "run_tests.ps1: reusing .venv ($RequiredPythonVersion)"
}
else {
    Write-Output 'run_tests.ps1: creating .venv'
    $venvCreationArgument = @()
    $venvCreationArgument += $pythonArgument
    $venvCreationArgument += @('-m', 'venv', '.venv')
    Invoke-NativeCommand -FilePath $pythonCommand -ArgumentList $venvCreationArgument
    $venvStatus = $script:LastNativeExitCode
    if ($venvStatus -ne 0) {
        Write-Diagnostic @(
            'run_tests.ps1: failed to create the .venv virtual environment.',
            "  interpreter : $ResolvedInterpreterPath",
            "  exit status : $venvStatus",
            '',
            'The error printed above this message comes from the interpreter',
            'itself and names the cause. The usual ones on a CI agent are a',
            'workspace the account cannot write to, a path already at the',
            'Windows length limit, or an interpreter installed without the',
            'venv and ensurepip components.'
        )
        exit 1
    }

    # A virtual environment that was created but carries no runnable
    # interpreter would fail later with a far less obvious error, so it is
    # caught here.
    $venvVersion = Get-InterpreterVersion -Command $venvPython
    if ($venvVersion -ne $RequiredPythonVersion) {
        $reportedVenvVersion = $venvVersion
        if ([string]::IsNullOrWhiteSpace($reportedVenvVersion)) {
            $reportedVenvVersion = 'no version reported (missing or not runnable)'
        }
        Write-Diagnostic @(
            'run_tests.ps1: .venv was created but has no usable interpreter.',
            "  required interpreter     : $RequiredPythonVersion",
            "  .venv\Scripts\python.exe : $reportedVenvVersion",
            '',
            'Creation reported success, so this points at the environment',
            'rather than at this script. Remove .venv with',
            '"Remove-Item -Recurse -Force .venv", check the interpreter above,',
            'and re-run.'
        )
        exit 1
    }
}

# The explicit existence check for the interpreter every command below runs
# through. Step 2 proved it reports the pinned version, so this guards the
# half-removed environment - a Scripts directory emptied between the two - and
# names it here instead of letting it surface as an obscure installer error.
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Diagnostic @(
        'run_tests.ps1: the virtual environment interpreter has gone missing.',
        "  expected : $venvPython",
        'It was present a moment ago, so something outside this script removed',
        'it mid-run. Remove the environment with',
        '"Remove-Item -Recurse -Force .venv" and re-run.'
    )
    exit 1
}


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
# The second install is the project distribution, and step 6 cannot run
# without it: installing -r manifests installs DEPENDENCIES ONLY, while the
# run-tests console script declared in pyproject.toml under
#     [project.scripts] run-tests = "app.cli:run_tests"
# is materialised into the environment's Scripts directory only when the
# distribution itself is installed. Editable (-e .) specifically, never a
# plain '.': CI has to execute the code in the checked-out workspace, whereas
# a non-editable install copies a snapshot into site-packages and could then
# run stale code against a fresh checkout. This install enables the specified
# behaviour; it does not extend it.
#
# pip is non-interactive by default. --quiet and --disable-pip-version-check
# keep the CI log to the point. The environment's own python is used with
# -m pip rather than the pip shim beside it, so the interpreter running the
# installer is unambiguously the one just verified.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: installing pinned dependencies'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
    '-r', 'requirements.txt', '-r', 'requirements-test.txt'
)
$dependencyStatus = $script:LastNativeExitCode
if ($dependencyStatus -ne 0) {
    Write-Diagnostic @(
        'run_tests.ps1: installing the pinned dependencies failed.',
        '  manifests   : requirements.txt, requirements-test.txt',
        "  exit status : $dependencyStatus",
        '',
        'pip''s own output above names the distribution that could not be',
        'installed. Every version in both manifests is exact-pinned, so the',
        'usual causes are an unreachable package index or a pin with no',
        'distribution for this platform.'
    )
    exit 1
}

Write-Output 'run_tests.ps1: installing the project (editable)'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-e', '.'
)
$projectStatus = $script:LastNativeExitCode
if ($projectStatus -ne 0) {
    Write-Diagnostic @(
        'run_tests.ps1: installing this project in editable mode failed.',
        "  project     : . (pyproject.toml in $repositoryRoot)",
        "  exit status : $projectStatus",
        '',
        'This install is what creates the run-tests console script in the',
        'environment''s Scripts directory, so the run cannot proceed without',
        'it. A failure here usually means pyproject.toml''s [build-system]',
        'section is missing or misdeclares its build backend; that file is',
        'maintained separately from this script.'
    )
    exit 1
}


# --------------------------------------------------------------------------
# Step 4 - the unit gate: this port's own test suite, and a real gate.
#
# Bare on purpose. pytest.ini owns test selection - testpaths = tests - which
# is what keeps the behave step definitions under features/steps/ out of the
# unit suite: they are glue matched by phrase at scenario run time and define
# no pytest tests. So no path argument is passed here, and no coverage flag
# either - measurement is step 5's job, and keeping it out of this run means a
# coverage miss and a test failure are reported as the separate problems they
# are.
#
# The status PROPAGATES, unlike step 6's. pytest's exit code 5, "no tests
# collected", propagates as well: a unit gate that collects nothing has not
# passed. On failure neither the coverage gates nor the suite run is started.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: running the unit gate'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @('-m', 'pytest')
$pytestStatus = $script:LastNativeExitCode
if ($pytestStatus -ne 0) {
    Write-Diagnostic @(
        "run_tests.ps1: the unit gate failed (pytest exit status $pytestStatus).",
        'The test suite was NOT started, and this stage fails with pytest''s own',
        'status. pytest''s output above identifies the failing tests; exit',
        'status 5 means it collected no tests at all, which is a failure of the',
        'gate rather than a pass.'
    )
    exit $pytestStatus
}


# --------------------------------------------------------------------------
# Step 5 - the coverage gates: four scopes, in order, first miss fails.
#
# A single --cov-fail-under cannot express four different per-package
# thresholds, so pytest runs once per scope and each run measures and gates
# only its own package. The four scopes and their minimums are exactly
#   app/utils 90, app/pages 85, app/automation 80, app/reporting 80
# and the Makefile's `coverage` target is their canonical declaration. They
# are spelled out again here rather than reached through `make coverage`
# because CI must not depend on make being installed - and on a Windows agent
# it usually is not installed at all - so the developer command and the CI
# command are the same four commands, and a threshold that ever changes
# changes in all three files together.
#
# This is where the thresholds are actually ENFORCED on a CI agent. Without
# this step the pipeline would run the suite with the gates declared but never
# applied, which is indistinguishable from having no gates at all.
#
# The first non-zero status is propagated and nothing after it runs, so the
# stage fails on the first scope that misses and the suite run is not started.
# These gates cover this port's own test code only; they say nothing about a
# scenario outcome, which keeps them clear of the exit contract step 6 carries.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: running the coverage gates'

Write-Output 'run_tests.ps1: coverage gate 1 of 4 - app/utils, minimum 90 percent'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '--cov=app/utils', '--cov-fail-under=90'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/utils' -Minimum 90 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 2 of 4 - app/pages, minimum 85 percent'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '--cov=app/pages', '--cov-fail-under=85'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/pages' -Minimum 85 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 3 of 4 - app/automation, minimum 80 percent'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '--cov=app/automation', '--cov-fail-under=80'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/automation' -Minimum 80 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 4 of 4 - app/reporting, minimum 80 percent'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '--cov=app/reporting', '--cov-fail-under=80'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/reporting' -Minimum 80 -Status $coverageStatus
    exit $coverageStatus
}


# --------------------------------------------------------------------------
# Step 6 - the suite run, through the one sanctioned entry point.
#
# The run-tests console script from the virtual environment's Scripts
# directory, never the Flask CLI and never python -m: pyproject.toml declares
# this entry point and every caller - this script, scripts/run_tests.sh, the
# Makefile and the README - reaches the runner through it.
#
# Its absence is a bootstrap failure rather than a test outcome, so it is
# checked first and reported as such.
# --------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $venvRunTests -PathType Leaf)) {
    Write-Diagnostic @(
        'run_tests.ps1: .venv\Scripts\run-tests.exe is missing.',
        "  expected : $venvRunTests",
        '',
        'That console script is declared in pyproject.toml as',
        '    [project.scripts] run-tests = "app.cli:run_tests"',
        'and is created by the editable install in step 3, which reported',
        'success. Check that declaration and the packaging configuration around',
        'it, then re-run.',
        'This is a bootstrap failure, not a test result.'
    )
    exit 1
}

# The status of this invocation is the status of the stage, and the next line
# propagates it verbatim - deliberately, and as the whole point of the file.
# Invoke-NativeCommand runs the console script in the foreground of this
# process and captures its exit code on the statement immediately following
# the call, with nothing in between that could replace the value being
# returned; the arguments are forwarded exactly as received, none added,
# removed or defaulted here. It also holds the native-command relaxation the
# file header explains, without which app/cli.py writing one of the
# diagnostics it deliberately emits on stderr while returning 0 could
# terminate this script on a Windows PowerShell 5.1 agent and fail a stage the
# exit contract requires to pass.
#
# $args is forwarded whatever its length: with no arguments, which is how
# Jenkins invokes this file, the runner is invoked bare and every default
# stays in force. Nothing may follow these two lines.
Invoke-NativeCommand -FilePath $venvRunTests -ArgumentList $args
exit $script:LastNativeExitCode
