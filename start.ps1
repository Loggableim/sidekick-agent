param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList
)

python -m sidekick_app @ArgsList
