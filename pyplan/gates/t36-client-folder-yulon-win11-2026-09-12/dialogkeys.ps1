param([string]$Title = "*client folder*", [string]$Path)
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W33 { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string t); }
"@
$log = "C:\Users\pk\uia.log"
$root = [System.Windows.Automation.AutomationElement]::RootElement
$wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$all = @(); $dlg = $null; $app = $null; foreach ($w in $wins) { $all += $w.Current.Name; if ($w.Current.Name -like $Title -and -not $dlg) { $dlg = $w }; if ($w.Current.Name -like "Yu*lon*") { $app = $w } }
if (-not $dlg -and $app) { foreach ($d in $app.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)))) { "app child window: " + $d.Current.Name | Out-File $log -Append; if ($d.Current.Name -like $Title) { $dlg = $d } } }
"windows: " + ($all -join " | ") | Out-File $log -Append
"dialog found: " + [bool]$dlg | Out-File $log -Append
if ($dlg) {
  $kids = $dlg.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $names = @(); foreach ($k in $kids) { $n=$k.Current.Name; $t=$k.Current.ControlType.ProgrammaticName; if ($n -and ($t -like "*Edit*" -or $t -like "*Button*" -or $t -like "*ComboBox*")) { $names += "$t=$n" } }
  "dialog controls: " + ($names -join " | ") | Out-File $log -Append
  $h = [IntPtr]$dlg.Current.NativeWindowHandle
  "foreground: " + [W33]::SetForegroundWindow($h) | Out-File $log -Append
  Start-Sleep -Milliseconds 400
  [System.Windows.Forms.SendKeys]::SendWait("%d"); Start-Sleep -Milliseconds 300
  [System.Windows.Forms.SendKeys]::SendWait($Path + "{ENTER}"); Start-Sleep -Milliseconds 1500
  "typed path into the address bar at " + (Get-Date -Format o) | Out-File $log -Append
  [System.Windows.Forms.SendKeys]::SendWait("%s"); Start-Sleep -Milliseconds 800
  "sent Alt+S (Select Folder) at " + (Get-Date -Format o) | Out-File $log -Append
}
