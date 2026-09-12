param([string]$Title = "*client folder*")
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class M35 { [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@
$log = "C:\Users\pk\uia.log"
$root = [System.Windows.Automation.AutomationElement]::RootElement
$app = $null; foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) { if ($w.Current.Name -like "Yu*lon*") { $app = $w } }
$dlg = $null; foreach ($d in $app.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)))) { if ($d.Current.Name -like $Title) { $dlg = $d } }
if (-not $dlg) { "no dialog" | Out-File $log -Append; exit 0 }
$r = $dlg.Current.BoundingRectangle
"dialog rect $($r.X),$($r.Y) $($r.Width)x$($r.Height)" | Out-File $log -Append
[void][M35]::SetForegroundWindow([IntPtr]$dlg.Current.NativeWindowHandle); Start-Sleep -Milliseconds 300
# Select Folder sits left of Cancel on the bottom row: measured on the frame at ~77% of the width, ~95% of the height
$x = [int]($r.X + $r.Width * 0.72); $y = [int]($r.Y + $r.Height * 0.94)
[void][M35]::SetCursorPos($x, $y); Start-Sleep -Milliseconds 200; [M35]::mouse_event(2,0,0,0,[IntPtr]::Zero); [M35]::mouse_event(4,0,0,0,[IntPtr]::Zero)
"clicked Select Folder at $x,$y at " + (Get-Date -Format o) | Out-File $log -Append
