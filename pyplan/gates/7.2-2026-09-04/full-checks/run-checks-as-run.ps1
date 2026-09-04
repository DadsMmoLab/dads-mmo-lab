# Full checks for the 7.2 gate line, run exactly as .github/workflows/ci.yml runs them.
$root = 'C:/gate/checks-badee625'
$py   = 'C:/Users/pk/co/dads-mmo-lab-yulon-phase7/pylauncher/.venv/Scripts/python.exe'
$pl   = Join-Path $root 'pylauncher'
$out  = Join-Path $root 'out2'
$tmp  = Join-Path $root 'pytesttmp2'
New-Item -ItemType Directory -Force $out | Out-Null
New-Item -ItemType Directory -Force $tmp | Out-Null
Set-Location $pl

$plan = @(
  @{ n='ruff';          f='ruff.txt';         a=@('-m','ruff','check','.');                              ci='python -m ruff check .' },
  @{ n='black';         f='black.txt';        a=@('-m','black','--check','.');                           ci='python -m black --check .' },
  @{ n='mypy-native';   f='mypy-native.txt';  a=@('-m','mypy','yulon','main.py');                        ci='python -m mypy yulon main.py' },
  @{ n='mypy-win32';    f='mypy-win32.txt';   a=@('-m','mypy','--platform','win32','yulon','main.py');   ci='python -m mypy --platform win32 yulon main.py' },
  @{ n='mypy-darwin';   f='mypy-darwin.txt';  a=@('-m','mypy','--platform','darwin','yulon','main.py');  ci='python -m mypy --platform darwin yulon main.py' },
  @{ n='pytest';        f='pytest.txt';       a=@('-m','pytest','-q','-m','not integration','--basetemp',$tmp); ci='python -m pytest -q -m "not integration"' },
  @{ n='pytest-integration'; f='pytest-integration.txt'; a=@('-m','pytest','-q','-m','integration','--basetemp',$tmp); ci='python -m pytest -v --durations=0 -m integration  (CI runs this as a SEPARATE job, on a runner whose only Docker workload is the test)' }
)

$summary = @()
$summary += "full checks for the 7.2 gate line"
$summary += "host        : $env:COMPUTERNAME"
$summary += "tree        : $pl  (git archive of badee6255965f2ba8e05ad2306dcdc6126e5880b)"
$summary += "interpreter : $py"
$summary += "started UTC : " + (Get-Date).ToUniversalTime().ToString('o')
$summary += ""

foreach ($step in $plan) {
  $f = Join-Path $out $step.f
  $hdr = @()
  $hdr += "=== $($step.n) ==="
  $hdr += "host          : $env:COMPUTERNAME"
  $hdr += "tree          : $pl (git archive of badee6255965f2ba8e05ad2306dcdc6126e5880b)"
  $hdr += "interpreter   : $py"
  $hdr += "CI spells it  : $($step.ci)"
  $hdr += "argv here     : python " + ($step.a -join ' ')
  if ($step.n -like 'pytest*') {
    $hdr += "note          : --basetemp added because under schtasks the default basetemp"
    $hdr += "                'C:\Users\pk\AppData\Local\Temp\pytest-of-pk' is not readable by the"
    $hdr += "                task (PermissionError WinError 5). Harness accommodation, nothing else."
  }
  $hdr += "started UTC   : " + (Get-Date).ToUniversalTime().ToString('o')
  $hdr += "---"
  $hdr | Out-File -FilePath $f -Encoding utf8
  $t0 = Get-Date
  & $py $step.a 2>&1 | Out-File -FilePath $f -Append -Encoding utf8
  $rc = $LASTEXITCODE
  $el = ((Get-Date) - $t0).TotalSeconds
  ("--- exit code: {0}   elapsed: {1:N1}s   finished UTC: {2}" -f $rc, $el, (Get-Date).ToUniversalTime().ToString('o')) | Out-File -FilePath $f -Append -Encoding utf8
  $summary += ("{0,-20} exit {1}   {2,8:N1}s   {3}" -f $step.n, $rc, $el, $step.f)
}
$summary += ""
$summary += "finished UTC: " + (Get-Date).ToUniversalTime().ToString('o')
$summary | Out-File -FilePath (Join-Path $out 'SUMMARY.txt') -Encoding utf8
