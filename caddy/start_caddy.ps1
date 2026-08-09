# start_caddy.ps1 - reads CLOUDFLARE_API_TOKEN out of the main project's .env (same secrets
# file every other integration in this project uses, per its own docstring convention) and
# launches Caddy with it set, so the token itself never has to live in the Caddyfile or in
# this script. Run this from anywhere; it resolves paths relative to its own location.
#
# Kept plain-ASCII on purpose (no em-dashes/smart quotes) - Windows PowerShell 5.1 doesn't
# reliably auto-detect UTF-8 without a BOM, and a stray multi-byte character earlier in the
# file was previously desyncing the parser, surfacing as confusing "missing terminator"
# errors on unrelated later lines.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $here "..\.env"

if (-not (Test-Path $envFile)) {
    Write-Error "Could not find .env at $envFile - add CLOUDFLARE_API_TOKEN=<your token> to it first."
    exit 1
}

$tokenLine = Get-Content $envFile | Where-Object { $_ -match '^\s*CLOUDFLARE_API_TOKEN\s*=' } | Select-Object -Last 1
if (-not $tokenLine) {
    Write-Error "CLOUDFLARE_API_TOKEN is not set in .env - add a line: CLOUDFLARE_API_TOKEN=<your scoped Cloudflare token>"
    exit 1
}
$token = ($tokenLine -split '=', 2)[1].Trim()
if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "your-scoped-cloudflare-token-here") {
    Write-Error "CLOUDFLARE_API_TOKEN in .env is still a placeholder - replace it with your real scoped token (Cloudflare dashboard -> My Profile -> API Tokens -> Edit zone DNS template, scoped to the dhaaselab.com zone)."
    exit 1
}

$env:CLOUDFLARE_API_TOKEN = $token
Set-Location $here
Write-Output "Starting Caddy for ai.dhaaselab.com (config: $here\Caddyfile) ..."

# Real bug hit running this: Caddy logs its normal, healthy startup info (structured JSON)
# to stderr, and Windows PowerShell 5.1 wraps every native-command stderr line in a
# NativeCommandError when captured - with $ErrorActionPreference = "Stop" (set above, for
# the earlier validation checks) that turned Caddy's routine logging into a terminating
# script error before Caddy ever got a chance to actually obtain the certificate. Reset to
# "Continue" for this one long-running foreground call; a real Caddy failure still shows up
# in its own JSON output, it just won't get wrapped and aborted by PowerShell's own
# native-stderr handling.
$ErrorActionPreference = "Continue"
& "$here\caddy.exe" run --config "$here\Caddyfile"
