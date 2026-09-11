param([int]$X, [int]$Y, [string]$Keys = "")
Add-Type @"
using System; using System.Runtime.InteropServices;
public class M32 {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
}
"@
Add-Type -AssemblyName System.Windows.Forms
$log = "C:\Users\pk\click.log"
if ($X -gt 0 -or $Y -gt 0) {
  [void][M32]::SetCursorPos($X, $Y); Start-Sleep -Milliseconds 150
  [M32]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero); [M32]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero)
  "clicked $X,$Y at " + (Get-Date -Format o) | Out-File $log -Append
}
if ($Keys) { Start-Sleep -Milliseconds 300; [System.Windows.Forms.SendKeys]::SendWait($Keys); "sent [$Keys] at " + (Get-Date -Format o) | Out-File $log -Append }
