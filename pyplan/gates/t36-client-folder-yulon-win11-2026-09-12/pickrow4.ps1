param([string]$Prefix = "[keg")
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class M36 { [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@
$log = "C:\Users\pk\uia.log"
$root = [System.Windows.Automation.AutomationElement]::RootElement
$app = $null; foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) { if ($w.Current.Name -like "Yu*lon*") { $app = $w } }
[void][M36]::SetForegroundWindow([IntPtr]$app.Current.NativeWindowHandle); Start-Sleep -Milliseconds 400
$list = $app.FindFirst([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::List)))
$first = $null
foreach ($li in $app.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ListItem)))) { $rr = $li.Current.BoundingRectangle; if ($li.Current.Name -like "\[*" -and $rr.Height -gt 0 -and $rr.Y -gt 300) { $first = $li; break } }
if (-not $first) { "no module row on screen" | Out-File $log -Append; exit 1 }
"first row [" + $first.Current.Name.Substring(0,20) + "] rect " + $first.Current.BoundingRectangle.X + "," + $first.Current.BoundingRectangle.Y | Out-File $log -Append
$r = $first.Current.BoundingRectangle; $x=[int]($r.X + 40); $y=[int]($r.Y + $r.Height/2)
[void][M36]::SetCursorPos($x,$y); Start-Sleep -Milliseconds 200; [M36]::mouse_event(2,0,0,0,[IntPtr]::Zero); [M36]::mouse_event(4,0,0,0,[IntPtr]::Zero)
"clicked first row at $x,$y" | Out-File $log -Append
Start-Sleep -Milliseconds 500
[System.Windows.Forms.SendKeys]::SendWait($Prefix); Start-Sleep -Milliseconds 900
$sel = $null; try { $sel = $list.GetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection() } catch {}
"selection after type-ahead: " + $(if ($sel) { ($sel | ForEach-Object { $_.Current.Name.Substring(0, [Math]::Min(70, $_.Current.Name.Length)) }) -join " | " } else { "(unreadable)" }) | Out-File $log -Append
