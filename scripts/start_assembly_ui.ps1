param([ValidateRange(1024,65535)][int]$Port=8765,[switch]$WithoutApi)
$ErrorActionPreference='Stop'
$assemblyRoot=Split-Path -Parent $PSScriptRoot
$assemblyPython=Join-Path $assemblyRoot 'runtime/luna-venv/Scripts/python.exe'
$assemblyEntry=Join-Path $assemblyRoot 'scripts/run_assembly.py'
$assemblyPreviousKey=[Environment]::GetEnvironmentVariable('OPENAI_API_KEY','Process')
try {
    if (-not $WithoutApi -and -not $assemblyPreviousKey) {
        $assemblySecureKey=Read-Host 'API key (hidden; this process only)' -AsSecureString
        $env:OPENAI_API_KEY=[System.Net.NetworkCredential]::new('', $assemblySecureKey).Password
        if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) { throw 'API key was empty.' }
    }
    if ($WithoutApi) { Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue }
    & $assemblyPython -B $assemblyEntry ui --port $Port
    if ($LASTEXITCODE -ne 0) { throw "UI exited with code $LASTEXITCODE" }
}
finally {
    [Environment]::SetEnvironmentVariable('OPENAI_API_KEY',$assemblyPreviousKey,'Process')
    if ($assemblySecureKey) { $assemblySecureKey.Dispose() }
    $assemblyPreviousKey=$null
}
