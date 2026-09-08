# Sets the Stripe keys on the deployed LOA backend, reading them from this
# folder's .env so the secret is never typed, pasted or echoed.
#
# Uses --update-env-vars, which merges. Do NOT switch to --set-env-vars: that
# replaces the whole block and would wipe DB_SERVER / DB_PASSWORD / SECRET_KEY
# and take the service down.
#
# Run from anywhere:  powershell -File .\set-stripe-env.ps1

$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) { throw "No .env found at $envFile" }

$vals = @{}
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*(STRIPE_SECRET_KEY|STRIPE_PUBLISHABLE_KEY)\s*=\s*(.+?)\s*$') {
    $vals[$matches[1]] = $matches[2].Trim('"').Trim("'")
  }
}

foreach ($k in 'STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY') {
  if (-not $vals[$k]) { throw "$k is missing or empty in $envFile" }
  # Report length and prefix only — never the value.
  $prefix = ($vals[$k] -split '_')[0..1] -join '_'
  Write-Host ("  {0,-24} {1}_... ({2} chars)" -f $k, $prefix, $vals[$k].Length)
}

$pairs = "STRIPE_SECRET_KEY=$($vals['STRIPE_SECRET_KEY']),STRIPE_PUBLISHABLE_KEY=$($vals['STRIPE_PUBLISHABLE_KEY'])"

Write-Host ''
Write-Host 'Updating livestock-backend-prod (merges with existing vars)...'
gcloud run services update livestock-backend-prod `
  --region=us-central1 `
  --project=animated-flare-421518 `
  --update-env-vars $pairs `
  --quiet

Write-Host ''
Write-Host 'Env var names now on the service:'
gcloud run services describe livestock-backend-prod `
  --region=us-central1 --project=animated-flare-421518 `
  --format="value(spec.template.spec.containers[0].env[].name)"
