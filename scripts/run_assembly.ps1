param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [ValidateSet('run','prepare')][string]$Action='run',
    [ValidateSet('S0','S1','R0','R1')][string]$Condition='S0',
    [ValidateSet('scripted','astra')][string]$Policy='scripted',
    [string]$OutputPath,
    [string]$LlmConfig,
    [ValidateRange(5,1800)][int]$Seconds=300,
    [ValidateSet('mm','cm','m','inch')][string]$Unit,
    [switch]$AssembledObj,
    [string]$Distribution='ASAP-Ubuntu'
)
$ErrorActionPreference='Stop'
$assemblyRoot=Split-Path -Parent $PSScriptRoot
function Convert-AssemblyPath([string]$Value) {
    $assemblyPath=[System.IO.Path]::GetFullPath($Value)
    if ($assemblyPath -match '^(?i:E:|\\\\)') { throw 'This integration does not access E: or UNC paths.' }
    $converted=& wsl -d $Distribution -u root --exec wslpath -a $assemblyPath
    if ($LASTEXITCODE -ne 0) { throw 'Cannot convert the configured path for WSL.' }
    return $converted.Trim()
}
$assemblyLinuxRoot=Convert-AssemblyPath $assemblyRoot
$assemblyInput=Convert-AssemblyPath $InputPath
$assemblyArgs=@('-B','scripts/run_assembly.py',$Action,$assemblyInput,'--bridge-config','config/disassembly_bridge.wsl.example.json')
if ($Unit) { $assemblyArgs+=@('--unit',$Unit) }
if ($AssembledObj) { $assemblyArgs+='--assembled-obj' }
if ($OutputPath) { $assemblyArgs+=@('--output',(Convert-AssemblyPath $OutputPath)) }
if ($Action -eq 'run') {
    $assemblyArgs+=@('--condition',$Condition,'--policy',$Policy,'--seconds',$Seconds.ToString())
    if ($Policy -eq 'astra' -and -not $LlmConfig) { throw 'Astra needs an explicit approved -LlmConfig file.' }
    if ($LlmConfig) { $assemblyArgs+=@('--llm-config',(Convert-AssemblyPath $LlmConfig)) }
}
$assemblySavedWslEnv=$env:WSLENV
try {
    # Only the variable NAME enters argv. The key is never printed or written.
    if ($Policy -eq 'astra') {
        $assemblyKeyName='OPENAI_API_KEY'
        $assemblyConfigText=Get-Content -LiteralPath $LlmConfig -Raw
        if ($assemblyConfigText -match '(?m)^api_key_env\s*=\s*["'']([A-Z][A-Z0-9_]+)["'']') { $assemblyKeyName=$Matches[1] }
        $env:WSLENV=(@($assemblySavedWslEnv,$assemblyKeyName) | Where-Object { $_ }) -join ':'
    }
    & wsl -d $Distribution -u root --cd $assemblyLinuxRoot --exec env MUJOCO_GL=egl runtime/linux-venv/bin/python @assemblyArgs
    $assemblyExit=$LASTEXITCODE
} finally { $env:WSLENV=$assemblySavedWslEnv }
exit $assemblyExit
