param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path

$versionOutput = & $resolvedExe --version
if ($LASTEXITCODE -ne 0) {
    throw "--version failed with exit code $LASTEXITCODE"
}
if (($versionOutput | Out-String).Trim() -ne "mdrl $ExpectedVersion") {
    throw "unexpected version output: $versionOutput"
}

$helpOutput = & $resolvedExe --help
if ($LASTEXITCODE -ne 0) {
    throw "--help failed with exit code $LASTEXITCODE"
}
if (($helpOutput | Out-String) -notmatch "config" -or ($helpOutput | Out-String) -notmatch "status") {
    throw "help output does not contain core commands"
}

$historyHelp = & $resolvedExe history --help
if ($LASTEXITCODE -ne 0) {
    throw "history --help failed with exit code $LASTEXITCODE"
}
if (($historyHelp | Out-String) -notmatch "play" -or ($historyHelp | Out-String) -notmatch "reveal") {
    throw "history help does not contain recording browsing commands"
}

$duelHelp = & $resolvedExe duel --help
if ($LASTEXITCODE -ne 0) {
    throw "duel --help failed with exit code $LASTEXITCODE"
}
foreach ($command in @("show", "set", "confirm", "history")) {
    if (($duelHelp | Out-String) -notmatch $command) {
        throw "duel help does not contain command: $command"
    }
}

$timelineHelp = & $resolvedExe timeline --help
if ($LASTEXITCODE -ne 0) {
    throw "timeline --help failed with exit code $LASTEXITCODE"
}
foreach ($command in @("list", "add", "confirm", "reject")) {
    if (($timelineHelp | Out-String) -notmatch $command) {
        throw "timeline help does not contain command: $command"
    }
}

$isolatedData = Join-Path ([System.IO.Path]::GetTempPath()) ("mdrl-exe-smoke-" + [guid]::NewGuid().ToString("N"))
$configJson = & $resolvedExe --user-data-dir $isolatedData config show --json
if ($LASTEXITCODE -ne 0) {
    throw "config show --json failed with exit code $LASTEXITCODE"
}
$config = ($configJson | Out-String) | ConvertFrom-Json
if ($config.schema_version -ne 1 -or $config.values."upload.privacy_status" -ne "private") {
    throw "config JSON contract is invalid"
}
if (Test-Path -LiteralPath $isolatedData) {
    throw "read-only config smoke unexpectedly created user_data"
}

$credentialHelperSource = @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class MdrlCredentialSmoke {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct FILETIME {
        public UInt32 dwLowDateTime;
        public UInt32 dwHighDateTime;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {
        public UInt32 Flags;
        public UInt32 Type;
        public string TargetName;
        public string Comment;
        public FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("Advapi32.dll", EntryPoint = "CredWriteW", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CredWrite(ref CREDENTIAL credential, UInt32 flags);

    [DllImport("Advapi32.dll", EntryPoint = "CredDeleteW", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CredDelete(string target, UInt32 type, UInt32 flags);

    public static void Write(string target, string value) {
        byte[] blob = Encoding.UTF8.GetBytes(value);
        IntPtr blobPointer = Marshal.AllocHGlobal(blob.Length);
        try {
            Marshal.Copy(blob, 0, blobPointer, blob.Length);
            CREDENTIAL credential = new CREDENTIAL();
            credential.Type = 1;
            credential.TargetName = target;
            credential.CredentialBlobSize = (UInt32)blob.Length;
            credential.CredentialBlob = blobPointer;
            credential.Persist = 2;
            credential.UserName = "youtube";
            if (!CredWrite(ref credential, 0)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally {
            Marshal.FreeHGlobal(blobPointer);
        }
    }

    public static void Delete(string target) {
        CredDelete(target, 1, 0);
    }
}
"@
Add-Type -TypeDefinition $credentialHelperSource
$credentialTarget = "master-duel-recorder-lite/smoke/" + [guid]::NewGuid().ToString("N")
$fakeCredentials = '{"client_id":"smoke-client","client_secret":"smoke-secret","refresh_token":"smoke-refresh","scope":"https://www.googleapis.com/auth/youtube.upload"}'
$previousCredentialTarget = $env:MDRL_YOUTUBE_CREDENTIAL_TARGET
try {
    [MdrlCredentialSmoke]::Write($credentialTarget, $fakeCredentials)
    $env:MDRL_YOUTUBE_CREDENTIAL_TARGET = $credentialTarget

    $accountOutput = & $resolvedExe youtube account
    if ($LASTEXITCODE -ne 0) {
        throw "youtube account failed to read smoke credential with exit code $LASTEXITCODE"
    }
    if (($accountOutput | Out-String) -notmatch "scope: https://www.googleapis.com/auth/youtube.upload") {
        throw "youtube account did not report connected credentials"
    }

    $disconnectOutput = & $resolvedExe youtube disconnect
    if ($LASTEXITCODE -ne 0) {
        throw "youtube disconnect failed with exit code $LASTEXITCODE"
    }
    if (($disconnectOutput | Out-String) -notmatch "OAuth") {
        throw "youtube disconnect did not report deletion"
    }

    $missingOutput = & $resolvedExe youtube account
    if ($LASTEXITCODE -eq 0) {
        throw "youtube account unexpectedly reported connected after disconnect"
    }
    if (($missingOutput | Out-String) -match "scope: https://www.googleapis.com/auth/youtube.upload") {
        throw "youtube account did not report missing credentials after disconnect"
    }
}
finally {
    $env:MDRL_YOUTUBE_CREDENTIAL_TARGET = $previousCredentialTarget
    [MdrlCredentialSmoke]::Delete($credentialTarget)
}

$defaultSmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mdrl-default-path-smoke-" + [guid]::NewGuid().ToString("N"))
$appPath = Join-Path $defaultSmokeRoot "app"
$localAppDataPath = Join-Path $defaultSmokeRoot "local-app-data"
$copiedExe = Join-Path $appPath "master-duel-recorder-lite.exe"
$previousLocalAppData = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $appPath -Force | Out-Null
Copy-Item -LiteralPath $resolvedExe -Destination $copiedExe
try {
    $env:LOCALAPPDATA = $localAppDataPath
    $defaultConfig = & $copiedExe config show --json
    if ($LASTEXITCODE -ne 0) {
        throw "default-path config show failed with exit code $LASTEXITCODE"
    }
    if (Test-Path -LiteralPath (Join-Path $appPath "user_data")) {
        throw "CLI smoke created user_data next to the EXE"
    }
    if (Test-Path -LiteralPath (Join-Path $localAppDataPath "MasterDuelRecorderLite")) {
        throw "read-only CLI smoke unexpectedly created runtime data"
    }
}
finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    Remove-Item -LiteralPath $defaultSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "EXE smoke passed: $resolvedExe"
