# Desktop notification for Claude Code Notification hook events.
#
# Wired up in .claude/settings.json so that a supervisor loop or a background
# implementation agent needing input surfaces without anyone watching the
# terminal (see AUTOMATION.md — "Checking in"). Reads the hook payload as JSON
# on stdin; falls back to a generic message if the payload is unreadable.

$ErrorActionPreference = 'SilentlyContinue'

$raw = [Console]::In.ReadToEnd()
$message = 'Claude Code needs your attention'
try {
    $payload = $raw | ConvertFrom-Json
    if ($payload.message) { $message = [string]$payload.message }
} catch {
    # Unparseable payload is not worth failing over — keep the generic message.
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.ShowBalloonTip(10000, 'Claude Code — yesKnowMaybe', $message, [System.Windows.Forms.ToolTipIcon]::Info)

# The balloon disappears with the icon, so hold it briefly before disposing.
# The hook is configured async, so this does not block the session.
Start-Sleep -Seconds 6
$icon.Dispose()
