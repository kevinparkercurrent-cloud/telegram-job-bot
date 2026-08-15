$ErrorActionPreference = "Stop"

trap {
    Write-Host ""
    Write-Host ("SETUP ERROR: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "No configuration was saved."
    Read-Host "Press Enter to close"
    exit 1
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    return [Net.NetworkCredential]::new("", $secure).Password
}

Write-Host "Telegram vacancy bot - secure setup" -ForegroundColor Cyan
Write-Host "Values entered here are saved only to the local .env file."
Write-Host ""

$apiId = (Read-Host "Telegram api_id (number)").Trim()
$apiHash = (Read-Secret "Telegram api_hash").Trim()
$phone = (Read-Host "Phone of the separate reader account, e.g. +79991234567").Trim()
$botToken = (Read-Secret "Token received from @BotFather").Trim()

if ($apiId -notmatch "^\d+$") {
    throw "api_id must contain digits only."
}
if ([string]::IsNullOrWhiteSpace($apiHash) -or
    [string]::IsNullOrWhiteSpace($phone) -or
    [string]::IsNullOrWhiteSpace($botToken)) {
    throw "All requested values are required."
}

try {
    $me = Invoke-RestMethod -Method Get -Uri ("https://api.telegram.org/bot{0}/getMe" -f $botToken)
    if (-not $me.ok) { throw "Bot token was rejected." }

    $updates = Invoke-RestMethod -Method Get -Uri ("https://api.telegram.org/bot{0}/getUpdates?limit=100&timeout=0" -f $botToken)
    $adminId = $updates.result |
        Where-Object { $_.message.from.id } |
        Select-Object -Last 1 -ExpandProperty message |
        Select-Object -ExpandProperty from |
        Select-Object -ExpandProperty id
} catch {
    throw "Could not verify the bot. Check the token and internet connection."
}

if (-not $adminId) {
    throw "No /start message found. Open the new bot from the main account, press Start, and run this setup again."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$content = @"
TELEGRAM_API_ID=$apiId
TELEGRAM_API_HASH=$apiHash
TELEGRAM_PHONE=$phone
TELEGRAM_SESSION_PATH=/data/telegram.session
CONTROL_BOT_TOKEN=$botToken
ADMIN_TELEGRAM_ID=$adminId
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
EXTERNAL_SOURCE_DOMAINS=["hh.ru","career.habr.com"]
CANDIDATE_PROFILE_PATH=/app/config/candidate-profile.full.json
DATABASE_PATH=/data/job-bot.sqlite3
APP_TIMEZONE=Europe/Moscow
DIGEST_TIMES=["12:00","19:00"]
BACKUP_AGE_RECIPIENT=
"@

[IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))
$apiHash = $null
$botToken = $null

Write-Host ""
Write-Host "Configuration saved successfully." -ForegroundColor Green
Write-Host "You may close this window and tell Codex: setup complete"
Read-Host "Press Enter to close"
