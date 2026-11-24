param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [ValidateSet('run','prepare')][string]$Action='run',
    [ValidateSet('S0','S1','R0','R1')][string]$Condition='R0',
    [ValidateSet('scripted','astra')][string]$Policy='astra',
    [string]$OutputPath,
    [string]$LlmConfig,
    [string]$PythonPath,
    [ValidateRange(1,1800)][int]$Seconds=300,
    [ValidateSet('mm','cm','m','inch')][string]$Unit,
    [switch]$AssembledObj
)
$ErrorActionPreference='Stop'
$assemblyRoot=Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath=Join-Path $assemblyRoot 'runtime\luna-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath=Join-Path $assemblyRoot '.venv\Scripts\python.exe' }
}
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Project Python environment is missing. See README setup.' }
$assemblyInput=[System.IO.Path]::GetFullPath($InputPath)
if ($assemblyInput -match '^(?i:E:|\\\\)') { throw 'This project does not access E: or UNC paths.' }
$assemblyArgs=@('-B',(Join-Path $PSScriptRoot 'run_assembly.py'),$Action,'--input',$assemblyInput,'--seconds',$Seconds.ToString())
if ($OutputPath) { $assemblyArgs+=@('--output',[System.IO.Path]::GetFullPath($OutputPath)) }
if ($Unit) { $assemblyArgs+=@('--unit',$Unit) }
if ($AssembledObj) { $assemblyArgs+='--assembled-obj' }
if ($Action -eq 'run') {
    $assemblyArgs+=@('--condition',$Condition,'--policy',$Policy)
    if ($LlmConfig) { $assemblyArgs+=@('--llm-config',[System.IO.Path]::GetFullPath($LlmConfig)) }
}
# The key remains in the existing process environment; never copied into argv.
& $PythonPath @assemblyArgs
exit $LASTEXITCODE
