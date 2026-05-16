$Host.UI.RawUI.WindowTitle = "Trinity - Live Log"
try { $Host.UI.RawUI.BackgroundColor = "Black"; Clear-Host } catch {}

Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkCyan
Write-Host "  T R I N I T Y   -   Live Log" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkCyan
Write-Host ""

$logFile = "logs\trinity_$((Get-Date).ToString('yyyy-MM-dd')).log"

if (-not (Test-Path $logFile)) {
    New-Item -ItemType File -Path $logFile -Force | Out-Null
}

Write-Host "  $logFile" -ForegroundColor DarkGray
Write-Host ""

Get-Content $logFile -Wait -Tail 60 | ForEach-Object {
    if     ($_ -match '\[ERROR\]') { Write-Host $_ -ForegroundColor Red     }
    elseif ($_ -match '\[WARN \]') { Write-Host $_ -ForegroundColor Yellow  }
    elseif ($_ -match '\[WIDGET \]') { Write-Host $_ -ForegroundColor Green }
    elseif ($_ -match '\[DISCORD\]') { Write-Host $_ -ForegroundColor Cyan  }
    elseif ($_ -match '\[EYES   \]') { Write-Host $_ -ForegroundColor Magenta }
    else                             { Write-Host $_ -ForegroundColor Gray   }
}
