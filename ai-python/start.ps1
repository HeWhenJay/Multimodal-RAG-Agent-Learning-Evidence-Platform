$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$entryPath = Join-Path $PSScriptRoot 'run.py'
& conda run --no-capture-output -n learning-evidence-rag python -B $entryPath @args
exit $LASTEXITCODE
