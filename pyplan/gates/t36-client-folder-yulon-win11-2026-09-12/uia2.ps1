param([string]$Name, [string]$Type = "Button", [int]$Index = 0)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$log = "C:\Users\pk\uia.log"
$root = [System.Windows.Automation.AutomationElement]::RootElement
$ct = switch ($Type) { "Button" { [System.Windows.Automation.ControlType]::Button } "TabItem" { [System.Windows.Automation.ControlType]::TabItem } default { $null } }
$cond = if ($ct) { New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ct) } else { [System.Windows.Automation.Condition]::TrueCondition }
$scope = $root; foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) { if ($w.Current.Name -like "Yu*lon*") { $scope = $w } }
$all = $scope.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
$hits = @(); foreach ($e in $all) { if ($e.Current.Name -like $Name) { $hits += $e } }
"[$Type/$Name] hits: " + $hits.Count + " -> " + (($hits | ForEach-Object { $_.Current.Name }) -join " | ") | Out-File $log -Append
if ($hits.Count -le $Index) { "NOT FOUND [$Name] index $Index" | Out-File $log -Append; exit 1 }
$hit = $hits[$Index]; $r = $hit.Current.BoundingRectangle
"chose [$($hit.Current.Name)] at $($r.X),$($r.Y) $($r.Width)x$($r.Height)" | Out-File $log -Append
try { $hit.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "invoked at " + (Get-Date -Format o) | Out-File $log -Append }
catch { try { $hit.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); "selected at " + (Get-Date -Format o) | Out-File $log -Append } catch { "no invoke/select pattern: $($_.Exception.Message)" | Out-File $log -Append } }
