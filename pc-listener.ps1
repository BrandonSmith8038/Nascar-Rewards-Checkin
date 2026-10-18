# Opens the NASCAR leaderboard in your default browser when a "race is live" alert arrives.
# You still press Check In yourself.
# Run at login with Task Scheduler:
#   powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File pc-listener.ps1 -Topic YOUR-TOPIC
param(
  [Parameter(Mandatory = $true)][string]$Topic,
  [string]$Server = "https://ntfy.sh",
  [string]$Url = "https://www.nascar.com/leaderboard"
)
while ($true) {
  try {
    & curl.exe -s -N "$Server/$Topic/json" | ForEach-Object {
      if (-not $_) { return }
      $m = $_ | ConvertFrom-Json
      if ($m.event -eq "message" -and $m.tags -contains "checkered_flag") { Start-Process $Url }
    }
  } catch { }
  Start-Sleep -Seconds 10
}
