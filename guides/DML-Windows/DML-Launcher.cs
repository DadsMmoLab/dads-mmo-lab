// DML-Launcher.cs -- Dad's MMO Lab system tray launcher
// Compiled at install time: csc.exe /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

class DmlLauncherEntry
{
    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.ThreadException += delegate(object s, ThreadExceptionEventArgs e) { };
        SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
        Application.Run(new TrayApp());
    }
}

class TrayApp : ApplicationContext
{
    const string DISTRO   = "dml-arch";
    const string VERSION  = "2.1.2";

    enum ServerDisplayState { Stopped, Running, Loading }

    string TrayTooltip(bool serverActive)
    {
        return serverActive
            ? "DML Launcher v" + VERSION + " — Server Active"
            : "DML Launcher v" + VERSION;
    }

    string TitlesCachePath {
        get {
            return System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
                "dml-titles.cache");
        }
    }

    string StoppedMarkerPath {
        get {
            return System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
                ".dml-servers-stopped");
        }
    }

    bool ServersIntentionallyStopped()
    {
        try { return System.IO.File.Exists(StoppedMarkerPath); }
        catch { return false; }
    }

    // After stop, tray/doctor must not boot WSL for status — that leaves VmmemWSL ~2 GB idle.
    string GetStatusOutput()
    {
        if (ServersIntentionallyStopped() || !IsDistroRunning())
            return BuildStoppedStatusOutput();
        return WslRun("dml status");
    }

    void MaybeReReleaseWsl()
    {
        if (ServersIntentionallyStopped() && IsDistroRunning())
            TriggerReleaseWsl(0);
    }

    // True only when dml-arch is Running — wsl -l -v does NOT boot the distro.
    static bool IsDistroRunning()
    {
        try
        {
            var psi = new ProcessStartInfo();
            psi.FileName               = "wsl.exe";
            psi.Arguments              = "-l -v";
            psi.UseShellExecute        = false;
            psi.RedirectStandardOutput = true;
            psi.CreateNoWindow         = true;
            // wsl -l -v emits UTF-16 LE; UTF-8 decoding breaks "Running" matching
            psi.StandardOutputEncoding = Encoding.Unicode;
            using (var p = Process.Start(psi))
            {
                string output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(5000);
                foreach (var line in output.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string trimmed = line.Trim();
                    if (trimmed.StartsWith(DISTRO, StringComparison.OrdinalIgnoreCase)
                        || trimmed.StartsWith("* " + DISTRO, StringComparison.OrdinalIgnoreCase))
                        return trimmed.IndexOf("Running", StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
        }
        catch { }
        return false;
    }

    void SaveTitleCache(System.Collections.Generic.IEnumerable<string> titles)
    {
        try
        {
            System.IO.File.WriteAllLines(TitlesCachePath, titles);
        }
        catch { }
    }

    string[] LoadTitleCache()
    {
        try
        {
            if (System.IO.File.Exists(TitlesCachePath))
                return System.IO.File.ReadAllLines(TitlesCachePath);
        }
        catch { }
        return new string[0];
    }

    string BuildStoppedStatusOutput()
    {
        var titles = LoadTitleCache();
        if (titles.Length == 0) return "";
        var lines = new System.Collections.Generic.List<string>();
        foreach (var t in titles)
        {
            string title = (t ?? "").Trim();
            if (title.Length > 0) lines.Add(title + ":stopped");
        }
        return string.Join("\n", lines);
    }

    // Prevents Windows from sleeping while a server is running.
    // ES_CONTINUOUS makes the state persist until explicitly released.
    // ES_SYSTEM_REQUIRED blocks sleep without requiring the display to stay on.
    [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint esFlags);
    const uint ES_CONTINUOUS      = 0x80000000;
    const uint ES_SYSTEM_REQUIRED = 0x00000001;

    NotifyIcon _tray;
    ContextMenuStrip _menu;
    System.Windows.Forms.Timer _menuTimer;
    System.Windows.Forms.Timer _loadingAnimTimer;
    Form _syncForm;
    string _lastStatusOut = "";
    int _loadingDotFrame;
    readonly Dictionary<string, string> _pendingTitleStatus =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    HashSet<string> _manageScriptTitles =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    const int WslQuickTimeoutMs = 15000;
    const int WslLongTimeoutMs  = 600000;

    static readonly Color ColorRunning = Color.FromArgb(30, 160, 60);
    static readonly Color ColorStopped = Color.FromArgb(110, 110, 110);
    static readonly Color ColorLoading = Color.FromArgb(200, 150, 0);

    public TrayApp()
    {
        // Hidden form gives a stable UI thread marshal target (ApplicationContext alone can leave _uiSync null).
        _syncForm = new Form();
        _syncForm.FormBorderStyle = FormBorderStyle.None;
        _syncForm.ShowInTaskbar   = false;
        _syncForm.StartPosition   = FormStartPosition.Manual;
        _syncForm.Size            = new Size(0, 0);
        _syncForm.Opacity         = 0;
        _syncForm.Show();

        _tray = new NotifyIcon();
        string icoPath = System.IO.Path.Combine(
            System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
            "dml.ico");
        _tray.Icon = System.IO.File.Exists(icoPath) ? new Icon(icoPath) : SystemIcons.Application;
        _tray.Text    = TrayTooltip(false);
        _tray.Visible = true;

        _menu = new ContextMenuStrip();
        _menu.Closed += OnMenuClosed;
        // Win11: show menu only on right-click (not ContextMenuStrip — avoids left-click open)
        _tray.MouseUp += OnTrayMouseUp;

        // Re-release if something woke WSL after an intentional stop (doctor, old tray poll).
        MaybeReReleaseWsl();

        // Check server state at startup so sleep is blocked immediately
        // if a server is already running when the tray loads.
        var startupTimer = new System.Windows.Forms.Timer { Interval = 3000 };
        startupTimer.Tick += delegate {
            startupTimer.Stop(); startupTimer.Dispose();
            string[] r = { null };
            var pollTimer = new System.Windows.Forms.Timer { Interval = 150 };
            pollTimer.Tick += delegate {
                if (r[0] == null) return;
                pollTimer.Stop(); pollTimer.Dispose();
                ApplyStatusResult(r[0]);
            };
            pollTimer.Start();
            System.Threading.ThreadPool.QueueUserWorkItem(_ => {
                try { r[0] = GetStatusOutput(); }
                catch { r[0] = BuildStoppedStatusOutput(); }
            });
        };
        startupTimer.Start();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            try { if (_syncForm != null) { _syncForm.Close(); _syncForm.Dispose(); } } catch { }
        }
        base.Dispose(disposing);
    }

    void PostToUi(Action action)
    {
        if (action == null) return;
        try
        {
            if (_syncForm != null && !_syncForm.IsDisposed)
            {
                if (_syncForm.InvokeRequired)
                    _syncForm.BeginInvoke(action);
                else
                    action();
                return;
            }
        }
        catch { }
        try { action(); } catch { }
    }

    void DeferCloseMenu()
    {
        PostToUi(delegate {
            try { if (_menu != null && _menu.Visible) _menu.Close(); } catch { }
        });
    }

    // Blocks or releases Windows sleep based on how many servers are running.
    void UpdateSleepLock(int runningCount)
    {
        if (runningCount > 0)
        {
            SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED);
            _tray.Text = TrayTooltip(true);
        }
        else
        {
            SetThreadExecutionState(ES_CONTINUOUS);  // release
            _tray.Text = TrayTooltip(false);
        }
    }

    static int CountRunning(string statusOut)
    {
        int count = 0;
        foreach (var line in statusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            int colon = line.Trim().IndexOf(':');
            if (colon <= 0) continue;
            string state = line.Trim().Substring(colon + 1).Trim();
            if (state.Equals("running", StringComparison.OrdinalIgnoreCase)
                || state.Equals("loading", StringComparison.OrdinalIgnoreCase))
                count++;
        }
        return count;
    }

    void OnTrayMouseUp(object sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Right) return;
        try
        {
            if (_menu == null || _menu.IsDisposed) return;
            if (_menu.Visible)
            {
                _menu.Close();
                return;
            }
            try
            {
                PopulateMenu(_menu);
            }
            catch
            {
                _menu.Items.Clear();
                var err = new ToolStripMenuItem("DML Launcher");
                err.Enabled = false;
                _menu.Items.Add(err);
                _menu.Items.Add(new ToolStripSeparator());
                AddStaticItems(_menu);
            }
            _menu.Show(Cursor.Position);
        }
        catch { }
    }

    void OnMenuClosed(object sender, ToolStripDropDownClosedEventArgs e)
    {
        if (_menuTimer != null)
        {
            _menuTimer.Stop();
            _menuTimer.Dispose();
            _menuTimer = null;
        }
        if (_loadingAnimTimer != null && _pendingTitleStatus.Count == 0)
        {
            _loadingAnimTimer.Stop();
            _loadingAnimTimer.Dispose();
            _loadingAnimTimer = null;
        }
    }

    string LoadingDots()
    {
        int n = (_loadingDotFrame % 3) + 1;
        return new string('.', n);
    }

    string FormatTitleText(string title, ServerDisplayState state)
    {
        switch (state)
        {
            case ServerDisplayState.Running:
                return title + "  \u25cf Running";
            case ServerDisplayState.Loading:
                return title + "  \u25cc Loading" + LoadingDots();
            default:
                return title + "  \u25cb Stopped";
        }
    }

    Color ColorForState(ServerDisplayState state)
    {
        switch (state)
        {
            case ServerDisplayState.Running: return ColorRunning;
            case ServerDisplayState.Loading: return ColorLoading;
            default: return ColorStopped;
        }
    }

    ServerDisplayState GetDisplayState(string title, string reportedStatus)
    {
        if (string.Equals(reportedStatus, "stopped", StringComparison.OrdinalIgnoreCase))
            return ServerDisplayState.Stopped;

        if (string.Equals(reportedStatus, "loading", StringComparison.OrdinalIgnoreCase))
            return ServerDisplayState.Loading;

        string expected;
        if (_pendingTitleStatus.TryGetValue(title, out expected))
        {
            if (!string.Equals(reportedStatus, expected, StringComparison.OrdinalIgnoreCase))
                return ServerDisplayState.Loading;
        }
        return string.Equals(reportedStatus, "running", StringComparison.OrdinalIgnoreCase)
            ? ServerDisplayState.Running : ServerDisplayState.Stopped;
    }

    void ApplyTitleActionEnabled(ToolStripMenuItem start, ToolStripMenuItem restart,
        ToolStripMenuItem stop, ServerDisplayState state)
    {
        bool running = state == ServerDisplayState.Running;
        bool loading = state == ServerDisplayState.Loading;
        start.Enabled   = !running && !loading;
        restart.Enabled =  running && !loading;
        stop.Enabled    =  running || loading;
    }

    void RefreshManageScriptCache()
    {
        var titles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (!IsDistroRunning())
        {
            _manageScriptTitles = titles;
            return;
        }
        try
        {
            string output = WslRun(
                "for d in \"$HOME\"/*/; do [ -f \"${d}wow-manage.sh\" ] && basename \"$d\"; done");
            foreach (var line in output.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string title = line.Trim();
                if (title.Length > 0) titles.Add(title);
            }
        }
        catch { }
        _manageScriptTitles = titles;
    }

    bool TitleHasManageScript(string title)
    {
        return _manageScriptTitles != null && _manageScriptTitles.Contains(title);
    }

    void SyncPendingWithStatus(string statusOut)
    {
        var map = ParseStatusMap(statusOut);
        var done = new System.Collections.Generic.List<string>();
        foreach (var kv in _pendingTitleStatus)
        {
            string reported;
            if (!map.TryGetValue(kv.Key, out reported)) continue;
            if (string.Equals(reported, "stopped", StringComparison.OrdinalIgnoreCase)
                || (string.Equals(reported, kv.Value, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(reported, "loading", StringComparison.OrdinalIgnoreCase)))
                done.Add(kv.Key);
        }
        foreach (var t in done) _pendingTitleStatus.Remove(t);
        if (_pendingTitleStatus.Count == 0 && _loadingAnimTimer != null)
        {
            _loadingAnimTimer.Stop();
            _loadingAnimTimer.Dispose();
            _loadingAnimTimer = null;
        }
    }

    void MarkTitlePending(string title, string expectedStatus)
    {
        _pendingTitleStatus[title] = expectedStatus;
        EnsureLoadingAnimTimer();
    }

    void MarkAllTitlesPending(string expectedStatus)
    {
        foreach (var line in _lastStatusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string trimmed = line.Trim();
            int colon = trimmed.IndexOf(':');
            if (colon > 0)
                _pendingTitleStatus[trimmed.Substring(0, colon)] = expectedStatus;
        }
        if (_pendingTitleStatus.Count == 0)
        {
            foreach (var t in LoadTitleCache())
            {
                string title = (t ?? "").Trim();
                if (title.Length > 0) _pendingTitleStatus[title] = expectedStatus;
            }
        }
        EnsureLoadingAnimTimer();
    }

    void EnsureLoadingAnimTimer()
    {
        if (_loadingAnimTimer != null) return;
        _loadingAnimTimer = new System.Windows.Forms.Timer { Interval = 450 };
        _loadingAnimTimer.Tick += delegate {
            _loadingDotFrame++;
            if (_menu != null && _menu.Visible && _pendingTitleStatus.Count > 0)
                UpdateTitleRowsInOpenMenu(_lastStatusOut);
        };
        _loadingAnimTimer.Start();
    }

    void UpdateTitleRowsInOpenMenu(string statusOut)
    {
        if (_menu == null || _menu.IsDisposed || !_menu.Visible) return;
        var statusMap = ParseStatusMap(statusOut);
        PostToUi(delegate {
            try
            {
                if (_menu == null || _menu.IsDisposed || !_menu.Visible) return;
                foreach (ToolStripItem item in _menu.Items)
                {
                    string title = item.Tag as string;
                    if (string.IsNullOrEmpty(title)) continue;
                    var gameMenu = item as ToolStripMenuItem;
                    if (gameMenu == null) continue;
                    string reported;
                    if (!statusMap.TryGetValue(title, out reported)) reported = "stopped";
                    var state = GetDisplayState(title, reported);
                    gameMenu.Text = FormatTitleText(title, state);
                    gameMenu.ForeColor = ColorForState(state);
                    if (gameMenu.DropDownItems.Count >= 3)
                        ApplyTitleActionEnabled(
                            gameMenu.DropDownItems[0] as ToolStripMenuItem,
                            gameMenu.DropDownItems[1] as ToolStripMenuItem,
                            gameMenu.DropDownItems[2] as ToolStripMenuItem,
                            state);
                }
            }
            catch { }
        });
    }

    Dictionary<string, string> ParseStatusMap(string statusOut)
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in statusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string trimmed = line.Trim();
            int colon = trimmed.IndexOf(':');
            if (colon > 0)
                map[trimmed.Substring(0, colon)] = trimmed.Substring(colon + 1).Trim();
        }
        return map;
    }

    void ApplyStatusResult(string statusOut)
    {
        _lastStatusOut = statusOut ?? "";
        SyncPendingWithStatus(_lastStatusOut);
        UpdateSleepLock(CountRunning(_lastStatusOut));
        UpdateTitleRowsInOpenMenu(_lastStatusOut);
    }

    void PopulateMenu(ContextMenuStrip menu)
    {
        if (_menuTimer != null)
        {
            _menuTimer.Stop();
            _menuTimer.Dispose();
            _menuTimer = null;
        }

        menu.Items.Clear();

        var header = new ToolStripMenuItem("DML Launcher v" + VERSION);
        header.Enabled = false;
        try { header.Font = new Font(SystemFonts.MenuFont, FontStyle.Bold); }
        catch { }
        menu.Items.Add(header);
        menu.Items.Add(new ToolStripSeparator());

        var placeholder = new ToolStripMenuItem("Checking servers...");
        placeholder.Enabled = false;
        placeholder.Tag = "placeholder";
        menu.Items.Add(placeholder);

        menu.Items.Add(new ToolStripSeparator());
        AddStaticItems(menu);

        string[] result = new string[1];

        _menuTimer = new System.Windows.Forms.Timer();
        _menuTimer.Interval = 150;
        _menuTimer.Tick += delegate
        {
            if (result[0] == null) return;
            _menuTimer.Stop();
            _menuTimer.Dispose();
            _menuTimer = null;
            if (menu.IsDisposed) return;

            int idx = -1;
            for (int i = 0; i < menu.Items.Count; i++)
                if ("placeholder".Equals(menu.Items[i].Tag as string)) { idx = i; break; }
            if (idx < 0) return;

            menu.Items.RemoveAt(idx);
            ApplyStatusResult(result[0]);
            var items = BuildTitleItems(result[0]);
            for (int i = items.Count - 1; i >= 0; i--)
                menu.Items.Insert(idx, items[i]);
        };
        _menuTimer.Start();

        System.Threading.ThreadPool.QueueUserWorkItem(delegate
        {
            try { result[0] = GetStatusOutput(); }
            catch { result[0] = BuildStoppedStatusOutput(); }
        });
        System.Threading.ThreadPool.QueueUserWorkItem(delegate { RefreshManageScriptCache(); });
    }

    System.Collections.Generic.List<ToolStripItem> BuildTitleItems(string statusOut)
    {
        var items     = new System.Collections.Generic.List<ToolStripItem>();
        var statusMap = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        int runningCount = 0;

        foreach (var line in statusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string trimmed = line.Trim();
            int colon = trimmed.IndexOf(':');
            if (colon > 0)
                statusMap[trimmed.Substring(0, colon)] = trimmed.Substring(colon + 1);
        }

        if (statusMap.Count == 0)
        {
            var empty = new ToolStripMenuItem("No titles installed");
            empty.Enabled = false;
            items.Add(empty);
            UpdateSleepLock(0);
            return items;
        }

        foreach (var kv in statusMap)
        {
            string title   = kv.Key;
            string reported = kv.Value;
            var displayState = GetDisplayState(title, reported);
            if (displayState == ServerDisplayState.Running) runningCount++;

            var gameMenu = new ToolStripMenuItem(FormatTitleText(title, displayState));
            gameMenu.Tag = title;
            gameMenu.ForeColor = ColorForState(displayState);

            var startItem   = new ToolStripMenuItem("Start");
            var restartItem = new ToolStripMenuItem("Restart");
            var stopItem    = new ToolStripMenuItem("Stop");
            ApplyTitleActionEnabled(startItem, restartItem, stopItem, displayState);

            string captured = title;
            startItem.Click   += delegate { RunAndReport("start",   captured); };
            restartItem.Click += delegate { RunAndReport("restart", captured); };
            stopItem.Click    += delegate { RunAndReport("stop",    captured); };

            gameMenu.DropDownItems.Add(startItem);
            gameMenu.DropDownItems.Add(restartItem);
            gameMenu.DropDownItems.Add(stopItem);

            if (TitleHasManageScript(title))
            {
                var manageItem = new ToolStripMenuItem("Manage");
                string capturedManage = title;
                manageItem.Click += delegate { OpenManageConsole(capturedManage); };
                gameMenu.DropDownItems.Add(manageItem);
            }

            items.Add(gameMenu);
        }

        SaveTitleCache(statusMap.Keys);
        UpdateSleepLock(runningCount);
        return items;
    }

    void AddStaticItems(ContextMenuStrip menu)
    {
        var installItem = new ToolStripMenuItem("Install New Title...");
        installItem.Click += delegate { ShowInstallDialog(); };
        menu.Items.Add(installItem);

        var shellItem = new ToolStripMenuItem("Open DML Shell");
        shellItem.Click += delegate { OpenTerminal("-d " + DISTRO); };
        menu.Items.Add(shellItem);

        var doctorItem = new ToolStripMenuItem("Run DML Doctor");
        doctorItem.Click += delegate
        {
            DeferCloseMenu();
            SetTrayProgress("Running doctor...");
            System.Threading.ThreadPool.QueueUserWorkItem(_ => {
                try {
                    string result = WslRun("dml doctor", WslLongTimeoutMs);
                    MaybeReReleaseWsl();
                    bool warn = result.Contains("[WARN]");
                    PostToUi(delegate {
                        RefreshTrayFromStatus();
                        MessageBoxIcon icon = warn ? MessageBoxIcon.Warning : MessageBoxIcon.Information;
                        MessageBox.Show(result, "DML Doctor", MessageBoxButtons.OK, icon);
                    });
                } catch (Exception ex) {
                    PostToUi(delegate {
                        MessageBox.Show("[error] " + ex.Message, "DML Doctor", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
            });
        };
        menu.Items.Add(doctorItem);

        menu.Items.Add(new ToolStripSeparator());

        var minimizeItem = new ToolStripMenuItem("Minimize");
        minimizeItem.Click += delegate { if (_menu != null && _menu.Visible) _menu.Close(); };
        menu.Items.Add(minimizeItem);

        var extrasMenu = new ToolStripMenuItem("Extras");
        var restartActiveItem = new ToolStripMenuItem("Restart active server/s");
        restartActiveItem.Click += delegate { RestartActiveServers(); };
        extrasMenu.DropDownItems.Add(restartActiveItem);

        var releaseItem = new ToolStripMenuItem("Stop WSL (release RAM)");
        releaseItem.Click += delegate { ConfirmAndReleaseWsl(); };
        extrasMenu.DropDownItems.Add(releaseItem);
        menu.Items.Add(extrasMenu);

        var exitItem = new ToolStripMenuItem("Exit");
        exitItem.Click += delegate {
            SetThreadExecutionState(ES_CONTINUOUS);  // always release before exit
            _tray.Visible = false;
            _tray.Dispose();
            Application.Exit();
        };
        menu.Items.Add(exitItem);
    }

    void RestartActiveServers()
    {
        if (!IsDistroRunning())
        {
            MessageBox.Show(
                "WSL is not running.\n\nUse Start on a title to boot the server first.",
                "Restart active server/s", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        int running = 0;
        try { running = CountRunning(WslRun("dml status")); } catch { }

        if (running == 0)
        {
            MessageBox.Show(
                "No active servers to restart.",
                "Restart active server/s", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        string msg = running == 1
            ? "Restart the 1 active server now?"
            : "Restart all " + running + " active servers now?";

        if (MessageBox.Show(msg, "Restart active server/s",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
            return;

        DeferCloseMenu();
        MarkAllTitlesPending("running");
        SetTrayProgress("Restarting active server(s) — see console");
        OpenLiveConsole("dml restart-active", "Restart active server/s");
        StartStatusPolling(900);
    }

    void ConfirmAndReleaseWsl()
    {
        int running = 0;
        try {
            if (IsDistroRunning())
                running = CountRunning(WslRun("dml status"));
        } catch { }

        string msg =
            "This will stop any running game servers cleanly (docker compose down), "
            + "then shut down WSL and return RAM to Windows.\n\n"
            + "• Running titles are stopped with docker compose down\n"
            + "• Docker and DML services are then stopped\n"
            + "• Vmmem RAM should drop within a few seconds\n\n"
            + "Use Start on a title when you want to play again.";

        if (running > 0)
            msg += "\n\n" + running + " server(s) will be stopped gracefully first.";

        if (MessageBox.Show(msg, "Stop WSL", MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
                != DialogResult.Yes)
            return;

        try { System.IO.File.WriteAllText(StoppedMarkerPath, DateTime.Now.ToString("o")); }
        catch { }

        MarkAllTitlesPending("stopped");
        UpdateSleepLock(0);
        DeferCloseMenu();

        if (IsDistroRunning())
        {
            SetTrayProgress("Stopping servers + releasing WSL...");
            System.Threading.ThreadPool.QueueUserWorkItem(_ => {
                try {
                    string result = WslRun("dml release-wsl", WslLongTimeoutMs);
                    bool warn = result.Contains("[WARN]") || result.ToLower().Contains("error");
                    PostToUi(delegate {
                        UpdateSleepLock(0);
                        _tray.Text = TrayTooltip(false);
                        MessageBoxIcon icon = warn ? MessageBoxIcon.Warning : MessageBoxIcon.Information;
                        MessageBox.Show(result, "Stop WSL", MessageBoxButtons.OK, icon);
                    });
                } catch (Exception ex) {
                    PostToUi(delegate {
                        MessageBox.Show("[error] " + ex.Message, "Stop WSL", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
            });
        }
        else
        {
            TriggerReleaseWsl(0);
            MessageBox.Show(
                "WSL is shutting down — RAM should free in a few seconds.\n"
                + "Use Start when you want to bring the server back.",
                "Stop WSL", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
    }

    void TriggerReleaseWsl(int delaySeconds)
    {
        try
        {
            string ps1 = System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
                "DML-Release-WSL.ps1");
            if (!System.IO.File.Exists(ps1)) return;
            var psi = new ProcessStartInfo();
            psi.FileName = "powershell.exe";
            psi.Arguments = "-NoProfile -WindowStyle Hidden -File \"" + ps1 + "\" -DelaySeconds " + delaySeconds;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(psi);
        }
        catch { }
    }

    void CloseMenuIfOpen()
    {
        try { if (_menu != null && _menu.Visible) _menu.Close(); } catch { }
    }

    void SetTrayProgress(string detail)
    {
        try { _tray.Text = "DML Launcher v" + VERSION + " — " + detail; } catch { }
    }

    void ShowTrayBalloon(string title, string text, ToolTipIcon icon)
    {
        try
        {
            _tray.BalloonTipTitle = title;
            _tray.BalloonTipText  = TruncateForBalloon(text);
            _tray.BalloonTipIcon  = icon;
            _tray.ShowBalloonTip(5000);
        }
        catch { }
    }

    string TruncateForBalloon(string text)
    {
        if (string.IsNullOrEmpty(text)) return "";
        text = text.Trim();
        return text.Length <= 240 ? text : text.Substring(0, 237) + "...";
    }

    void RefreshTrayFromStatus()
    {
        string[] r = { null };
        var pollTimer = new System.Windows.Forms.Timer { Interval = 150 };
        pollTimer.Tick += delegate {
            if (r[0] == null) return;
            pollTimer.Stop(); pollTimer.Dispose();
            ApplyStatusResult(r[0]);
        };
        pollTimer.Start();
        System.Threading.ThreadPool.QueueUserWorkItem(_ => {
            try { r[0] = GetStatusOutput(); }
            catch { r[0] = BuildStoppedStatusOutput(); }
        });
    }

    void StartStatusPolling(int durationSeconds)
    {
        var deadline = DateTime.UtcNow.AddSeconds(durationSeconds);
        var timer = new System.Windows.Forms.Timer { Interval = 3000 };
        timer.Tick += delegate {
            RefreshTrayFromStatus();
            if (DateTime.UtcNow >= deadline) {
                timer.Stop();
                timer.Dispose();
            }
        };
        timer.Start();
        RefreshTrayFromStatus();
    }

    // Same terminal + dml-arch path as "Open DML Shell" (wt → cmd → PowerShell).
    bool OpenShell(string wslArguments, string errorTitle)
    {
        try {
            // wt.exe treats ';' as a command separator — use 'new-tab --' so the
            // remainder is passed intact to wsl (live-console scripts use '&&' not ';').
            var psi = new ProcessStartInfo("wt.exe", "new-tab -- wsl " + wslArguments);
            psi.UseShellExecute = true;
            Process.Start(psi);
            return true;
        }
        catch { }
        try {
            var psi = new ProcessStartInfo("cmd.exe", "/k wsl " + wslArguments);
            psi.UseShellExecute = true;
            Process.Start(psi);
            return true;
        }
        catch { }
        try {
            var psi = new ProcessStartInfo("powershell.exe",
                "-NoExit -Command \"wsl " + wslArguments + "\"");
            psi.UseShellExecute = true;
            Process.Start(psi);
            return true;
        }
        catch (Exception ex) {
            MessageBox.Show("[error] Could not open console: " + ex.Message, errorTitle,
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
    }

    void OpenLiveConsole(string wslInnerCmd, string windowTitle)
    {
        // Staged start/stop and wow-manage.sh keep the console open themselves.
        // Other commands: land in login bash when done.
        string bashScript = wslInnerCmd;
        if (!KeepsConsoleOpen(wslInnerCmd))
            bashScript = wslInnerCmd + " && exec bash -l";
        string wslArgs = "-d " + DISTRO + " -e bash -lic \"" + bashScript.Replace("\"", "\\\"") + "\"";
        OpenShell(wslArgs, windowTitle);
    }

    static bool KeepsConsoleOpen(string wslInnerCmd)
    {
        return wslInnerCmd.IndexOf("wow-server-playerbots", StringComparison.OrdinalIgnoreCase) >= 0
            || wslInnerCmd.IndexOf("wow-manage.sh", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    void OpenManageConsole(string title)
    {
        DeferCloseMenu();
        OpenLiveConsole("cd ~/" + title + " && ./wow-manage.sh", "Manage " + title);
    }

    void RunAndReport(string cmd, string title)
    {
        DeferCloseMenu();
        string expected = (cmd == "stop") ? "stopped" : "running";
        MarkTitlePending(title, expected);
        try { System.IO.File.Delete(StoppedMarkerPath); } catch { }
        string caption = (cmd == "start" ? "Start " : cmd == "restart" ? "Restart " : "Stop ") + title;
        string verb = cmd == "start" ? "Starting" : cmd == "restart" ? "Restarting" : "Stopping";
        SetTrayProgress(verb + " " + title + " — see console");
        OpenLiveConsole("dml " + cmd + " " + title, caption);
        StartStatusPolling(900);
    }

    string WslRun(string wslCmd)
    {
        return WslRun(wslCmd, WslQuickTimeoutMs);
    }

    string WslRun(string wslCmd, int timeoutMs)
    {
        try
        {
            var psi = new ProcessStartInfo();
            psi.FileName               = "wsl.exe";
            psi.Arguments              = "-d " + DISTRO + " -- " + wslCmd;
            psi.UseShellExecute        = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError  = true;
            psi.CreateNoWindow         = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding  = Encoding.UTF8;
            using (var p = Process.Start(psi))
            {
                var stdout = new System.Text.StringBuilder();
                var stderr = new System.Text.StringBuilder();
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) {
                    if (e.Data != null) stdout.AppendLine(e.Data);
                };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) {
                    if (e.Data != null) stderr.AppendLine(e.Data);
                };
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                if (!p.WaitForExit(timeoutMs))
                {
                    string partial = stdout.ToString().Trim();
                    if (string.IsNullOrEmpty(partial)) partial = stderr.ToString().Trim();
                    if (!string.IsNullOrEmpty(partial)) partial += "\n";
                    return partial + "[WARN] Still running — use Open DML Shell to monitor progress.";
                }
                p.WaitForExit();
                string output = stdout.ToString().Trim();
                if (string.IsNullOrEmpty(output)) output = stderr.ToString().Trim();
                return output;
            }
        }
        catch (Exception ex)
        {
            return "[error] Could not run WSL: " + ex.Message;
        }
    }

    void OpenTerminal(string wslArgs)
    {
        OpenShell(wslArgs, "Open DML Shell");
    }

    void ShowInstallDialog()
    {
        using (var form = new Form())
        {
            form.Text            = "Install New DML Title";
            form.Size            = new Size(520, 210);
            form.StartPosition   = FormStartPosition.CenterScreen;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MaximizeBox     = false;
            form.MinimizeBox     = false;

            var lbl = new Label();
            lbl.Text   = "GitHub URL, local .sh installer, or folder:";
            lbl.Left   = 10; lbl.Top = 15; lbl.Width = 490; lbl.Height = 20;

            var box = new TextBox();
            box.Left = 10; box.Top = 42; box.Width = 380;

            var btnBrowse = new Button();
            btnBrowse.Text  = "Browse...";
            btnBrowse.Left  = 398; btnBrowse.Top = 40;
            btnBrowse.Width = 100;
            btnBrowse.Click += delegate {
                using (var ofd = new OpenFileDialog())
                {
                    ofd.Title       = "Select a DML installer script";
                    ofd.Filter      = "Shell scripts (*.sh)|*.sh|All files (*.*)|*.*";
                    ofd.FilterIndex = 1;
                    if (ofd.ShowDialog() == DialogResult.OK)
                        box.Text = ofd.FileName;
                }
            };

            var btnOk = new Button();
            btnOk.Text   = "Install"; btnOk.Left = 320; btnOk.Top = 100;
            btnOk.Width  = 85; btnOk.DialogResult = DialogResult.OK;

            var btnCancel = new Button();
            btnCancel.Text   = "Cancel"; btnCancel.Left = 415; btnCancel.Top = 100;
            btnCancel.Width  = 85; btnCancel.DialogResult = DialogResult.Cancel;

            form.Controls.Add(lbl);
            form.Controls.Add(box);
            form.Controls.Add(btnBrowse);
            form.Controls.Add(btnOk);
            form.Controls.Add(btnCancel);
            form.AcceptButton = btnOk;
            form.CancelButton = btnCancel;

            if (form.ShowDialog() == DialogResult.OK)
            {
                string input = box.Text.Trim();
                if (string.IsNullOrEmpty(input)) return;
                string wslPath = ToWslPath(input);
                // .sh file -> run directly with bash; directory or URL -> dml run
                string wslArgs = wslPath.EndsWith(".sh")
                    ? "-d " + DISTRO + " -- bash \"" + wslPath + "\""
                    : "-d " + DISTRO + " -- dml run \"" + wslPath + "\"";
                OpenTerminal(wslArgs);
            }
        }
    }

    static string ToWslPath(string input)
    {
        // Convert C:\path\to\folder -> /mnt/c/path/to/folder
        if (input.Length >= 3 && input[1] == ':' && (input[2] == '\\' || input[2] == '/'))
            return "/mnt/" + input.Substring(0, 1).ToLower() + "/" + input.Substring(3).Replace('\\', '/');
        return input;
    }
}

