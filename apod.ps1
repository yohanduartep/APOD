$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command py -ErrorAction SilentlyContinue

if ($Python) {
    & $Python.Source -3 "$ScriptDir\apod.py" @args
} else {
    & python "$ScriptDir\apod.py" @args
}

exit $LASTEXITCODE
