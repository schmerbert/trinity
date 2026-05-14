# Trinity Uninstaller

$ErrorActionPreference = "SilentlyContinue"

$INSTALL_DIR = "$env:USERPROFILE\Trinity"
$DESKTOP_SHORTCUT = "$env:USERPROFILE\Desktop\Trinity.lnk"
$TASK_NAME = "Trinity Eyes"

Write-Host ""
Write-Host "  T R I N I T Y" -ForegroundColor Cyan
Write-Host "  Uninstalling..." -ForegroundColor DarkGray
Write-Host ""

$confirm = Read-Host "  This will remove Trinity and all local files. Continue? (Y/N)"
if ($confirm -ne "Y") {
    Write-Host "  Cancelled." -ForegroundColor DarkGray
    exit
}

# Remove scheduled task
Write-Host "Removing scheduled task..." -ForegroundColor DarkGray
Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false

# Remove desktop shortcut
Write-Host "Removing desktop shortcut..." -ForegroundColor DarkGray
if (Test-Path $DESKTOP_SHORTCUT) {
    Remove-Item $DESKTOP_SHORTCUT -Force
}

# Remove install directory completely
Write-Host "Removing Trinity files..." -ForegroundColor DarkGray
if (Test-Path $INSTALL_DIR) {
    Remove-Item $INSTALL_DIR -Recurse -Force
}

# Remove self from downloads if run from there
$selfPath = $MyInvocation.MyCommand.Path
if ($selfPath -like "*Downloads*") {
    Write-Host "Cleaning up installer files..." -ForegroundColor DarkGray
    Remove-Item $selfPath -Force
}

Write-Host ""
Write-Host "  Trinity has been removed." -ForegroundColor Cyan
Write-Host "  Your Supabase data was not touched — delete that manually if needed." -ForegroundColor DarkGray
Write-Host ""