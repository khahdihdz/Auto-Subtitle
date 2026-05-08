# ============================================================
#  SubViet — Auto Subtitle Tieng Viet
#  Chay: chuot phai vao file -> "Run with PowerShell"
#        hoac: pwsh -ExecutionPolicy Bypass -File run.ps1
# ============================================================

$Host.UI.RawUI.WindowTitle = "SubViet - Auto Subtitle"
$TTL = 1800   # Thoi gian tu xoa file (giay). Mac dinh 30 phut.

Write-Host ""
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host "  |   SubViet -- Auto Subtitle              |" -ForegroundColor Cyan
Write-Host "  |   NVIDIA NIM + Whisper + FFmpeg         |" -ForegroundColor Cyan
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host ""

# -- Kiem tra Python -----------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "  [LOI] Khong tim thay Python!" -ForegroundColor Red
    Write-Host "  Tai Python tai: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Nho tich 'Add Python to PATH' khi cai." -ForegroundColor Yellow
    Read-Host "`n  Nhan Enter de thoat"
    exit 1
}

$pyVersion = python --version 2>&1
Write-Host "  [OK] $pyVersion" -ForegroundColor Green

# -- Kiem tra thu vien ----------------------------------------
Write-Host "  Kiem tra thu vien..." -ForegroundColor Gray
python -c "import flask" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [!] Chua cai thu vien. Dang cai requirements.txt..." -ForegroundColor Yellow
    Write-Host ""
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  [LOI] Cai thu vien that bai. Kiem tra ket noi mang." -ForegroundColor Red
        Read-Host "`n  Nhan Enter de thoat"
        exit 1
    }
    Write-Host ""
    Write-Host "  [OK] Cai xong!" -ForegroundColor Green
} else {
    Write-Host "  [OK] Thu vien da san sang." -ForegroundColor Green
}

# -- Set TTL env ---------------------------------------------
$env:SUBVIET_TTL = $TTL
Write-Host "  [OK] Tu xoa file sau: $TTL giay ($([math]::Round($TTL/60)) phut)" -ForegroundColor Gray

# -- Tu mo trinh duyet sau 2 giay ----------------------------
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:5000"
} | Out-Null

# -- Chay app ------------------------------------------------
Write-Host ""
Write-Host "  Dang khoi dong SubViet..." -ForegroundColor White
Write-Host "  Truy cap : http://localhost:5000" -ForegroundColor Cyan
Write-Host "  Dung app : Nhan Ctrl+C" -ForegroundColor Gray
Write-Host ""

try {
    python app.py
} finally {
    Write-Host ""
    Write-Host "  [SubViet da tat]" -ForegroundColor Yellow
    Read-Host "  Nhan Enter de dong"
}
