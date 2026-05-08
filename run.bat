@echo off
chcp 65001 >nul
title SubViet - Auto Subtitle Tieng Viet

set SUBVIET_TTL=1800

echo.
echo  +==========================================+
echo  ^|   SubViet -- Auto Subtitle              ^|
echo  ^|   NVIDIA NIM + Whisper + FFmpeg         ^|
echo  +==========================================+
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [LOI] Khong tim thay Python!
    echo  Tai Python tai: https://www.python.org/downloads/
    echo  Nho tich "Add Python to PATH" khi cai.
    pause
    exit /b 1
)

python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  [!] Chua cai thu vien. Dang cai requirements.txt...
    echo.
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  [LOI] Cai thu vien that bai. Kiem tra ket noi mang.
        pause
        exit /b 1
    )
    echo.
    echo  [OK] Cai xong!
    echo.
)

echo  [OK] Tu xoa file sau: %SUBVIET_TTL% giay
echo.
echo  Dang khoi dong SubViet...
echo  Truy cap: http://localhost:5000
echo  Dung app: Nhan Ctrl+C
echo.

start "" /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

python app.py

echo.
echo  [SubViet da tat]
pause
