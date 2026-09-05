# Lane C -- 7.1 clause 14 driver: drive the real 3.3.5a client on the laptop and log in.
# Dot-source this, then call Shot / Focus / TypeText / Key.
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

if (-not ("W32" -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W32 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern short VkKeyScan(char ch);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
}

$OUT = "C:\Users\perzi\dml-phase7\pyplan\gates\7.1-client-login"

function WowProc { Get-Process -Name wow -ErrorAction SilentlyContinue | Select-Object -First 1 }

function Focus {
  $p = WowProc
  if (-not $p) { return $false }
  [void][W32]::ShowWindow($p.MainWindowHandle, 9)   # SW_RESTORE
  [void][W32]::SetForegroundWindow($p.MainWindowHandle)
  Start-Sleep -Milliseconds 700
  return ([W32]::GetForegroundWindow() -eq $p.MainWindowHandle)
}

function Shot($name) {
  $bmp = New-Object System.Drawing.Bitmap ([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width), ([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
  $path = Join-Path $OUT $name
  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  Write-Output "shot -> $path"
}

# scancode-based key press: WoW's login screen reads DirectInput-ish scancodes reliably
function KeyVk([int]$vk, [int]$holdMs = 40) {
  $sc = [W32]::MapVirtualKey([uint32]$vk, 0)
  [W32]::keybd_event([byte]$vk, [byte]$sc, 0x0008, [UIntPtr]::Zero)          # KEYEVENTF_SCANCODE
  Start-Sleep -Milliseconds $holdMs
  [W32]::keybd_event([byte]$vk, [byte]$sc, 0x0008 -bor 0x0002, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds $holdMs
}

function TypeText($text) {
  foreach ($ch in $text.ToCharArray()) {
    $vks = [W32]::VkKeyScan($ch)
    $vk = $vks -band 0xFF
    $shift = ($vks -shr 8) -band 1
    if ($shift) { $sc = [W32]::MapVirtualKey(0x10, 0); [W32]::keybd_event(0x10, [byte]$sc, 0x0008, [UIntPtr]::Zero) }
    KeyVk $vk 35
    if ($shift) { $sc = [W32]::MapVirtualKey(0x10, 0); [W32]::keybd_event(0x10, [byte]$sc, 0x000A, [UIntPtr]::Zero) }
  }
}

$VK_RETURN = 0x0D; $VK_TAB = 0x09; $VK_ESC = 0x1B; $VK_BACK = 0x08
