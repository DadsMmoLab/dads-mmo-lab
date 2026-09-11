# T34 — "Forget this install…" pressed on the two stale tabs of `yulon-win11`

The box: `yulon-win11` (Hyper-V, Windows 11, 1920×1080), the fork's Actions build of `yulon-phase8b` at `3a124f40` (run 34650994644, `Yulon-yulon-phase8b-windows-x64.zip`, 56,347,711 bytes) swapped in for `v0.8.0-Public` with `swap-build.ps1` and launched into the desktop session by `on-desktop.ps1`. Two WotLK install records from 2026-08-28 whose folders and containers no longer exist opened as tabs. Every text capture carries the VM's own `Get-Date -Format o` stamp; frames are Hyper-V thumbnails from the host (`vmshot.ps1`, 1024×768 with the 16:9 screen letterboxed).

| Step | File | What it shows |
|---|---|---|
| the stale tab with the new button | `frame-01-stale-tab-with-forget-button.png` | the second record's Server tab: "could not be asked — docker did not answer about this container", Uninstall…, and "Forget this install…" beneath |
| a synthetic mouse click did nothing | `02-click-forget.txt`, `frame-02-after-forget-click.png`, `frame-03-second-click.png` | `click.ps1` (`SetCursorPos` + `mouse_event`) at 1012,491 — inside the button's rectangle UIA later reported (441,478 1140×26) — twice, no dialog; a deviation of the driver, not of the app: the press was then made through UI Automation's `InvokePattern`, which is a real press of the same `QPushButton` |
| the press and its confirmation | `03-uia-forget.txt`, `frame-04-uia-forget.png` | `uia.ps1` finds the button by name and invokes it at 23:55:29; the dialog "Forget this install?" with the ticket's text, No as the default |
| Yes, and the record gone | `04-yes-and-state.txt`, `frame-05-after-yes.png` | Yes invoked at 23:56:12; `state.json` read back with ONE install left (`C:\Users\pk\wow-server-playerbots`); the tab closed, the other tab now in front |
| the second tab, the same way | `05-second-tab.txt`, `frame-06-after-second-forget.png` | Forget invoked 23:56:46, Yes 23:56:59; `state.json` reads `"installs": []`; only the Catalog tab remains |

Not pressed here: the Uninstall refusal's new clause (the refusal appears only after "Uninstall…", which was not pressed on these tabs), and the missing-folder Docker sentence (Half A) — that path is unit-proven; the log of the earlier press on `v0.8.0-Public` (22:38:17, the ticket) is the before.
