param([string]$Name)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$log = "C:\Users\pk\uia.log"
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
$buttons = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
$names = @(); $hit = $null
foreach ($b in $buttons) { $n = $b.Current.Name; if ($n -like "*Yu*" -or $n -like "*install*" -or $n -like "*Forget*" -or $n -eq "Yes" -or $n -eq "No") { $names += $n }; if ($n -like $Name -and -not $hit) { $hit = $b } }
"buttons seen: " + ($names -join " | ") | Out-File $log -Append
if ($hit) { $r = $hit.Current.BoundingRectangle; "found [$Name] at $($r.X),$($r.Y) $($r.Width)x$($r.Height); invoking" | Out-File $log -Append; $hit.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "invoked at " + (Get-Date -Format o) | Out-File $log -Append } else { "NOT FOUND [$Name]" | Out-File $log -Append }
