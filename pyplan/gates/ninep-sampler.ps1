# Sample what an install is writing into a Windows bind mount, once a minute, into a file.
#
# WHY THIS EXISTS. 7.7 asks for "9p extract/mmaps throughput recorded", and the
# obvious way to get it -- measure the directory twice by hand and subtract --
# produces a number that lives only in a terminal. That is the exact failure a
# review found in 7.4c's record on 2026-09-04: a dozen figures read off a live
# server while the gate was running, and gone the moment the gate changed the
# server. So this writes every sample to disk as it takes it.
#
# It measures the SERVER directory, which on Windows is a host folder that
# Docker Desktop exposes to the Linux VM over a 9p mount. Every byte a container
# writes into `src/`, `data/` or `env/` crosses that mount, which is what makes
# the number worth having: on Linux the same work is at disk speed.
#
# Deliberately dumb: no dependencies, no cleanup, no rotation. It is a gate
# instrument that runs for a few hours and is read once.
#
#   powershell -NoProfile -File ninep-sampler.ps1 -Root C:\gate\vanilla-server -Out C:\gate\ninep.csv

param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$IntervalSeconds = 60
)

# UTC as well as local: this box runs Pacific time while the people reading its
# logs are on CEST, so a local-only stamp reads ten hours wrong to them.
"utc,local,subdir,bytes,files,bytes_delta,files_delta,mb_per_s,files_per_s" |
    Out-File -FilePath $Out -Encoding utf8

$last = @{}
while ($true) {
    $now = Get-Date
    foreach ($sub in @("src", "data", "env", ".")) {
        $path = if ($sub -eq ".") { $Root } else { Join-Path $Root $sub }
        if (-not (Test-Path $path)) { continue }
        $files = Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue
        $bytes = ($files | Measure-Object Length -Sum).Sum
        if ($null -eq $bytes) { $bytes = 0 }
        $count = ($files | Measure-Object).Count

        $key = $sub
        $db = 0
        $dfl = 0
        if ($last.ContainsKey($key)) {
            $db = $bytes - $last[$key].Bytes
            $dfl = $count - $last[$key].Files
        }
        $last[$key] = @{ Bytes = $bytes; Files = $count }

        $mbps = [math]::Round($db / 1MB / $IntervalSeconds, 3)
        $fps = [math]::Round($dfl / $IntervalSeconds, 2)
        $line = "{0},{1},{2},{3},{4},{5},{6},{7},{8}" -f `
            $now.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"), `
            $now.ToString("yyyy-MM-ddTHH:mm:ss"), $sub, $bytes, $count, $db, $dfl, $mbps, $fps
        $line | Out-File -FilePath $Out -Encoding utf8 -Append
    }
    Start-Sleep -Seconds $IntervalSeconds
}
