try {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile('install.ps1', [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        foreach ($e in $errors) {
            Write-Host ("ERROR at line " + $e.Extent.StartLineNumber + ": " + $e.Message) -ForegroundColor Red
        }
    } else {
        Write-Host "SYNTAX OK" -ForegroundColor Green
    }
} catch {
    Write-Host ("Parse exception: " + $_.ToString()) -ForegroundColor Red
}
