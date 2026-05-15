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

# Check for backups and ask whether to purge them
$backups = @()
if (Test-Path $INSTALL_DIR) {
    $backups = Get-ChildItem -Path $INSTALL_DIR -Filter "trinity_backup_*.json" -ErrorAction SilentlyContinue
}

$purgeBackups = $false
if ($backups.Count -gt 0) {
    Write-Host ""
    Write-Host "  Found $($backups.Count) backup file(s):" -ForegroundColor DarkGray
    foreach ($b in $backups) {
        Write-Host "    $($b.Name)" -ForegroundColor DarkGray
    }
    Write-Host ""
    $purgeChoice = Read-Host "  Delete backups too? (Y/N, default N)"
    $purgeBackups = ($purgeChoice -eq "Y")
}

# Remove scheduled task
Write-Host "Removing scheduled task..." -ForegroundColor DarkGray
Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false

# Remove desktop shortcut
Write-Host "Removing desktop shortcut..." -ForegroundColor DarkGray
if (Test-Path $DESKTOP_SHORTCUT) {
    Remove-Item $DESKTOP_SHORTCUT -Force
}

# Save backups before wiping directory, then restore if keeping them
if (($backups.Count -gt 0) -and (-not $purgeBackups)) {
    $backupStagingDir = "$env:USERPROFILE\TrinityBackups"
    New-Item -ItemType Directory -Force -Path $backupStagingDir | Out-Null
    foreach ($b in $backups) {
        Copy-Item $b.FullName -Destination $backupStagingDir -Force
    }
    Write-Host "  Backups moved to $backupStagingDir" -ForegroundColor DarkGray
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
if (($backups.Count -gt 0) -and (-not $purgeBackups)) {
    Write-Host "  Backups preserved at $env:USERPROFILE\TrinityBackups" -ForegroundColor DarkGray
} elseif ($purgeBackups) {
    Write-Host "  Backups purged." -ForegroundColor DarkGray
}
Write-Host "  Your Supabase data was not touched — delete that manually if needed." -ForegroundColor DarkGray
Write-Host ""