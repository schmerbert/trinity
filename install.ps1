# Trinity Installer
# Run this as Administrator in PowerShell

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  T R I N I T Y" -ForegroundColor Cyan
Write-Host "  Installing..." -ForegroundColor DarkGray
Write-Host ""

# --- Config ---
$REPO_URL = "https://github.com/YOUR_GITHUB_USERNAME/trinity.git"
$INSTALL_DIR = "$env:USERPROFILE\Trinity"
$ANTHROPIC_KEY = "your_anthropic_key_here"
$SUPABASE_URL = "https://eelcjityipyihvgokpcy.supabase.co"
$SUPABASE_KEY = "your_supabase_key_here"
$NEWS_API_KEY = "your_newsapi_key_here"
$TROLL_CA = "your_troll_ca_here"
$WISH_CA = "your_wish_ca_here"
$REDDIT_SUBREDDITS = "pkmntcg,PokemonTCG,pokemoncardcollectors,UnionArena,CryptoMoonShots,SatoshiStreetBets,solana,stocks,investing,wallstreetbets"
$KEYWORDS = "pokemon,union arena,star card,troll,wish,MOS,mosaic,fertilizer,potash,reprint,print run,memecoin,solana"

# --- Check Python ---
Write-Host "Checking Python..." -ForegroundColor DarkGray
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Found: $pythonVersion" -ForegroundColor DarkGray
} catch {
    Write-Host "Python not found. Installing..." -ForegroundColor Yellow
    winget install Python.Python.3.12 --silent
    $env:PATH += ";$env:LOCALAPPDATA\Programs\Python\Python312"
}

# --- Check Git ---
Write-Host "Checking Git..." -ForegroundColor DarkGray
try {
    git --version | Out-Null
} catch {
    Write-Host "Git not found. Installing..." -ForegroundColor Yellow
    winget install Git.Git --silent
}

# --- Clone Repo ---
Write-Host "Cloning Trinity..." -ForegroundColor DarkGray
if (Test-Path $INSTALL_DIR) {
    Write-Host "Existing install found. Updating..." -ForegroundColor Yellow
    Set-Location $INSTALL_DIR
    git pull
} else {
    git clone $REPO_URL $INSTALL_DIR
    Set-Location $INSTALL_DIR
}

# --- Virtual Environment ---
Write-Host "Setting up environment..." -ForegroundColor DarkGray
python -m venv venv
& "$INSTALL_DIR\venv\Scripts\Activate.ps1"
pip install -r requirements.txt --quiet

# --- Create .env ---
Write-Host "Writing configuration..." -ForegroundColor DarkGray
$envContent = @"
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
SUPABASE_URL=$SUPABASE_URL
SUPABASE_KEY=$SUPABASE_KEY
NEWS_API_KEY=$NEWS_API_KEY
TROLL_CA=$TROLL_CA
WISH_CA=$WISH_CA
REDDIT_SUBREDDITS=$REDDIT_SUBREDDITS
KEYWORDS=$KEYWORDS
"@
$envContent | Out-File -FilePath "$INSTALL_DIR\.env" -Encoding UTF8

# --- Create launch script ---
$launchScript = @"
@echo off
cd /d $INSTALL_DIR
call venv\Scripts\activate.bat
python eyes\scraper.py
python voice\interface.py
pause
"@
$launchScript | Out-File -FilePath "$INSTALL_DIR\trinity.bat" -Encoding ASCII

# --- Desktop Shortcut ---
Write-Host "Creating desktop shortcut..." -ForegroundColor DarkGray
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\Trinity.lnk")
$Shortcut.TargetPath = "$INSTALL_DIR\trinity.bat"
$Shortcut.WorkingDirectory = $INSTALL_DIR
$Shortcut.Description = "Trinity Financial Intelligence"
$Shortcut.Save()

# --- Task Scheduler for Eyes ---
Write-Host "Scheduling background scans..." -ForegroundColor DarkGray
$action = New-ScheduledTaskAction -Execute "$INSTALL_DIR\venv\Scripts\python.exe" -Argument "$INSTALL_DIR\eyes\scraper.py" -WorkingDirectory $INSTALL_DIR
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours 4) -Once -At (Get-Date)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "Trinity Eyes" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "  Trinity is ready." -ForegroundColor Cyan
Write-Host "  Launch from your desktop or run trinity.bat" -ForegroundColor DarkGray
Write-Host ""
</parameter>

Fill in your actual values where it says `your_x_here` and `YOUR_GITHUB_USERNAME`. Save it, commit it privately for now, and we'll test it on your PC.

```bash
git add .
git commit -m "Windows installer"
git push
```