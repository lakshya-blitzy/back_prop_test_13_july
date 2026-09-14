#Requires -Version 5.1
<#
.SYNOPSIS
    Windows entry point for the Testinium-QA test run: bootstrap the pinned
    Python 3.14.6 environment, gate on this port's own test suite, then run
    the Gherkin suite.

.DESCRIPTION
    The payload of the Jenkins pipeline's Windows branch, and the behavioural
    mirror of scripts/run_tests.sh: the same five steps in the same order,
    the same failure conditions, the same exit semantics and the same message
    content. Only platform mechanics differ - a Scripts directory with .exe
    shims instead of bin, the Windows Python launcher among the interpreter
    candidates, a reparse-point test where POSIX tests for a symbolic link,
    the native-command wrapper described below, and an explicit exit where
    the POSIX file can hand its process over to the runner. A divergence
    beyond that is a defect in whichever of the two drifted. This file
    performs no platform detection of its own and never delegates to its
    POSIX counterpart.

    HOW IT IS INVOKED, AND THE CONSEQUENCES
    ---------------------------------------
    The pipeline runs it as

        powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1

        bat "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
             -NoLogo -NoProfile -NonInteractive
             -ExecutionPolicy Bypass -File scripts\run_tests.ps1"

    on one line. The interpreter is named by its absolute System32 path so
    that PATH cannot decide which powershell runs, and the three switches keep
    the Jenkins account's profile and the all-user profiles from executing
    ahead of this script on an unattended agent. None of that is this file's
    behaviour - it is the pipeline's, recorded here because this file is what
    that command runs.

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

    ONE PARITY ITEM THAT NEEDS NO CODE HERE, recorded so its absence is not
    read as an omission: the POSIX script pins PATH to a fixed list of system
    directories before anything runs, because every check it makes is built
    out of external utilities - find, id, ls, awk, cksum, tr, cut, env, sed -
    and an inherited PATH would let a planted one of those decide its own
    verdict. This script runs NO external program other than the interpreters
    it has trust-checked, the Python launcher it interrogates and the
    console script it has trust-checked and bound: every other operation is a
    PowerShell cmdlet or a .NET call, resolved from the runtime and not from
    PATH. There is therefore nothing here for a PATH entry to substitute, and
    no PATH manipulation below.

    HOW IT IS INVOKED, AND THE FOUR CONSEQUENCES
    --------------------------------------------
      1. The runtime is Windows PowerShell 5.1 Desktop (powershell.exe), not
         PowerShell 7 (pwsh). Only 5.1-compatible syntax appears below: no
         null-coalescing and no null-conditional operator, no ternary
         operator, no parallel ForEach-Object, and Join-Path is called one
         child at a time because 5.1 has no -AdditionalChildPath.
      2. The execution policy is the caller's business - it is supplied on
         that command line - so this script neither inspects nor alters it.
      3. -File makes this script's exit code powershell.exe's exit code,
         which the calling step reads to pass or fail the stage. An exit that
         is never reached returns 0, which is why every path below exits
         explicitly. See EXIT STATUS and THE TWO POWERSHELL TRAPS.
      4. Arguments after the -File path reach this script, so the $args
         forwarding in step 5 works from Jenkins and from a developer shell
         alike.

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
    console script in step 5, and nowhere else. No option is defined,
    defaulted or interpreted here, and the script declares no param block on
    purpose, so nothing can intercept, validate or reorder what it was given.
    With no arguments - exactly how Jenkins invokes it - the behaviour is
    identical to invoking run-tests bare: the tag default from behave.ini and
    the --clean default from app/cli.py stay in force.

    THE FIVE STEPS, IN ORDER
    ------------------------
      1. Locate a Python 3.14.6 interpreter, or fail loudly.
      2. Create .venv with that interpreter if it is missing; refuse a drifted
         or redirected one rather than replacing it.
      3. Install the pinned dependencies, then this project itself (editable).
      4. The quality gate: pytest, bare first and then once per coverage
         scope. The coverage gates are part of this step, not a step of their
         own.
      5. Invoke the run-tests console script out of the environment's Scripts
         directory.

    EXIT STATUS: two different semantics, deliberately not blurred
    -------------------------------------------------------------
      * Bootstrap failures of this script - the working directory, steps 1 to
        3 and a missing entry point in step 5 - exit 1. app/cli.py publishes 0
        for every test outcome and exactly three non-zero classes: 2 a usage
        error, 3 a dead worker, 4 a failure of this port's own artifact
        production. 1 is deliberately not among them, so a 1 from this stage
        always means the bootstrap failed and never that the suite reported
        something.
      * The quality gate in step 4 PROPAGATES pytest's own status, including
        pytest's exit code 5, "no tests collected", and the status of the
        first coverage scope that misses its threshold. A coverage miss gates
        this port's own test work and is never a scenario outcome.
      * Step 5 propagates the run-tests status UNALTERED. A test outcome never
        reaches it: pom.xml:25 sets
        <testFailureIgnore>true</testFailureIgnore> and the publisher
        thresholds on Jenkins:15 are -1, so failing scenarios, errors,
        undefined or skipped steps, a browser that fails to start, an
        unrecognised browser value, a feature that fails to parse, a missing
        or malformed rerun manifest and a tag expression that selects nothing
        all exit 0 with the artifacts written. Nothing in this file
        suppresses, swallows, remaps or adds to that status.

    THE TWO POWERSHELL TRAPS THIS FILE IS BUILT AROUND
    --------------------------------------------------
    FIRST: a non-zero native exit status is silent. $ErrorActionPreference =
    'Stop' governs PowerShell's own error records, not a NATIVE program -
    python.exe, pip, pytest, the runner shim - that returns non-zero. Left
    implicit, this file would run to the end and return 0 whatever they
    reported, showing a green stage over a failed gate.

    SECOND, the exact opposite failure: on Windows PowerShell 5.1 anything a
    native program writes to STDERR becomes a PowerShell error record, and
    under a global 'Stop' that record TERMINATES the script - before the
    $LASTEXITCODE capture on the following line ever runs. pip warns on
    stderr, pytest writes to stderr, and app/cli.py deliberately writes
    tolerated diagnostics there while returning 0, so a global 'Stop' around
    native commands would fail a run the exit contract requires to pass -
    and only on the 5.1 runtime, since PowerShell 7 changed that behaviour.

    Invoke-NativeCommand answers both, and every native invocation in steps 2
    to 5 goes through it; PowerShell cmdlets keep the file-scope 'Stop', which
    is where that preference belongs. Diagnostics go to true stderr through
    [Console]::Error.WriteLine, which bypasses error-record formatting and
    cannot perturb the exit code; progress goes to stdout.

.EXAMPLE
    %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts\run_tests.ps1

    How Jenkins invokes it: no arguments, so every default the runner and
    behave.ini declare stays in force. The absolute executable path and the
    three switches are the pipeline's invocation hardening, not requirements
    of this script - run by hand, the shorter form below behaves identically.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1 --workers 1 --dry-run

    Arguments are forwarded verbatim to the run-tests console script, which is
    the only thing that interprets them.

.NOTES
    Runtime : Windows PowerShell 5.1 (powershell.exe), per the #Requires
              directive above - the host the pipeline invokes and the syntax
              level this file is written to.
    Exit    : 1 for a bootstrap failure of this script; pytest's own status for
              a quality-gate failure; the run-tests status verbatim otherwise.
#>

$ErrorActionPreference = 'Stop'

# Suppresses the progress records the environment builder and pip emit, which
# an unattended log has no use for. It affects no exit status and hides no
# error - errors are written by the code below, explicitly.
$ProgressPreference = 'SilentlyContinue'

# Compared with string equality on purpose: no other 3.14.x and no 3.15 can
# satisfy the pin, so CI and development cannot drift apart. Its other two
# homes are .python-version and pyproject.toml's requires-python.
$RequiredPythonVersion = '3.14.6'

# One already-indented line per candidate probed, for the step 1 failure
# diagnostic.
$ProbeReport = New-Object 'System.Collections.Generic.List[string]'

# Set by Test-PinnedInterpreter when a candidate matches, so the progress line
# can name the interpreter chosen rather than the name probed.
$ResolvedInterpreterPath = ''

# The arguments that belong to that resolved path, set by the same function.
# Every candidate resolves to an interpreter file which needs none, the Python
# launcher included - its series argument selects a registered interpreter, and
# that interpreter is what gets authenticated and then executed directly.
$ResolvedInterpreterArgument = @()

# Set by Invoke-NativeCommand to the exit status of the program it just ran.
# It is a script-scope variable rather than a return value on purpose: a
# returned value would be written to the success output stream and would
# interleave with the program's own stdout, which CI reads.
$LastNativeExitCode = $null

# How many characters of any one environment- or program-derived value reach
# the log. See ConvertTo-SafeText.
$DiagnosticLimit = 200

# How much of what a probed program printed is stored at all, mirroring the
# `head -c 4096` in scripts/run_tests.sh. See Get-InterpreterVersion.
$ProbeOutputLimit = 4096

# Set once step 2 has validated the virtual environment, to the identity tokens
# Assert-VenvIdentity re-asserts before every use of it. The counterparts of
# VENV_DIR_TOKEN and VENV_PYTHON_TOKEN in scripts/run_tests.sh.
$venvDirectoryToken = ''
$venvPythonToken = ''

# The identity token of the run-tests console script, empty until step 3's
# editable install creates that file and binds it. Assert-VenvIdentity
# re-asserts it whenever it is set, which is every call from that point to the
# invocation in step 6. The counterpart of VENV_SHIM_TOKEN in
# scripts/run_tests.sh.
$venvRunTestsToken = ''

# Set by Test-TrustedProgram to the reason it refused a program, already
# rendered for printing. Step 1 folds it into the candidate report; step 2
# prints it as the reason for a bootstrap failure. The counterpart of
# TRUST_REASON in scripts/run_tests.sh.
$TrustReason = ''

# The ONLY identities that may own a program this script runs, or hold
# write access to it or to a directory on the way to it. Everyone else is
# refused: an allowlist, because the question a trust gate has to answer is
# "could anything other than the system or this account have put this program
# here?", and a DENYLIST of well-known names answers a different and much
# weaker question - it passes an ALLOW entry for any account not thought of.
#
# Compared as SECURITY IDENTIFIERS and never as names. A name is localized
# (Administrators is Administratoren on a German agent), is renameable, and is
# resolved through a name-lookup path that a refusal must not depend on; a SID
# is none of those. The three well-known values are SYSTEM, the local
# Administrators group and NT SERVICE\TrustedInstaller - the account Windows
# Update and the servicing stack own system binaries as - and the invoking
# account's own SID is added to them at run time, which is the counterpart of
# the POSIX "owned by root or by the invoking user" test.
$TrustedPrincipalSid = @(
    'S-1-5-18',
    'S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
)


# Helpers.

function ConvertTo-SafeText {
    <#
    .SYNOPSIS
        Render one value for a human to read, bounded and control-free.
    .DESCRIPTION
        EVERY value this script prints that came from the environment or from a
        program it ran - $env:PYTHON, a resolved path, the version string a
        candidate printed, a reported link target, an exception message, a
        dropped variable name, the repository root, this script's own location -
        goes through here first, because a raw value can carry a newline and
        forge what looks like a separate record of this script's own, or carry
        an ANSI escape and repaint the console.

        Every character outside printable ASCII becomes a question mark, the
        result is bounded to $DiagnosticLimit characters, and a value that was
        longer says so.

        Diagnostics only. A COMPARISON is never made against the rendered form -
        the version test in Test-PinnedInterpreter is against the exact string
        the candidate printed - so a crafted value cannot be rendered into a
        passing shape.
    .OUTPUTS
        System.String. Printable ASCII, at most $DiagnosticLimit characters plus
        the truncation marker.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $false, Position = 0)]
        [AllowEmptyString()]
        [AllowNull()]
        $Value
    )

    $text = ''
    if ($null -ne $Value) {
        $text = [string]$Value
    }
    $text = [regex]::Replace($text, '[^\x20-\x7E]', '?')
    if ($text.Length -gt $DiagnosticLimit) {
        return $text.Substring(0, $DiagnosticLimit) + '[truncated]'
    }
    return $text
}

function Write-Diagnostic {
    <#
    .SYNOPSIS
        Write one or more lines to true stderr.
    .DESCRIPTION
        [Console]::Error.WriteLine goes straight to the process's standard
        error handle: no error-record formatting, no dependence on
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
        The answer to the two traps the file header sets out, and the single
        place this file invokes a native program outside the tolerant probe
        in Get-InterpreterVersion.

        'Continue' is set for this one call so a native stderr write cannot
        terminate the script, and restored in a finally block, which confines
        the relaxation to the call and survives a terminating error in it.
        $LASTEXITCODE is captured on the statement IMMEDIATELY after the
        invocation and cleared first, so a program that never launched is
        distinguishable from one that returned 0: no status means nothing ran,
        which is a bootstrap failure and exits 1.
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
            "  program : $(ConvertTo-SafeText $FilePath)",
            "  reason  : $(ConvertTo-SafeText $launchFailureReason)",
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
        Report a coverage scope of step 4 that missed its threshold.
    .DESCRIPTION
        It only writes a diagnostic: the caller sets the exit status on the
        following line, so nothing here can alter it.
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
        Append one already-indented line to the candidate report.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Note
    )

    $script:ProbeReport.Add('  ' + $Note)
}

function Get-PrincipalSid {
    <#
    .SYNOPSIS
        The security identifier of one ACL principal, or $null when it cannot
        be determined.
    .DESCRIPTION
        Get-Acl reports an owner and an identity reference as NTAccount names
        by default, and every comparison this script makes has to happen on the
        SID behind that name. Translate does that lookup; a reference that is
        already a SecurityIdentifier is returned as it stands.

        A failure returns $null rather than a name, and every caller treats
        $null as untrusted - an identity that cannot be resolved is an identity
        this script cannot vouch for, and a trust gate that let it through
        because the lookup broke would be a gate that opens under pressure.
    .OUTPUTS
        System.String, or $null.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        $Identity
    )

    if ($null -eq $Identity) {
        return $null
    }
    if ($Identity -is [System.Security.Principal.SecurityIdentifier]) {
        return $Identity.Value
    }
    try {
        return $Identity.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        return $null
    }
}

function Test-AclIsTrusted {
    <#
    .SYNOPSIS
        Return $true when nothing untrusted can write to one filesystem entry.
    .DESCRIPTION
        The Windows half of the trust gate, and the mirror of the POSIX mode
        and ownership tests in scripts/run_tests.sh: Windows has no permission
        bits, so the same question - could anyone other than this account or the
        system have put this program here? - is asked of the entry's access
        control list instead.

        Two refusals, both decided against the SID ALLOWLIST above. An owner
        whose SID is not in it; and any ALLOW entry granting a write-class right
        - Write, Modify, FullControl, WriteData, AppendData, WriteAttributes,
        ChangePermissions or TakeOwnership - to a principal whose SID is not in
        it either. An identity whose SID cannot be resolved at all is refused
        as well: fail closed.

        INHERIT-ONLY entries are skipped, and that is correctness rather than
        leniency. A stock Windows volume root carries ACEs marked
        ObjectInherit + ContainerInherit + InheritOnly - "Authenticated Users:
        Modify" on C:\ is one - which grant nothing on the entry itself and
        apply only to children created under it. Judging them here would refuse
        every interpreter on a default installation, which is a broken gate
        rather than a strict one.

        The reason is left in $script:TrustReason, already rendered, so the
        caller can fold it into the step 1 report or print it as a bootstrap
        failure.
    .OUTPUTS
        System.Boolean.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $Kind
    )

    $acl = $null
    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    }
    catch {
        $script:TrustReason = 'rejected, its access control list could not be read: ' + (ConvertTo-SafeText $_.Exception.Message)
        return $false
    }

    # The trusted set for this call: the three well-known SIDs plus the SID of
    # the account running this script. The invoking account is resolved through
    # the current Windows identity rather than by name comparison, and a
    # failure to resolve it simply leaves the set at the three well-known
    # values - narrower, never wider.
    $trustedSid = New-Object 'System.Collections.Generic.List[string]'
    foreach ($wellKnownSid in $TrustedPrincipalSid) {
        $trustedSid.Add($wellKnownSid)
    }
    try {
        $invokerSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        if (-not [string]::IsNullOrWhiteSpace($invokerSid)) {
            $trustedSid.Add($invokerSid)
        }
    }
    catch {
        $script:TrustReason = 'rejected, the identity of the invoking account could not be determined: ' + (ConvertTo-SafeText $_.Exception.Message)
        return $false
    }

    # The owner, read as a SID directly from the descriptor so that no name
    # lookup stands between the file and the comparison.
    $owner = $null
    try {
        $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
    }
    catch {
        $script:TrustReason = 'rejected, the owner of the ' + $Kind + ' ' + (ConvertTo-SafeText $Path) + ' could not be read: ' + (ConvertTo-SafeText $_.Exception.Message)
        return $false
    }
    $ownerSid = Get-PrincipalSid -Identity $owner
    if ([string]::IsNullOrWhiteSpace($ownerSid)) {
        $script:TrustReason = 'rejected, the owner of the ' + $Kind + ' ' + (ConvertTo-SafeText $Path) + ' has no resolvable identity'
        return $false
    }
    if (-not ($trustedSid -contains $ownerSid)) {
        $script:TrustReason = 'rejected, the ' + $Kind + ' ' + (ConvertTo-SafeText $Path) + ' is owned by ' + (ConvertTo-SafeText $ownerSid)
        return $false
    }

    $writeMask =
        [System.Security.AccessControl.FileSystemRights]::Write -bor
        [System.Security.AccessControl.FileSystemRights]::Modify -bor
        [System.Security.AccessControl.FileSystemRights]::FullControl -bor
        [System.Security.AccessControl.FileSystemRights]::WriteData -bor
        [System.Security.AccessControl.FileSystemRights]::AppendData -bor
        [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [System.Security.AccessControl.FileSystemRights]::TakeOwnership

    # The one difference between judging an entry as the PROGRAM and judging it
    # as a DIRECTORY on the way to that program. On a file every right above
    # alters its content or its security. On a directory, AppendData alone is
    # the right to create a NEW subdirectory, which cannot replace an entry
    # that is already there - WriteData (create files), Delete,
    # DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership, Write,
    # Modify and FullControl all can, and all of them stay in the mask. It is
    # cleared for a directory because a stock Windows volume root grants
    # exactly that single right to Authenticated Users, not inherited, so
    # judging it would refuse every interpreter installed anywhere under C:\ -
    # and a gate that refuses everything is not a stricter gate, it is a
    # broken one.
    $writeMaskValue = [int]$writeMask
    if ($Kind -ne 'program') {
        $writeMaskValue = $writeMaskValue -band (-bnot [int][System.Security.AccessControl.FileSystemRights]::AppendData)
    }

    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        if (([int]$rule.FileSystemRights -band $writeMaskValue) -eq 0) {
            continue
        }
        # Inherit-only: grants nothing on this entry. See the note above.
        if (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -eq [System.Security.AccessControl.PropagationFlags]::InheritOnly) {
            continue
        }
        $identitySid = Get-PrincipalSid -Identity $rule.IdentityReference
        if ([string]::IsNullOrWhiteSpace($identitySid)) {
            $script:TrustReason = 'rejected, the ' + $Kind + ' ' + (ConvertTo-SafeText $Path) + ' grants write access to ' + (ConvertTo-SafeText $rule.IdentityReference) + ', whose identity cannot be resolved'
            return $false
        }
        if (-not ($trustedSid -contains $identitySid)) {
            $script:TrustReason = 'rejected, the ' + $Kind + ' ' + (ConvertTo-SafeText $Path) + ' grants write access to ' + (ConvertTo-SafeText $identitySid)
            return $false
        }
    }

    return $true
}

function Test-TrustedProgram {
    <#
    .SYNOPSIS
        Return $true when the absolute path in -Path is a program this script is
        willing to EXECUTE.
    .DESCRIPTION
        Every candidate goes through here BEFORE it is run, which is the whole
        point: probing an interpreter runs it, so a candidate anyone on the
        machine could have written is a candidate that must never be probed at
        all. The mirror of interpreter_is_trusted in scripts/run_tests.sh.

        Three tests: the path is a real file rather than a link, its access
        control list admits no untrusted writer, and - unless -SkipAncestors is
        given - neither does that of any directory on the way to it.

        -SkipAncestors is used for exactly one interpreter, the repository-local
        .venv one, whose trust comes from the object-identity binding in step 2
        instead: a checkout legitimately sits under a directory the machine at
        large can write to, and walking there would refuse every environment in
        the workspace rather than make anything safer.

        One deliberate divergence from the POSIX script, which follows a link
        and judges its target: here a reparse point is refused outright. The
        case that actually arises on Windows is the Store app-execution-alias
        stub named python.exe, which is not an interpreter, and refusing it
        without running it is exactly the intent.
    .OUTPUTS
        System.Boolean. The reason for a $false is left in
        $script:TrustReason.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $false)]
        [switch] $SkipAncestors
    )

    $script:TrustReason = ''

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $script:TrustReason = 'rejected, not a regular file'
        return $false
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq [System.IO.FileAttributes]::ReparsePoint) {
        $script:TrustReason = 'rejected, it is a link rather than a real file'
        return $false
    }
    if (-not (Test-AclIsTrusted -Path $Path -Kind 'program')) {
        return $false
    }
    if ($SkipAncestors) {
        return $true
    }

    $ancestor = Split-Path -Path $item.FullName -Parent
    while (-not [string]::IsNullOrWhiteSpace($ancestor)) {
        if (-not (Test-AclIsTrusted -Path $ancestor -Kind 'directory')) {
            return $false
        }
        $parent = Split-Path -Path $ancestor -Parent
        if ($parent -eq $ancestor) {
            break
        }
        $ancestor = $parent
    }

    return $true
}

function Assert-TrustedVenvInterpreter {
    <#
    .SYNOPSIS
        Stop unless the environment's own interpreter is one this script is
        willing to run.
    .DESCRIPTION
        The same file and access-control tests step 1 applies to a candidate on
        PATH, minus the ancestor walk for the reason Test-TrustedProgram gives,
        applied before the first time the environment's python.exe is executed.
        The mirror of require_trusted_venv_interpreter in
        scripts/run_tests.sh.

        A refusal here is a bootstrap failure and exits 1: the environment is
        the one thing every later command runs through, and the alternative to
        stopping is running whatever has been put there.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if (Test-TrustedProgram -Path $Path -SkipAncestors) {
        return
    }

    Write-Diagnostic @(
        'run_tests.ps1: the .venv interpreter is not one this script will run.',
        "  path   : $(ConvertTo-SafeText $Path)",
        "  reason : $script:TrustReason",
        '',
        'Every command from here on - both pip installs, the unit gate, the',
        'four coverage gates and the suite run - goes through that one',
        'interpreter, so it is refused rather than used.',
        '',
        'It is deliberately not deleted for you:',
        '',
        '    Remove-Item -Recurse -Force .venv',
        '',
        'then re-run this script, which will rebuild it from the',
        "$RequiredPythonVersion interpreter located in step 1."
    )
    exit 1
}

function Get-IdentityToken {
    <#
    .SYNOPSIS
        An identity token for one filesystem object, or $null when it cannot be
        read.
    .DESCRIPTION
        The Windows counterpart of the token identity_token yields in
        scripts/run_tests.sh. Windows exposes no stable inode through
        PowerShell 5.1, so a DIRECTORY is identified by what it does expose and
        what a replacement cannot preserve: whether the object is a reparse
        point, its creation time in UTC ticks and its fully resolved name. A
        directory swapped for a junction changes at least one of them. Its size
        and modification time are deliberately absent, because entries appear
        beneath it while the two pip installs in step 3 run.

        A FILE adds its length, its last write time in UTC ticks and - the part
        that makes the token mean something - a SHA256 of its CONTENT. Metadata
        alone does not settle identity: a replacement written over the top of
        the file keeps its name and creation time, a same-size replacement
        keeps its length, and a timestamp is one SetFileTime call away from
        being kept too. The digest is what a substitution cannot preserve, and
        the files this script binds - one interpreter and one console script -
        are read once per use site, which is a cost measured in tens of
        milliseconds.
    .OUTPUTS
        System.String, or $null.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return $null
    }

    $isReparsePoint = '0'
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq [System.IO.FileAttributes]::ReparsePoint) {
        $isReparsePoint = '1'
    }
    $length = '-'
    $digest = '-'
    $written = '-'
    if ($item.PSObject.Properties.Match('Length').Count -gt 0 -and $null -ne $item.Length) {
        $length = [string]$item.Length
        $written = [string]$item.LastWriteTimeUtc.Ticks
        # A file that cannot be hashed has no identity to report, and an
        # unreadable one must not degrade to a metadata-only token: the caller
        # treats $null as a failure, which is the refusal this belongs in.
        try {
            $digest = (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash
        }
        catch {
            return $null
        }
        if ([string]::IsNullOrWhiteSpace($digest)) {
            return $null
        }
    }

    return $isReparsePoint + '|' + [string]$item.CreationTimeUtc.Ticks + '|' + $written + '|' + $length + '|' + $digest + '|' + [string]$item.FullName
}

function Write-VenvIdentityFailure {
    <#
    .SYNOPSIS
        Report a virtual environment that changed identity between the checks
        and a use of it, and stop.
    .DESCRIPTION
        A bootstrap failure: exit 1. The mirror of venv_identity_failed in
        scripts/run_tests.sh. Nothing is deleted - the operator is told what to
        remove.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Use,

        [Parameter(Mandatory = $true)]
        [string] $Change
    )

    Write-Diagnostic @(
        'run_tests.ps1: the virtual environment changed identity mid-run.',
        "  before this : $(ConvertTo-SafeText $Use)",
        "  change      : $Change",
        "  path        : $(ConvertTo-SafeText $script:venvDirectory)",
        '',
        'Step 2 validated that path and something outside this script has',
        'replaced it since. Both pip installs write INTO it and every command',
        'after them runs THROUGH its interpreter, so the run stops here rather',
        'than installing into, or executing from, an object it never checked.',
        '',
        'Nothing has been deleted. Remove the environment yourself and re-run:',
        '',
        '    Remove-Item -Recurse -Force .venv'
    )
    exit 1
}

function Assert-VenvIdentity {
    <#
    .SYNOPSIS
        Re-assert that .venv is still the object step 2 validated, immediately
        before each use of it.
    .DESCRIPTION
        Step 2's checks and the commands that follow are separated in time, and
        in between .venv is an ordinary path that anything else on the machine
        can replace. A directory swapped for a junction would send both pip
        installs into someone else's environment; an interpreter swapped for a
        shim would then be executed by the unit gate, the four coverage gates
        and the runner. Binding the OBJECT rather than trusting the path is
        what closes that window, so the tests below - not a reparse point, the
        same resolved path, the same directory token, still a file, the same
        interpreter content, and the same console script once step 3 has bound
        it - are repeated at every use site instead of being believed once.

        What this does NOT close, stated rather than glossed over: the interval
        between the last check and the command itself. Windows PowerShell 5.1
        cannot hold an .exe against replacement while launching it, so the
        check and the use cannot be made one indivisible act here. Every call
        site therefore sits IMMEDIATELY before its command, with nothing in
        between, which makes that interval as small as this runtime allows
        rather than absent.

        The mirror of venv_reassert in scripts/run_tests.sh.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Use
    )

    $item = Get-Item -LiteralPath $script:venvDirectory -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        Write-VenvIdentityFailure -Use $Use -Change '.venv can no longer be read'
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq [System.IO.FileAttributes]::ReparsePoint) {
        Write-VenvIdentityFailure -Use $Use -Change '.venv has become a link, junction or mounted folder'
    }

    $resolved = ''
    try {
        $resolved = (Resolve-Path -LiteralPath $script:venvDirectory -ErrorAction Stop).ProviderPath
    }
    catch {
        $resolved = ''
    }
    if ($resolved -ne $script:venvDirectory) {
        $reportedPath = $resolved
        if ([string]::IsNullOrWhiteSpace($reportedPath)) {
            $reportedPath = 'a directory that cannot be entered'
        }
        Write-VenvIdentityFailure -Use $Use -Change (".venv now resolves to " + (ConvertTo-SafeText $reportedPath))
    }

    $token = Get-IdentityToken -Path $script:venvDirectory
    if ([string]::IsNullOrWhiteSpace($token) -or $token -ne $script:venvDirectoryToken) {
        Write-VenvIdentityFailure -Use $Use -Change '.venv is not the directory validated in step 2'
    }

    if (-not (Test-Path -LiteralPath $script:venvPython -PathType Leaf)) {
        Write-VenvIdentityFailure -Use $Use -Change ((ConvertTo-SafeText $script:venvPython) + ' is no longer a file')
    }
    $token = Get-IdentityToken -Path $script:venvPython
    if ([string]::IsNullOrWhiteSpace($token) -or $token -ne $script:venvPythonToken) {
        Write-VenvIdentityFailure -Use $Use -Change 'the interpreter is not the file validated in step 2'
    }

    # The console script, once step 3 has bound it. Its token is empty until
    # then - the editable install is what creates the file - so the calls that
    # happen before that simply have nothing to re-assert here, and every call
    # after it, the one immediately before step 6, covers it.
    if (-not [string]::IsNullOrWhiteSpace($script:venvRunTestsToken)) {
        if (-not (Test-Path -LiteralPath $script:venvRunTests -PathType Leaf)) {
            Write-VenvIdentityFailure -Use $Use -Change 'the console script is no longer a file'
        }
        $token = Get-IdentityToken -Path $script:venvRunTests
        if ([string]::IsNullOrWhiteSpace($token) -or $token -ne $script:venvRunTestsToken) {
            Write-VenvIdentityFailure -Use $Use -Change 'the console script is not the file validated in step 3'
        }
    }
}

function Assert-VenvIdentityCaptured {
    <#
    .SYNOPSIS
        Capture the identity of the virtual environment, or stop.
    .DESCRIPTION
        Called the moment step 2's checks pass and BEFORE the environment's
        interpreter is executed for the first time - the version probe is
        already a use of it, so a capture taken afterwards would be binding
        whatever had answered that probe. A token that cannot be read is a
        bootstrap failure: without one, no later command can be proved to be
        running what was checked. The mirror of capture_venv_identity in
        scripts/run_tests.sh.
    #>
    [CmdletBinding()]
    param()

    $script:venvDirectoryToken = Get-IdentityToken -Path $script:venvDirectory
    $script:venvPythonToken = Get-IdentityToken -Path $script:venvPython
    if (-not [string]::IsNullOrWhiteSpace($script:venvDirectoryToken) -and
        -not [string]::IsNullOrWhiteSpace($script:venvPythonToken)) {
        return
    }

    Write-Diagnostic @(
        'run_tests.ps1: the virtual environment cannot be identified.',
        "  path        : $(ConvertTo-SafeText $script:venvDirectory)",
        "  interpreter : $(ConvertTo-SafeText $script:venvPython)",
        '',
        'Both were validated a moment ago, so one of them has just become',
        'unreadable. Without an identity for them this script cannot prove at',
        'each later command that it is still installing into, and running,',
        'what it checked - so it stops instead.',
        '',
        'Nothing has been deleted. Remove the environment yourself and re-run:',
        '',
        '    Remove-Item -Recurse -Force .venv'
    )
    exit 1
}

function Get-InterpreterVersion {
    <#
    .SYNOPSIS
        Report "major.minor.micro" for a candidate interpreter, or $null when
        it cannot be run or says nothing.
    .DESCRIPTION
        Probing is deliberately tolerant, and is the ONLY thing in this file
        that is: a candidate which does not exist, is a stale shim, or is the
        Windows Store python.exe app-execution-alias stub has to count as "no
        match" so step 1 can keep looking and report everything it tried. So
        its stderr is discarded, a non-zero status is ignored, and 'Stop' is
        relaxed for this one call. No gate is ever probed through here.
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

    # -I -S is what makes the probe say something about the interpreter rather
    # than about the environment around it: -I ignores PYTHONPATH, PYTHONHOME
    # and the per-user site directory and keeps the current directory off
    # sys.path, and -S skips site initialisation, so no sitecustomize,
    # usercustomize or .pth file runs before the one expression above. Callers
    # pass a path Test-TrustedProgram has already approved.
    try {
        $output = & $Command @Argument '-I' '-S' '-c' $probeExpression 2>$null
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

    # Bound what a probed program can put into this script, mirroring the
    # `head -c 4096` in scripts/run_tests.sh: a candidate that streams without
    # stopping cannot grow this process or a diagnostic without limit. The
    # pinned version is three numbers, so no legitimate answer is affected.
    if ($reported.Length -gt $ProbeOutputLimit) {
        $reported = $reported.Substring(0, $ProbeOutputLimit)
    }

    return $reported
}

function Get-LauncherInterpreterPath {
    <#
    .SYNOPSIS
        The path of the interpreter the Windows Python launcher has registered
        for the pinned SERIES, or $null when it cannot be determined.
    .DESCRIPTION
        Trust-checking `py.exe` says something about the LAUNCHER and nothing
        about the interpreter it would then start: `py -3.14 ...` hands the work
        to whatever path the launcher has registered for that series, and that
        path - a registry entry any installer can write - would otherwise be
        executed without ever being authenticated. So the launcher is asked
        which file it would use, and the answer is then put through the same
        trust gate as any other candidate and executed directly.

        `--list-paths` is the one launcher operation that answers this question
        without starting a runtime: it prints its registered versions and their
        executables and exits, so nothing is executed here except the launcher
        the caller has already trust-checked. Some launcher builds write that
        listing to stderr, so both streams are read.

        A line of the listing reads like `-V:3.14 *  C:\Python314\python.exe`,
        with older builds spelling the tag `-3.14-64`; either is accepted, and
        anything else - no matching line, no absolute path - returns $null so
        the caller can report the candidate as rejected and carry on down its
        documented order.
    .OUTPUTS
        System.String, or $null.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Launcher
    )

    # Relaxed for this one call, exactly as Get-InterpreterVersion does and for
    # the same reason: a launcher that writes to stderr must not terminate the
    # script before its answer can be judged.
    $ErrorActionPreference = 'Continue'

    $listing = $null
    try {
        $listing = & $Launcher '--list-paths' 2>&1
    }
    catch {
        return $null
    }
    if ($null -eq $listing) {
        return $null
    }

    $series = $RequiredPythonVersion
    $lastSeparator = $series.LastIndexOf('.')
    if ($lastSeparator -gt 0) {
        $series = $series.Substring(0, $lastSeparator)
    }
    $pattern = '^\s*-(?:V:)?' + [regex]::Escape($series) + '(?:-(?:32|64|arm64))?\s*\*?\s+(\S.*)$'

    foreach ($line in @($listing)) {
        $text = [string]$line
        # Bound what a program can put into this script, as everywhere else.
        if ($text.Length -gt $ProbeOutputLimit) {
            $text = $text.Substring(0, $ProbeOutputLimit)
        }
        $found = [regex]::Match($text, $pattern)
        if ($found.Success) {
            $reported = $found.Groups[1].Value.Trim()
            if (-not [string]::IsNullOrWhiteSpace($reported) -and
                [System.IO.Path]::IsPathRooted($reported)) {
                return $reported
            }
            return $null
        }
    }

    return $null
}

function Test-PinnedInterpreter {
    <#
    .SYNOPSIS
        Return $true when a candidate resolves AND reports exactly the pinned
        version; otherwise record what it reported and return $false.
    .DESCRIPTION
        This one test is applied to every candidate including $env:PYTHON,
        which is what makes that override incapable of relaxing the pin. A near
        miss is reported and rejected, never used: silently falling back to
        another interpreter is the specific failure step 1 exists to prevent.

        -Launcher marks the one candidate that is not itself an interpreter.
        For it, the trust gate on py.exe is only the first half: the registered
        interpreter py.exe would start is then read with
        Get-LauncherInterpreterPath, put through the same gate WITH its ancestor
        walk, and becomes the resolved path - so the file executed from step 2
        on is one this function authenticated, and `py -3.14` is never invoked
        again. The path this leaves in $script:ResolvedInterpreterPath therefore
        needs no arguments, which is what $script:ResolvedInterpreterArgument
        records for the caller.
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
        [string[]] $Argument = @(),

        [Parameter(Mandatory = $false)]
        [switch] $Launcher
    )

    # -ErrorAction SilentlyContinue here is resolution, not suppression: an
    # absent candidate is an expected outcome of probing and is reported as
    # "not found on PATH" on the next line.
    $resolved = Get-Command -Name $Command -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $resolved) {
        Add-ProbeNote ('{0}: not found on PATH' -f (ConvertTo-SafeText $Label))
        return $false
    }

    $source = $resolved.Source
    if ([string]::IsNullOrWhiteSpace($source)) {
        $source = $resolved.Name
    }

    # The candidate has to resolve to an absolute path, and that path has to be
    # one this script is willing to run, BEFORE it is run: probing executes the
    # candidate. A rejected candidate becomes a note and is never started.
    if (-not [System.IO.Path]::IsPathRooted($source)) {
        Add-ProbeNote ('{0} ({1}): rejected, it does not resolve to an absolute path' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $source))
        return $false
    }
    if (-not (Test-TrustedProgram -Path $source)) {
        Add-ProbeNote ('{0} ({1}): {2}' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $source), $script:TrustReason)
        return $false
    }

    # The launcher is trusted as a launcher; the interpreter it has registered
    # for the pinned series is a separate program and is authenticated as one.
    # From here on this function knows only that second file.
    if ($Launcher) {
        $registered = Get-LauncherInterpreterPath -Launcher $source
        if ([string]::IsNullOrWhiteSpace($registered)) {
            Add-ProbeNote ('{0} ({1}): rejected, it names no registered interpreter this script can read' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $source))
            return $false
        }
        if (-not (Test-TrustedProgram -Path $registered)) {
            Add-ProbeNote ('{0} ({1}): {2}' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $registered), $script:TrustReason)
            return $false
        }
        $source = $registered
        $Argument = @()
    }

    # The resolved path, not the name it was reached by: what is probed is what
    # step 2 will start, with no second resolution through PATH in between.
    $reported = Get-InterpreterVersion -Command $source -Argument $Argument
    if ([string]::IsNullOrWhiteSpace($reported)) {
        Add-ProbeNote ('{0} ({1}): found, but reported no version' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $source))
        return $false
    }

    if ($reported -eq $RequiredPythonVersion) {
        $script:ResolvedInterpreterPath = $source
        $script:ResolvedInterpreterArgument = $Argument
        return $true
    }

    # Rendered for the report only. The comparison above is against the exact,
    # untouched string the candidate printed.
    Add-ProbeNote ('{0} ({1}): reported {2}' -f (ConvertTo-SafeText $Label), (ConvertTo-SafeText $source), (ConvertTo-SafeText $reported))
    return $false
}


# --------------------------------------------------------------------------
# The Python startup surface, dropped before any interpreter is run.
#
# Every interpreter this script starts - the step 1 candidates, the environment
# builder, pip, pytest, the attestation and the runner - inherits this
# process's environment, and five of its variables can make an interpreter
# execute code before it reaches the program it was given: PYTHONPATH and
# PYTHONHOME decide where modules come from, PYTHONSTARTUP names a file to run,
# PYTHONEXECUTABLE renames sys.executable, and PYTHONUSERBASE moves the
# per-user site directory. They are removed here, once, rather than guarded at
# each of a dozen call sites, and PYTHONNOUSERSITE is set so that no user site
# directory is added even if one exists. Presence is tested rather than a
# non-empty value, because an EMPTY PYTHONPATH is not nothing - it puts the
# current directory on sys.path - so it is removed and reported like any other.
#
# This is one half of a control whose other half is the isolation flag on each
# individual command - `-I -S` on a version probe and on the attestation, and
# `-I` on the environment builder, the two installs and the gates. This
# block governs what a child inherits; the flags govern the process being
# started. Neither alone is enough, because a flag cannot unset a variable for
# a program it does not launch and an unset variable does not stop site
# initialisation inside the environment being used.
#
# Nothing else about the caller's environment is touched: PATH, the locale and
# everything the suite itself reads are the operator's.
# --------------------------------------------------------------------------
$startupDropped = New-Object 'System.Collections.Generic.List[string]'
foreach ($startupVariable in @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONEXECUTABLE', 'PYTHONUSERBASE')) {
    $startupPath = Join-Path -Path 'Env:' -ChildPath $startupVariable
    if (Test-Path -LiteralPath $startupPath) {
        $startupDropped.Add($startupVariable)
        Remove-Item -LiteralPath $startupPath -Force -ErrorAction SilentlyContinue
    }
}
$env:PYTHONNOUSERSITE = '1'

# The installer's configuration surface goes with it, and for the same reason.
# EVERY pip option has an environment twin - PIP_INDEX_URL and
# PIP_EXTRA_INDEX_URL choose where distributions come from, PIP_TRUSTED_HOST
# and PIP_CERT decide what is trusted on the way, PIP_TARGET, PIP_PREFIX and
# PIP_ROOT decide where they land, PIP_FIND_LINKS and PIP_CONFIG_FILE bring in
# more of the same - so an ambient PIP_ variable can replace both what step 3
# installs and where it installs it, without appearing anywhere in the command
# line this file spells out.
#
# The names are not listed: they are ENUMERATED from the environment, because
# pip accepts one for every option it has and a list here would be a list of
# the ones thought of. Everything matching PIP_<identifier> is dropped and
# reported.
$pipDropped = New-Object 'System.Collections.Generic.List[string]'
foreach ($pipVariable in @(Get-ChildItem -Path 'Env:' | Where-Object { $_.Name -match '^PIP_[A-Za-z0-9_]+$' })) {
    $pipDropped.Add($pipVariable.Name)
    Remove-Item -LiteralPath (Join-Path -Path 'Env:' -ChildPath $pipVariable.Name) -Force -ErrorAction SilentlyContinue
}

# And pip's CONFIGURATION FILES, which are not environment variables and so
# survive the loop above. --isolated is not enough on its own: it skips the
# PIP_ variables and the per-user file, but NOT the SITE configuration file
# inside sys.prefix - .venv\pip.ini - nor the machine-wide one. Measured on the
# POSIX side, where the mechanism is identical: a pip.conf planted in the
# environment about to be installed into, naming an attacker index, a
# trusted-host and a target directory, was honoured by the fully isolated
# command, and was not read at all once this variable pointed at the null
# device. pip compares this value with os.devnull - `nul` on Windows - and,
# when they match, reads NO configuration file. It is set AFTER the loop so
# the loop cannot undo it.
$env:PIP_CONFIG_FILE = 'nul'

# The gates' own configuration surface, and the same reasoning once more.
# PYTEST_ADDOPTS is prepended to every pytest command line, so an ambient value
# can add -p to load a plugin, or --cov-fail-under=0 to neuter a coverage gate.
# PYTEST_PLUGINS names modules pytest IMPORTS at startup, and - measured - it
# is honoured even under the --disable-plugin-autoload that pytest.ini sets,
# because that switch governs entry-point discovery and not this variable. A
# gate an environment variable can weaken, or inject code into, is not a gate.
$pytestDropped = New-Object 'System.Collections.Generic.List[string]'
foreach ($pytestVariable in @('PYTEST_ADDOPTS', 'PYTEST_PLUGINS')) {
    $pytestPath = Join-Path -Path 'Env:' -ChildPath $pytestVariable
    if (Test-Path -LiteralPath $pytestPath) {
        $pytestDropped.Add($pytestVariable)
        Remove-Item -LiteralPath $pytestPath -Force -ErrorAction SilentlyContinue
    }
}

$environmentDropped = New-Object 'System.Collections.Generic.List[string]'
$environmentDropped.AddRange($startupDropped)
$environmentDropped.AddRange($pipDropped)
$environmentDropped.AddRange($pytestDropped)
if ($environmentDropped.Count -gt 0) {
    Write-Output "run_tests.ps1: dropped from the environment: $(ConvertTo-SafeText ($environmentDropped -join ' '))"
}


# --------------------------------------------------------------------------
# Working directory: the repository root, derived from this script's own
# location so that the run is identical whether Jenkins invokes it from the
# workspace root or a developer invokes it from a subdirectory.
#
# $PSScriptRoot is populated for a script run from a file, which is how the
# pipeline runs this one. .ProviderPath - rather than .Path - strips any
# PowerShell provider prefix, so the native commands below are handed an
# ordinary Windows path.
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
        "  script location : $(ConvertTo-SafeText $PSScriptRoot)",
        "  directory tried : $(ConvertTo-SafeText (Join-Path -Path $PSScriptRoot -ChildPath '..'))",
        "  reason          : $(ConvertTo-SafeText $_.Exception.Message)",
        'Run this script from a complete checkout, as either',
        '    powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1',
        'from the repository root or with any path that reaches it.'
    )
    exit 1
}

# Pre-flight: the three files step 3 installs from. Checking them here turns
# an obscure installer error into an actionable one, and confirms the
# directory reached above is the repository root.
$missingManifest = New-Object 'System.Collections.Generic.List[string]'
foreach ($manifest in @('pyproject.toml', 'requirements.txt', 'requirements-test.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path -Path $repositoryRoot -ChildPath $manifest) -PathType Leaf)) {
        $missingManifest.Add($manifest)
    }
}
if ($missingManifest.Count -gt 0) {
    Write-Diagnostic @(
        'run_tests.ps1: this does not look like a complete checkout.',
        "  working directory : $(ConvertTo-SafeText $repositoryRoot)",
        "  missing file(s)   : $(ConvertTo-SafeText ($missingManifest -join ', '))",
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
# python. The first EXACT match wins, and a near miss is reported and rejected
# rather than used: silently falling back to whatever python resolves to is
# the specific failure this step exists to prevent.
#
# The launcher comes before the bare names because it is the canonical Windows
# mechanism for asking for a particular version and, unlike a bare name, cannot
# be shadowed by whatever happens to sit first on PATH. It selects a series and
# not a patch level, so the exact check still decides: a launcher that answers
# 3.14.4 is reported and rejected like any other near miss. It is also the one
# candidate that is not an interpreter: what it would start is read from it and
# authenticated separately, as Test-PinnedInterpreter describes.
# --------------------------------------------------------------------------
$interpreterCandidate = New-Object 'System.Collections.Generic.List[hashtable]'

if (-not [string]::IsNullOrWhiteSpace($env:PYTHON)) {
    $interpreterCandidate.Add(@{
        Label    = '$env:PYTHON=' + $env:PYTHON
        Command  = $env:PYTHON
        Argument = @()
        Launcher = $false
    })
}

$interpreterCandidate.Add(@{ Label = 'py -3.14';   Command = 'py';         Argument = @('-3.14'); Launcher = $true })
$interpreterCandidate.Add(@{ Label = 'python3.14'; Command = 'python3.14'; Argument = @();        Launcher = $false })
$interpreterCandidate.Add(@{ Label = 'python3';    Command = 'python3';    Argument = @();        Launcher = $false })
$interpreterCandidate.Add(@{ Label = 'python';     Command = 'python';     Argument = @();        Launcher = $false })

$pythonCommand = ''
$pythonArgument = @()
$pythonLabel = ''
$probedInvocation = New-Object 'System.Collections.Generic.List[string]'

foreach ($candidate in $interpreterCandidate) {
    # Skip an invocation already probed - in practice $env:PYTHON naming one
    # of the bare candidates - so the report lists it once. The key is the
    # command plus its arguments, so `py -3.14` and a bare `py` stay distinct.
    $invocationKey = ($candidate.Command + ' ' + ($candidate.Argument -join ' ')).Trim()
    if ($probedInvocation -contains $invocationKey) {
        continue
    }
    $probedInvocation.Add($invocationKey)

    if (Test-PinnedInterpreter -Label $candidate.Label -Command $candidate.Command -Argument $candidate.Argument -Launcher:([bool]$candidate.Launcher)) {
        # The RESOLVED path and ITS arguments, mirroring PYTHON_BIN in
        # scripts/run_tests.sh: the program started in step 2 is the file step 1
        # approved and probed, which for the launcher candidate is the
        # registered interpreter rather than py.exe - so no series argument
        # survives here and step 2 starts an authenticated file directly.
        $pythonCommand = $script:ResolvedInterpreterPath
        $pythonArgument = $script:ResolvedInterpreterArgument
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
        '    interpreter is tried first, it does not relax the pin, and it',
        '    does not relax the trust check either - a candidate anyone on',
        '    the machine could replace is reported above as rejected and is',
        '    never run.'
    )
    exit 1
}

Write-Output "run_tests.ps1: using $(ConvertTo-SafeText $pythonLabel) [$(ConvertTo-SafeText $ResolvedInterpreterPath)] ($RequiredPythonVersion)"



# --------------------------------------------------------------------------
# Step 2 - the virtual environment: create it when it is missing, refuse a
# drifted or redirected one.
#
# THREE rejections - (a) a redirected .venv, meaning a directory symbolic
# link, an NTFS junction, a mounted folder or any other reparse point, (b)
# one that is not a directory, (c) one whose interpreter is not exactly the
# pinned version - and the order matters: (a) is tested FIRST because the
# other two follow reparse points, Test-Path -PathType Container being true
# for a junction to a directory, so a .venv pointing at a shared, profile or
# machine-wide 3.14.6 environment would pass them both and step 3 would
# pip-install into that external environment. This mirrors the `-L` test in
# scripts/run_tests.sh, where POSIX -d has the same defect.
#
# None of the three deletes anything: silently destroying a developer's
# environment, or following a link out of the checkout to destroy something
# else, is a destructive act nobody asked for. The operator is told what to
# remove and the run stops.
#
# Every path is built with Join-Path against the resolved repository root, one
# child at a time because Windows PowerShell 5.1 has no -AdditionalChildPath,
# and the Windows layout is a Scripts directory of .exe shims rather than a
# POSIX bin directory.
# --------------------------------------------------------------------------
$venvDirectory = Join-Path -Path $repositoryRoot -ChildPath '.venv'
$venvScriptDirectory = Join-Path -Path $venvDirectory -ChildPath 'Scripts'
$venvPython = Join-Path -Path $venvScriptDirectory -ChildPath 'python.exe'
$venvRunTests = Join-Path -Path $venvScriptDirectory -ChildPath 'run-tests.exe'

# (a) -Force so a hidden entry is still seen, and SilentlyContinue so an
# absent .venv - the ordinary first-run case - is simply $null here. The
# attribute test is the gate: it covers every reparse-point kind at once,
# while LinkType and Target only enrich the diagnostic.
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
        "  path   : $(ConvertTo-SafeText $venvDirectory)",
        "  kind   : $(ConvertTo-SafeText $venvRedirectionKind)",
        "  target : $(ConvertTo-SafeText $venvRedirectionTarget)",
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
        "  path : $(ConvertTo-SafeText $venvDirectory)",
        'The virtual environment has to live there. Remove or rename that',
        'entry and re-run; this script will not delete it for you.'
    )
    exit 1
}

# (c)
if (Test-Path -LiteralPath $venvDirectory -PathType Container) {
    Assert-TrustedVenvInterpreter -Path $venvPython
    Assert-VenvIdentityCaptured
    Assert-VenvIdentity -Use 'the interpreter version probe'
    $venvVersion = Get-InterpreterVersion -Command $venvPython
    if ($venvVersion -ne $RequiredPythonVersion) {
        $reportedVenvVersion = $venvVersion
        if ([string]::IsNullOrWhiteSpace($reportedVenvVersion)) {
            $reportedVenvVersion = 'no version reported (missing or not runnable)'
        }
        Write-Diagnostic @(
            'run_tests.ps1: the existing .venv is not usable for this project.',
            "  required interpreter     : $RequiredPythonVersion",
            "  .venv\Scripts\python.exe : $(ConvertTo-SafeText $reportedVenvVersion)",
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
    $venvCreationArgument += @('-I', '-m', 'venv', '.venv')
    Invoke-NativeCommand -FilePath $pythonCommand -ArgumentList $venvCreationArgument
    $venvStatus = $script:LastNativeExitCode
    if ($venvStatus -ne 0) {
        Write-Diagnostic @(
            'run_tests.ps1: failed to create the .venv virtual environment.',
            "  interpreter : $(ConvertTo-SafeText $ResolvedInterpreterPath)",
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
    Assert-TrustedVenvInterpreter -Path $venvPython
    Assert-VenvIdentityCaptured
    Assert-VenvIdentity -Use 'the interpreter version probe'
    $venvVersion = Get-InterpreterVersion -Command $venvPython
    if ($venvVersion -ne $RequiredPythonVersion) {
        $reportedVenvVersion = $venvVersion
        if ([string]::IsNullOrWhiteSpace($reportedVenvVersion)) {
            $reportedVenvVersion = 'no version reported (missing or not runnable)'
        }
        Write-Diagnostic @(
            'run_tests.ps1: .venv was created but has no usable interpreter.',
            "  required interpreter     : $RequiredPythonVersion",
            "  .venv\Scripts\python.exe : $(ConvertTo-SafeText $reportedVenvVersion)",
            '',
            'Creation reported success, so this points at the environment',
            'rather than at this script. Remove .venv with',
            '"Remove-Item -Recurse -Force .venv", check the interpreter above,',
            'and re-run.'
        )
        exit 1
    }
}

# Step 2 proved this interpreter reports the pinned version, so this check
# guards the half-removed environment - a Scripts directory emptied between
# the two - rather than letting it surface as an obscure installer error.
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Diagnostic @(
        'run_tests.ps1: the virtual environment interpreter has gone missing.',
        "  expected : $(ConvertTo-SafeText $venvPython)",
        'It was present a moment ago, so something outside this script removed',
        'it mid-run. Remove the environment with',
        '"Remove-Item -Recurse -Force .venv" and re-run.'
    )
    exit 1
}

# The environment's identity was captured inside each branch above, BEFORE its
# interpreter was executed for the first time - the version probe is already a
# use of it - and Assert-VenvIdentity re-asserts that binding before every
# single use of it below.


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
# nothing else. No index URL is set either: pip uses its own default index,
# because --isolated ignores the PIP_ variables and the per-user configuration
# file, PIP_CONFIG_FILE was pointed at the null device near the top of this
# file so that the SITE configuration file inside the environment - .venv's own
# pip.ini, which --isolated does NOT skip - is not read either, and every
# PIP_ variable was removed there. Those three together are what decide the
# source, the trust and the destination; any one of them alone leaves a way in.
#
# The second install is the project distribution, and step 5 cannot run
# without it: installing -r manifests installs DEPENDENCIES ONLY, while the
# run-tests console script declared in pyproject.toml under
#     [project.scripts] run-tests = "app.cli:run_tests"
# is materialised into the environment's Scripts directory only when the
# distribution itself is installed. Editable (-e .) specifically, never a
# plain '.': CI has to execute the code in the checked-out workspace, whereas
# a non-editable install copies a snapshot into site-packages and could then
# run stale code against a fresh checkout.
#
# pip is non-interactive by default and --no-input says so explicitly, so a
# prompt cannot stall an unattended stage. --require-virtualenv refuses to
# install at all unless the interpreter running pip is in a virtual
# environment, which is the destination half of the same guarantee the identity
# binding gives: an install can only land in the environment step 2 validated.
# --quiet and --disable-pip-version-check keep the CI log to the point. The
# environment's own python is used with -m pip rather than the pip shim beside
# it, so the interpreter running the installer is unambiguously the one just
# verified.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: installing pinned dependencies'
Assert-VenvIdentity -Use 'installing the pinned dependencies'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-I', '-m', 'pip', 'install',
    '--isolated', '--no-input', '--require-virtualenv',
    '--quiet', '--disable-pip-version-check',
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
Assert-VenvIdentity -Use 'installing this project'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-I', '-m', 'pip', 'install',
    '--isolated', '--no-input', '--require-virtualenv',
    '--quiet', '--disable-pip-version-check', '-e', '.'
)
$projectStatus = $script:LastNativeExitCode
if ($projectStatus -ne 0) {
    Write-Diagnostic @(
        'run_tests.ps1: installing this project in editable mode failed.',
        "  project     : . (pyproject.toml in $(ConvertTo-SafeText $repositoryRoot))",
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

# Still step 3: the console script that install just created is what step 6
# invokes, and nothing has been said about it until now. It gets the same two
# parts the interpreter has - a trust gate, then an identity token - so that
# the program run at the end is the program checked here. No ancestor walk, for
# the reason Test-TrustedProgram gives: it lives inside the .venv whose own
# identity is re-asserted alongside it at every use.
Assert-VenvIdentity -Use 'binding the console script'
if (-not (Test-TrustedProgram -Path $venvRunTests -SkipAncestors)) {
    Write-Diagnostic @(
        'run_tests.ps1: the console script is not one this script will run.',
        "  path   : $(ConvertTo-SafeText $venvRunTests)",
        "  reason : $script:TrustReason",
        '',
        'Step 6 invokes that file, so it is refused rather than used. The',
        'editable install that creates it has just reported success, so a',
        'refusal here means the file on disk is not the one it wrote.',
        '',
        'Nothing has been deleted. Remove the environment yourself and re-run:',
        '',
        '    Remove-Item -Recurse -Force .venv'
    )
    exit 1
}
$venvRunTestsToken = Get-IdentityToken -Path $venvRunTests
if ([string]::IsNullOrWhiteSpace($venvRunTestsToken)) {
    Write-Diagnostic @(
        'run_tests.ps1: the console script cannot be identified.',
        "  path : $(ConvertTo-SafeText $venvRunTests)",
        '',
        'It passed its trust check a moment ago, so it has just become',
        'unreadable. Without an identity for it this script cannot prove in',
        'step 6 that it is invoking what it checked, so it stops instead.',
        '',
        'Nothing has been deleted. Remove the environment yourself and re-run:',
        '',
        '    Remove-Item -Recurse -Force .venv'
    )
    exit 1
}

# Still step 3, and its last act: the attestation. Both installs have reported
# success, and success from pip means only that its resolver was satisfied - it
# says nothing about what ELSE is in the environment. A virtual environment is
# reused across runs and across clean checkouts of this repository, and pip
# leaves an already-satisfying distribution in place, so anything that ever
# arrived in .venv by any route at all survives every later run silently. The
# version check in step 2 cannot see it: it asks one question of one
# interpreter.
#
# So the environment is attested as a whole, in the interpreter that is about
# to run every gate, before any of them runs. EIGHT checks, any one of which is
# a bootstrap failure:
#   1. the interpreter really is in the expected virtual environment;
#   2. both manifests hold nothing but exact name==version pins, read from disk
#      rather than restated here;
#   3. every installed distribution resolves inside the environment, and no two
#      of them canonicalise to the same name;
#   4. every pin is installed at exactly the pinned version;
#   5. nothing is installed outside the dependency closure of those pins plus
#      this project, allowing only the environment builder's own seeds;
#   6. exactly one pytest plugin entry point exists and it belongs to
#      pytest-cov - the one plugin the coverage gates load by name, now that
#      pytest.ini has stopped pytest from importing plugins on its own;
#   7. every file a distribution records a digest for is present and matches
#      it, so a patched module inside a correctly pinned distribution fails;
#   8. no file in the environment's site directories is unrecorded, which is
#      what refuses a bare .pth, a sitecustomize or any other unowned import
#      hook - the objects that would otherwise run inside every later command.
#
# The program is standard library only, takes the expected environment path as
# its one argument, prints the check that failed and the remedy, and is the
# same text in scripts/run_tests.sh. It runs with -I -S, and the -S is the
# load-bearing half: -I alone still lets site initialisation run every .pth
# file in the environment UNDER TEST before the first check executes, which is
# precisely the code path check 8 exists to refuse. Measured: a planted .pth
# executed under -I and did not under -I -S. Because -S also leaves the site
# directories off sys.path, the program builds its own search path from this
# interpreter's purelib and platlib and reads both the distributions and their
# entry points through it.
Write-Output 'run_tests.ps1: attesting the environment'
Assert-VenvIdentity -Use 'attesting the environment'
$attestationProgram = @'
import base64
import csv
import hashlib
import importlib.metadata as meta
import os
import re
import sys
import sysconfig
# Bounds and allowances. The seeds are the four distributions an environment
# builder may leave behind; the project distribution is this repository itself.
DIAGNOSTIC_LIMIT = 200
MANIFEST_LIMIT = 262144
DIGEST_CHUNK = 1048576
SEED_DISTRIBUTIONS = ("pip", "setuptools", "wheel", "pkg-resources")
PROJECT_DISTRIBUTION = "testinium-qa"
PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][A-Za-z0-9.!+-]*)$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
QUOTE = chr(34)
APOSTROPHE = chr(39)
SHA256_PREFIX = "sha256="
PAD = chr(61)
def printable(value):
    # Every byte outside printable ASCII becomes a question mark and the result
    # is bounded, for the same reason the calling script renders its own
    # diagnostics: a distribution name or an exception text is data, and data
    # must not be able to forge a record or repaint a console.
    text = ""
    for character in str(value):
        if " " <= character <= "~":
            text = text + character
        else:
            text = text + "?"
    if len(text) > DIAGNOSTIC_LIMIT:
        return text[:DIAGNOSTIC_LIMIT] + "[truncated]"
    return text
def fail(reason):
    # One exit path, so every refusal names the check that failed and the one
    # remedy, and every refusal is a non-zero status.
    print("attestation of the virtual environment failed.", file=sys.stderr)
    print("  failed check : " + printable(reason), file=sys.stderr)
    print("  remedy       : remove .venv and re-run this script.", file=sys.stderr)
    raise SystemExit(1)
def canonical(name):
    # PEP 503 name canonicalisation: comparing raw names would let
    # Pytest_Cov and pytest-cov read as two different distributions.
    return re.sub(r"[-_.]+", "-", str(name)).lower()
def parse_requirement(text):
    # One Requires-Dist value, split into the name it needs, the extras it asks
    # that name for, and its marker. No version specifier is evaluated: the
    # installed versions are checked against the manifests, not against what a
    # dependency would accept.
    marker = ""
    body = str(text)
    if ";" in body:
        halves = body.split(";", 1)
        body = halves[0]
        marker = halves[1]
    body = body.strip()
    extras = []
    if "[" in body and "]" in body and body.index("[") < body.index("]"):
        opening = body.index("[")
        closing = body.index("]")
        for piece in body[opening + 1:closing].split(","):
            if piece.strip():
                extras.append(canonical(piece.strip()))
        body = body[:opening]
    found = NAME_PATTERN.match(body.strip())
    if found is None:
        return "", [], marker
    return canonical(found.group(0)), extras, marker
def marker_extras(marker):
    # The extras a marker names, read without evaluating the marker: the text
    # is split on quotes and a quoted value counts when what precedes it ends
    # in an extra equality. Both quote styles occur in the wild, so apostrophes
    # are folded to quotation marks first.
    names = []
    pieces = marker.replace(APOSTROPHE, QUOTE).split(QUOTE)
    index = 1
    while index < len(pieces):
        head = pieces[index - 1].strip().lower().replace(" ", "")
        if head.endswith("extra=="):
            names.append(canonical(pieces[index]))
        index = index + 2
    return names
def is_requested(marker, wanted):
    # A requirement guarded by an extra belongs to the closure only when that
    # extra was asked for. Deliberately the ONLY marker rule applied: selenium
    # asks for urllib3[socks], so a walk that dropped every extra-guarded
    # requirement would report pysocks as an intruder, and evaluating the other
    # marker kinds would add false refusals rather than remove them.
    named = marker_extras(marker)
    if not named:
        return True
    for name in named:
        if name in wanted:
            return True
    return False
def identify(path):
    # One filesystem path in the form both the walk below and a RECORD entry
    # can be compared in: normalised, and case-folded on the platforms whose
    # filesystem is, so that Scripts and scripts are one file and not two.
    return os.path.normcase(os.path.normpath(str(path)))
def digest_of(path):
    # The sha256 of one installed file, in the urlsafe-unpadded base64 form a
    # RECORD carries, read in bounded chunks so that the largest file in an
    # environment cannot decide this process memory.
    state = hashlib.sha256()
    handle = open(path, "rb")
    try:
        while True:
            chunk = handle.read(DIGEST_CHUNK)
            if not chunk:
                break
            state.update(chunk)
    finally:
        handle.close()
    return base64.urlsafe_b64encode(state.digest()).decode("ascii").rstrip(PAD)
if len(sys.argv) != 2:
    fail("the attestation was invoked without exactly one expected-prefix argument")
expected_prefix = sys.argv[1]
repository_root = os.path.dirname(os.path.abspath(expected_prefix))
# Check one: this interpreter is in the virtual environment the caller means.
if sys.prefix == sys.base_prefix:
    fail("sys.prefix equals sys.base_prefix, so this interpreter is not in a virtual environment")
prefix = os.path.realpath(sys.prefix)
if prefix != os.path.realpath(expected_prefix):
    fail("the running prefix is " + prefix + " rather than the expected " + expected_prefix)
# The search path every check below reads, built from this environment rather
# than from sys.path: the caller starts this program with -I -S precisely so
# that no .pth file and no site initialisation of the environment under test
# runs before the checks, which also leaves sys.path without the very
# directories that have to be examined.
search_path = []
for scheme_key in ("purelib", "platlib"):
    scheme_path = sysconfig.get_paths().get(scheme_key)
    if not scheme_path:
        fail("this interpreter reports no " + scheme_key + " directory, so its contents cannot be examined")
    resolved_scheme = os.path.realpath(scheme_path)
    if resolved_scheme not in search_path:
        search_path.append(resolved_scheme)
for scheme_path in search_path:
    if not os.path.isdir(scheme_path):
        fail("the site directory " + scheme_path + " does not exist")
    if scheme_path != prefix and not scheme_path.startswith(prefix + os.sep):
        fail("the site directory " + scheme_path + " lies outside the environment")
# Check two: both manifests hold nothing but exact pins. Read from disk, never
# hard-coded, so the manifests stay the single declaration of what is pinned.
pinned = {}
for manifest in ("requirements.txt", "requirements-test.txt"):
    manifest_path = os.path.join(repository_root, manifest)
    if not os.path.isfile(manifest_path):
        fail("the manifest " + manifest + " is missing from " + repository_root)
    handle = open(manifest_path, "rb")
    try:
        body = handle.read(MANIFEST_LIMIT).decode("ascii", "replace")
    finally:
        handle.close()
    for line in body.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        matched = PIN_PATTERN.match(entry)
        if matched is None:
            fail("the requirement " + entry + " in " + manifest + " is not an exact name==version pin")
        pinned[canonical(matched.group(1))] = matched.group(2)
if not pinned:
    fail("the two manifests declare no requirement at all")
# Check three: every installed distribution resolves inside this environment,
# and no two of them canonicalise to one name - a collision that a dictionary
# would otherwise silently resolve in favour of whichever was read last.
installed = {}
for distribution in meta.distributions(path=search_path):
    reported = distribution.metadata["Name"]
    if not reported:
        fail("an installed distribution declares no name")
    location = os.path.realpath(str(distribution.locate_file("")))
    if location != prefix and not location.startswith(prefix + os.sep):
        fail("the distribution " + reported + " resolves to " + location + ", outside the environment")
    if canonical(reported) in installed:
        fail("two installed distributions both canonicalise to " + canonical(reported))
    installed[canonical(reported)] = distribution
# Check four: every pin is installed, at exactly the pinned version.
for name in sorted(pinned):
    if name not in installed:
        fail("the pinned distribution " + name + " is not installed")
    version = str(installed[name].version)
    if version != pinned[name]:
        fail("the distribution " + name + " is installed at " + version + " rather than the pinned " + pinned[name])
# Check five: nothing is installed that the pins do not require. The closure is
# walked over Requires-Dist as (name, requested extras) pairs, so a
# distribution that arrived by any other route is reported by name.
closure = set()
visited = set()
pending = [(PROJECT_DISTRIBUTION, ())]
for name in sorted(pinned):
    pending.append((name, ()))
while pending:
    item = pending.pop()
    if item in visited:
        continue
    visited.add(item)
    closure.add(item[0])
    distribution = installed.get(item[0])
    if distribution is None:
        continue
    declared = distribution.metadata.get_all("Requires-Dist")
    if not declared:
        continue
    for requirement in declared:
        dependency, extras, marker = parse_requirement(requirement)
        if not dependency:
            continue
        if not is_requested(marker, item[1]):
            continue
        pending.append((dependency, tuple(sorted(set(extras)))))
unexpected = []
for name in sorted(installed):
    if name in closure or name in SEED_DISTRIBUTIONS:
        continue
    unexpected.append(name)
if unexpected:
    fail("the environment carries " + str(len(unexpected)) + " distribution(s) outside the pinned closure: " + ", ".join(unexpected))
# Check six: exactly one pytest plugin entry point, and it is pytest-cov. The
# four coverage gates load that one by name, and nothing else may be loadable.
# The entry points are read per distribution over the search path above, not
# through the module-level entry_points() helper: that helper reads sys.path,
# which -S deliberately leaves without this site directory, so it would report
# none and the check would pass by looking in the wrong place.
owners = []
for name in sorted(installed):
    for point in installed[name].entry_points:
        if point.group == "pytest11":
            owners.append((name, str(point.name)))
if len(owners) != 1:
    fail("the environment declares " + str(len(owners)) + " pytest11 plugin entry point(s) rather than exactly one")
if owners[0][0] != "pytest-cov":
    fail("the single pytest11 entry point " + owners[0][1] + " belongs to " + owners[0][0] + " rather than pytest-cov")
# Check seven: every file a distribution claims, verified against the digest
# its own RECORD carries. This is what turns "pip reported success" into a
# statement about the bytes on disk: a patched module inside an otherwise
# pinned distribution passes every check above and fails here. An entry with
# no digest is tolerated because a RECORD legitimately carries some - its own
# line and the compiled bytecode it cannot predict - and a missing digest is
# the absence of a claim rather than a claim to test.
owned = set()
for name in sorted(installed):
    distribution = installed[name]
    manifest_text = None
    try:
        manifest_text = distribution.read_text("RECORD")
    except OSError:
        manifest_text = None
    if manifest_text is None:
        fail("the distribution " + name + " carries no RECORD, so its files cannot be verified")
    for row in csv.reader(manifest_text.splitlines()):
        if not row or not row[0].strip():
            continue
        entry_path = str(distribution.locate_file(row[0]))
        owned.add(identify(entry_path))
        recorded = ""
        if len(row) > 1:
            recorded = row[1].strip()
        if not recorded.startswith(SHA256_PREFIX):
            continue
        if not os.path.isfile(entry_path):
            fail("the file " + row[0] + " that " + name + " records is missing from the environment")
        if digest_of(entry_path) != recorded[len(SHA256_PREFIX):]:
            fail("the file " + row[0] + " does not match the digest " + name + " records for it")
# Check eight: nothing in the site directories that no distribution claims.
# Check seven proves that what is owned is unmodified; this one proves there is
# nothing else there at all, which is what refuses a bare .pth file, a
# sitecustomize module or any other unowned import hook - the very objects the
# -I -S this program runs under stops from executing ahead of it. Compiled
# bytecode is excluded because it is generated rather than installed and no
# RECORD can enumerate it.
strays = []
for scheme_path in search_path:
    for directory, children, files in os.walk(scheme_path):
        children[:] = [child for child in children if child != "__pycache__"]
        for leaf in files:
            if leaf.endswith(".pyc") or leaf.endswith(".pyo"):
                continue
            candidate = os.path.join(directory, leaf)
            if identify(candidate) not in owned:
                strays.append(os.path.relpath(candidate, scheme_path))
if strays:
    fail("the environment carries " + str(len(strays)) + " file(s) no distribution records, the first being " + sorted(strays)[0])
# What these eight checks still cannot prove, stated plainly rather than left
# to be assumed: a WHOLESALE FORGED distribution whose own RECORD is
# internally consistent with the files it shipped. Detecting that needs a
# digest from outside the environment, and there is none to compare against -
# AAP 0.5.1 pins direct versions only and ships no hash-locked requirements
# file, so a lock file here would be an invention rather than an
# implementation. What is proven is everything reachable without one: the
# environment holds exactly the pinned closure, at the pinned versions, with
# every file matching the digest its distribution declares, nothing unowned
# beside them, and one loadable pytest plugin.
'@

# Windows PowerShell 5.1 passes a quotation mark inside an argument straight
# through to the native command line, where the C runtime that parses it then
# removes it - which would hand the interpreter a program with every string
# delimiter stripped out. Doubling each one as a backslash-quote pair is the
# documented way to get them through intact, and it is the whole of what is
# needed here because the program deliberately contains no apostrophe and no
# backslash of its own. The program TEXT is unchanged by this and stays
# byte-identical to the one in scripts/run_tests.sh.
$attestationArgument = $attestationProgram.Replace(
    [string][char]34,
    [string][char]92 + [string][char]34
)
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-I', '-S', '-c', $attestationArgument, $venvDirectory
)
$attestationStatus = $script:LastNativeExitCode
if ($attestationStatus -ne 0) {
    Write-Diagnostic @(
        "run_tests.ps1: the environment attestation failed (exit status $attestationStatus).",
        "  environment : $(ConvertTo-SafeText $venvDirectory)",
        '',
        'The attestation above names the one check that failed. It ran in the',
        'environment''s own interpreter, after both installs, and it is what',
        'proves that this environment holds the pinned distributions and',
        'nothing else - a check no reused environment passes by having the',
        'right interpreter version alone.',
        '',
        'Neither gate has been started and nothing has been deleted. Remove',
        'the environment and re-run this script, which will rebuild it:',
        '',
        '    Remove-Item -Recurse -Force .venv'
    )
    exit 1
}


# --------------------------------------------------------------------------
# Step 4 - the quality gate: this port's own test suite, then its coverage.
#
# One step in two parts: the unit run below, and the four per-package
# coverage scopes after it. Both are pytest, both propagate their status, and
# neither is a step of its own.
#
# The unit run is bare on purpose. pytest.ini owns test selection - testpaths
# = tests - which is what keeps the behave step definitions under
# features/steps/ out of the unit suite: they are glue matched by phrase at
# scenario run time and define no pytest tests. No coverage flag either, so a
# coverage miss and a test failure are reported as the separate problems they
# are.
#
# pytest's exit code 5, "no tests collected", propagates as well: a gate that
# collects nothing has not passed. On failure neither the coverage scopes nor
# the suite run is started.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: running the unit gate'
Assert-VenvIdentity -Use 'the unit gate'
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
# Step 4, continued - the coverage scopes: four of them, first miss fails.
#
# A single --cov-fail-under cannot express four different per-package
# thresholds, so pytest runs once per scope and each run measures and gates
# only its own package. The Makefile's `coverage` target is the canonical
# declaration of the four; they are spelled out again here because CI must
# not depend on make being installed - and on a Windows agent it usually is
# not - so a threshold that ever changes changes in both places together.
#
# This is where the thresholds are actually ENFORCED on a CI agent: without
# it the pipeline would run the suite with the gates declared but never
# applied. The first non-zero status is propagated and nothing after it runs.
# --------------------------------------------------------------------------
Write-Output 'run_tests.ps1: running the coverage gates'

Write-Output 'run_tests.ps1: coverage gate 1 of 4 - app/utils, minimum 90 percent'
Assert-VenvIdentity -Use 'coverage gate 1 of 4'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '-p', 'pytest_cov', '--cov=app/utils', '--cov-fail-under=90'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/utils' -Minimum 90 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 2 of 4 - app/pages, minimum 85 percent'
Assert-VenvIdentity -Use 'coverage gate 2 of 4'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '-p', 'pytest_cov', '--cov=app/pages', '--cov-fail-under=85'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/pages' -Minimum 85 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 3 of 4 - app/automation, minimum 80 percent'
Assert-VenvIdentity -Use 'coverage gate 3 of 4'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '-p', 'pytest_cov', '--cov=app/automation', '--cov-fail-under=80'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/automation' -Minimum 80 -Status $coverageStatus
    exit $coverageStatus
}

Write-Output 'run_tests.ps1: coverage gate 4 of 4 - app/reporting, minimum 80 percent'
Assert-VenvIdentity -Use 'coverage gate 4 of 4'
Invoke-NativeCommand -FilePath $venvPython -ArgumentList @(
    '-m', 'pytest', '-p', 'pytest_cov', '--cov=app/reporting', '--cov-fail-under=80'
)
$coverageStatus = $script:LastNativeExitCode
if ($coverageStatus -ne 0) {
    Write-CoverageGateFailure -Scope 'app/reporting' -Minimum 80 -Status $coverageStatus
    exit $coverageStatus
}


# --------------------------------------------------------------------------
# Step 5 - the suite run, through the one sanctioned entry point.
#
# The run-tests console script from the virtual environment's Scripts
# directory, never the Flask CLI and never python -m: pyproject.toml declares
# this entry point and every caller reaches the runner through it.
#
# Its absence is a bootstrap failure rather than a test outcome, so it is
# checked first and reported as such.
# --------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $venvRunTests -PathType Leaf)) {
    Write-Diagnostic @(
        'run_tests.ps1: .venv\Scripts\run-tests.exe is missing.',
        "  expected : $(ConvertTo-SafeText $venvRunTests)",
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
# Invoke-NativeCommand runs the console script in the foreground and captures
# its exit code on the statement immediately following the call, and it holds
# the native-command relaxation the file header explains, without which
# app/cli.py writing one of its tolerated stderr diagnostics while returning
# 0 could terminate this script on a 5.1 agent and fail a stage the exit
# contract requires to pass.
#
# $args is forwarded whatever its length: with no arguments, which is how
# Jenkins invokes this file, the runner is invoked bare and every default
# stays in force. Nothing may follow these two lines.
Assert-VenvIdentity -Use 'the suite run'
Invoke-NativeCommand -FilePath $venvRunTests -ArgumentList $args
exit $script:LastNativeExitCode
