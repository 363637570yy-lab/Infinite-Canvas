[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Repository = "."
)

$ErrorActionPreference = "Stop"

$root = (& git -C $Repository rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $root) {
    throw "Repository is not inside a Git worktree: $Repository"
}
$root = [System.IO.Path]::GetFullPath($root.Trim())

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = "git"
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.WorkingDirectory = $root
$startInfo.Arguments = "ls-files -z --cached --others --exclude-standard"

$process = [System.Diagnostics.Process]::Start($startInfo)
$output = [System.IO.MemoryStream]::new()
$process.StandardOutput.BaseStream.CopyTo($output)
$errorText = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    throw "git ls-files failed: $errorText"
}

$relativePaths = [System.Text.Encoding]::UTF8.GetString($output.ToArray()).Split(
    [char]0,
    [System.StringSplitOptions]::RemoveEmptyEntries
)
[System.Array]::Sort($relativePaths, [System.StringComparer]::Ordinal)

$hash = [System.Security.Cryptography.IncrementalHash]::CreateHash(
    [System.Security.Cryptography.HashAlgorithmName]::SHA256
)
$hash.AppendData([System.Text.Encoding]::UTF8.GetBytes("infinite-canvas-worktree-code-hash-v1`0"))

$buffer = [byte[]]::new(65536)
$rootPrefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
foreach ($relativePath in $relativePaths) {
    $normalizedPath = $relativePath.Replace("\", "/")
    $pathBytes = [System.Text.Encoding]::UTF8.GetBytes($normalizedPath)
    $hash.AppendData([System.BitConverter]::GetBytes([int]$pathBytes.Length))
    $hash.AppendData($pathBytes)

    $absolutePath = [System.IO.Path]::GetFullPath((Join-Path $root $relativePath))
    if (-not $absolutePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Git returned a path outside the worktree: $relativePath"
    }

    if (-not [System.IO.File]::Exists($absolutePath)) {
        $hash.AppendData([byte[]]@(0))
        continue
    }

    $hash.AppendData([byte[]]@(1))
    $stream = [System.IO.File]::OpenRead($absolutePath)
    try {
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $hash.AppendData($buffer, 0, $read)
        }
    }
    finally {
        $stream.Dispose()
    }
}

([System.BitConverter]::ToString($hash.GetHashAndReset()) -replace "-", "").ToLowerInvariant()
