param([string]$Tag = "d04bcfb5")
$ErrorActionPreference = "Continue"
$z = "C:\Users\pk\Yulon-$Tag-windows-x64.zip"
$d = "C:\Users\pk\yulon-$Tag"
if (Test-Path C:\Users\pk\claude-say.ps1) { powershell -ExecutionPolicy Bypass -File C:\Users\pk\claude-say.ps1 "Swapping Yulon to the $Tag build from the fork's Actions" }
"zip bytes: " + (Get-Item $z).Length
Get-Process yulon -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
"yulon still running: " + [bool](Get-Process yulon -ErrorAction SilentlyContinue)
New-Item -ItemType Directory -Force $d | Out-Null
tar -xf $z -C $d
$exe = Get-ChildItem $d -Recurse -Filter yulon.exe | Select-Object -First 1 -ExpandProperty FullName
"exe: $exe"
powershell -ExecutionPolicy Bypass -File C:\Users\pk\bin\on-desktop.ps1 -Name "YulonRun$Tag" -Exe $exe
Start-Sleep -Seconds 15
Get-Process yulon -ErrorAction SilentlyContinue | Select-Object Id, SessionId, StartTime | Format-Table -AutoSize | Out-String -Width 100
Get-Content "$env:APPDATA\yulon\yulon.log" -Tail 3
