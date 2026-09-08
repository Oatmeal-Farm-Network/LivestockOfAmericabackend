# Sets SENDGRID_API_KEY on the deployed LOA backend, reading it from this
# folder's .env so the secret is never typed, pasted or echoed.
#
# Without this variable every email path in the service is dead: routers/auth.py
# raises 503 "Email service not configured" on password reset, and the meeting,
# event and marketplace mailers silently no-op.
#
# Uses --update-env-vars, which merges. Do NOT switch to --set-env-vars: that
# replaces the whole block and would wipe DB_SERVER / DB_PASSWORD / SECRET_KEY /
# the Stripe keys and take the service down.
#
# Run from anywhere:  powershell -File .\set-sendgrid-env.ps1

$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) { throw "No .env found at $envFile" }

$key = $null
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*SENDGRID_API_KEY\s*=\s*(.+?)\s*$') {
    $key = $matches[1].Trim('"').Trim("'")
  }
}
if (-not $key) { throw "SENDGRID_API_KEY is missing or empty in $envFile" }

# Report shape only - never the value.
$fingerprint = [System.BitConverter]::ToString(
  [System.Security.Cryptography.SHA256]::Create().ComputeHash(
    [System.Text.Encoding]::UTF8.GetBytes($key))).Replace('-','').Substring(0,12).ToLower()
Write-Host ("  SENDGRID_API_KEY  {0}... ({1} chars, sha256:{2})" -f `
  $key.Substring(0, 3), $key.Length, $fingerprint)

Write-Host ''
Write-Host 'Updating livestock-backend-prod (merges with existing vars)...'
gcloud run services update livestock-backend-prod `
  --region=us-central1 `
  --project=animated-flare-421518 `
  --update-env-vars "SENDGRID_API_KEY=$key" `
  --quiet

Write-Host ''
Write-Host 'Env var names now on the service:'
gcloud run services describe livestock-backend-prod `
  --region=us-central1 --project=animated-flare-421518 `
  --format="value(spec.template.spec.containers[0].env[].name)"
