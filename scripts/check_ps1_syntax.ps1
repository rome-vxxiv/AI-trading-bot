# Parses every .ps1 file in the repo with the real PowerShell AST parser
# and fails if any file has a syntax error. Also flags non-ASCII
# characters, since Windows PowerShell 5.1 defaults to reading .ps1
# files without a BOM as the system codepage (not UTF-8), and every
# em-dash / smart-quote we've shipped has broken on a real machine even
# though it looked fine here.
#
# Run before shipping any .ps1 change:
#   pwsh -NoProfile -File scripts/check_ps1_syntax.ps1
# (or `powershell -File ...` on Windows - works on both editions)

param([string]$Dir = (Split-Path $PSScriptRoot -Parent))

$files = Get-ChildItem -Path $Dir -Filter "*.ps1" -Recurse | Where-Object { $_.FullName -notmatch '\.venv' }
$failed = 0

foreach ($f in $files) {
    $hasNonAscii = (Get-Content $f.FullName -Raw) -match '[^\x00-\x7f]'
    $errors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors) | Out-Null

    if ($errors.Count -gt 0 -or $hasNonAscii) {
        $failed++
        Write-Host "FAIL: $($f.FullName)" -ForegroundColor Red
        foreach ($e in $errors) {
            Write-Host "  Line $($e.Extent.StartLineNumber): $($e.Message)" -ForegroundColor Yellow
        }
        if ($hasNonAscii) {
            Write-Host "  Contains non-ASCII characters (em-dash, smart quotes, etc.) -- these break on Windows PowerShell 5.1 without a BOM." -ForegroundColor Yellow
        }
    } else {
        Write-Host "OK:   $($f.FullName)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "$($files.Count - $failed)/$($files.Count) files clean" -ForegroundColor Cyan
if ($failed -gt 0) { exit 1 }
exit 0
