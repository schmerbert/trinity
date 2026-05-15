# Trinity Installer
# Run in PowerShell as Administrator

$ErrorActionPreference = "Stop"

$REPO_URL    = "https://github.com/schmerbert/trinity.git"
$INSTALL_DIR = "$env:USERPROFILE\Trinity"

function Prompt-Key {
    param(
        [string]$Label,
        [string]$Hint,
        [string]$Default = "",
        [bool]$Required = $true
    )
    Write-Host ""
    Write-Host "  $Label" -ForegroundColor Cyan
    if ($Hint)    { Write-Host "  $Hint" -ForegroundColor DarkGray }
    if ($Default) { Write-Host "  [Enter to keep: $Default]" -ForegroundColor DarkGray }

    $val = Read-Host "  >"
    if (-not $val -and $Default) { return $Default }
    if (-not $val -and $Required) {
        Write-Host "  Required — please enter a value." -ForegroundColor Yellow
        return Prompt-Key -Label $Label -Hint $Hint -Default $Default -Required $Required
    }
    return $val
}

# ─── Header ──────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  T R I N I T Y" -ForegroundColor Cyan
Write-Host "  Installing..." -ForegroundColor DarkGray
Write-Host ""

# ─── Python ──────────────────────────────────────────────────────────────────

Write-Host "  Checking Python..." -ForegroundColor DarkGray
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor DarkGray
} catch {
    Write-Host "  Python not found. Installing via winget..." -ForegroundColor Yellow
    winget install Python.Python.3.12 --silent
    $env:PATH += ";$env:LOCALAPPDATA\Programs\Python\Python312"
}

# ─── Git ─────────────────────────────────────────────────────────────────────

Write-Host "  Checking Git..." -ForegroundColor DarkGray
try {
    git --version | Out-Null
    Write-Host "  Git found." -ForegroundColor DarkGray
} catch {
    Write-Host "  Git not found. Installing via winget..." -ForegroundColor Yellow
    winget install Git.Git --silent
}

# ─── Clone / update ──────────────────────────────────────────────────────────

Write-Host "  Fetching Trinity..." -ForegroundColor DarkGray
if (Test-Path $INSTALL_DIR) {
    Write-Host "  Existing install found. Updating..." -ForegroundColor Yellow
    Set-Location $INSTALL_DIR
    git pull
} else {
    git clone -b clean-main $REPO_URL $INSTALL_DIR
    Set-Location $INSTALL_DIR
}

# ─── Virtual environment ─────────────────────────────────────────────────────

Write-Host "  Setting up Python environment..." -ForegroundColor DarkGray
python -m venv venv
& "$INSTALL_DIR\venv\Scripts\pip.exe" install -r "$INSTALL_DIR\requirements.txt" --quiet

# ─── API keys ────────────────────────────────────────────────────────────────

# Load existing .env values as defaults if updating
$existing = @{}
if (Test-Path "$INSTALL_DIR\.env") {
    Get-Content "$INSTALL_DIR\.env" | ForEach-Object {
        if ($_ -match "^([^#][^=]+)=(.*)$") {
            $existing[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
}

Write-Host ""
Write-Host "  ── API Keys ─────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Required keys first, then optional. Press Enter to keep existing values." -ForegroundColor DarkGray

$ANTHROPIC_KEY = Prompt-Key `
    -Label    "Anthropic API Key  [REQUIRED]" `
    -Hint     "console.anthropic.com → API Keys" `
    -Default  $existing["ANTHROPIC_API_KEY"] `
    -Required $true

$SUPABASE_URL = Prompt-Key `
    -Label    "Supabase Project URL  [REQUIRED]" `
    -Hint     "supabase.com → Project Settings → API → Project URL" `
    -Default  $existing["SUPABASE_URL"] `
    -Required $true

$SUPABASE_KEY = Prompt-Key `
    -Label    "Supabase Anon Key  [REQUIRED]" `
    -Hint     "supabase.com → Project Settings → API → anon public" `
    -Default  $existing["SUPABASE_KEY"] `
    -Required $true

Write-Host ""
Write-Host "  ── Optional ─────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Leave blank to skip — Trinity will work without these." -ForegroundColor DarkGray

$NEWS_API_KEY = Prompt-Key `
    -Label    "NewsAPI Key  [optional]" `
    -Hint     "newsapi.org — free tier, 100 req/day" `
    -Default  $existing["NEWS_API_KEY"] `
    -Required $false

$DISCORD_TOKEN = Prompt-Key `
    -Label    "Discord Bot Token  [optional]" `
    -Hint     "discord.com/developers → your app → Bot → Token" `
    -Default  $existing["DISCORD_BOT_TOKEN"] `
    -Required $false

$DISCORD_OWNER = Prompt-Key `
    -Label    "Your Discord User ID  [optional]" `
    -Hint     "Discord → Settings → Advanced → Developer Mode → right-click your name → Copy User ID" `
    -Default  $existing["DISCORD_OWNER_ID"] `
    -Required $false

Write-Host ""
Write-Host "  ── Monitoring defaults ──────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Trinity learns what to watch through conversation — these seed her Eyes." -ForegroundColor DarkGray

$DEFAULT_SUBS = "pkmntcg,PokemonTCG,pokemoncardcollectors,UnionArena,CryptoMoonShots,SatoshiStreetBets,solana,stocks,investing,wallstreetbets"
$DEFAULT_KW   = "pokemon,union arena,star card,troll,wish,MOS,mosaic,fertilizer,potash,reprint,print run,memecoin,solana"

$SUBREDDITS = Prompt-Key `
    -Label    "Reddit subreddits (comma-separated)" `
    -Default  ($existing["REDDIT_SUBREDDITS"] ?? $DEFAULT_SUBS) `
    -Required $false

$KEYWORDS = Prompt-Key `
    -Label    "Keywords (comma-separated)" `
    -Default  ($existing["KEYWORDS"] ?? $DEFAULT_KW) `
    -Required $false

if (-not $SUBREDDITS) { $SUBREDDITS = $DEFAULT_SUBS }
if (-not $KEYWORDS)   { $KEYWORDS   = $DEFAULT_KW   }

# ─── Write .env ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  Writing configuration..." -ForegroundColor DarkGray

$envLines = @(
    "ANTHROPIC_API_KEY=$ANTHROPIC_KEY",
    "SUPABASE_URL=$SUPABASE_URL",
    "SUPABASE_KEY=$SUPABASE_KEY",
    "NEWS_API_KEY=$NEWS_API_KEY",
    "DISCORD_BOT_TOKEN=$DISCORD_TOKEN",
    "DISCORD_OWNER_ID=$DISCORD_OWNER",
    "REDDIT_SUBREDDITS=$SUBREDDITS",
    "KEYWORDS=$KEYWORDS"
)
$envLines | Out-File -FilePath "$INSTALL_DIR\.env" -Encoding ASCII

# Strip BOM if present
& "$INSTALL_DIR\venv\Scripts\python.exe" -c @"
content = open('.env', 'rb').read()
if content.startswith(b'\xef\xbb\xbf'):
    content = content[3:]
open('.env', 'wb').write(content)
"@

# ─── Desktop shortcut ────────────────────────────────────────────────────────

Write-Host "  Creating desktop shortcut..." -ForegroundColor DarkGray
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\Trinity.lnk")
$Shortcut.TargetPath      = "$INSTALL_DIR\trinity.bat"
$Shortcut.WorkingDirectory = $INSTALL_DIR
$Shortcut.Description     = "Trinity Financial Intelligence"
$Shortcut.Save()

# ─── Task scheduler ──────────────────────────────────────────────────────────

Write-Host "  Registering startup task (disabled by default)..." -ForegroundColor DarkGray
$action   = New-ScheduledTaskAction `
    -Execute "$INSTALL_DIR\venv\Scripts\pythonw.exe" `
    -Argument "$INSTALL_DIR\nervous_system\watcher.py" `
    -WorkingDirectory $INSTALL_DIR
$trigger  = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$task     = Register-ScheduledTask -TaskName "Trinity Eyes" -Action $action -Trigger $trigger -Settings $settings -Force
$task | Disable-ScheduledTask | Out-Null

# ─── Done ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ── Done ──────────────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  Trinity is installed at $INSTALL_DIR" -ForegroundColor DarkGray
Write-Host "  Launch via the desktop shortcut or trinity.bat" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Your keys are stored locally in .env — never committed to GitHub." -ForegroundColor DarkGray
Write-Host ""
