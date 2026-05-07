"""
SubViet — Kiểm tra môi trường trước khi chạy
Chạy: python check_env.py
"""
import sys, shutil, subprocess

print("=" * 50)
print("🔍 SubViet — Kiểm tra môi trường")
print("=" * 50)

ok = True

# Python version
print(f"\n✅ Python: {sys.version.split()[0]}")

# FFmpeg
ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    try:
        r = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
        ver = r.stdout.split("\n")[0]
        print(f"✅ FFmpeg: {ver}")
    except:
        print(f"✅ FFmpeg tìm thấy tại: {ffmpeg}")
else:
    print("❌ FFmpeg: KHÔNG TÌM THẤY!")
    print("   → Tải tại: https://www.gyan.dev/ffmpeg/builds/")
    print("   → Giải nén, sau đó thêm thư mục bin vào PATH")
    print("   → Hoặc đặt vào C:\\ffmpeg\\bin\\")
    ok = False

# Whisper
try:
    import whisper
    print(f"✅ openai-whisper: OK")
except ImportError:
    print("❌ openai-whisper: chưa cài — chạy: pip install openai-whisper")
    ok = False

# Flask
try:
    import flask
    print(f"✅ Flask: {flask.__version__}")
except ImportError:
    print("❌ Flask: chưa cài — chạy: pip install flask flask-cors")
    ok = False

# Requests
try:
    import requests
    print(f"✅ requests: {requests.__version__}")
except ImportError:
    print("❌ requests: chưa cài")
    ok = False

print("\n" + "=" * 50)
if ok:
    print("🎉 Môi trường OK! Chạy app bằng: python app.py")
else:
    print("⚠️  Sửa các lỗi trên rồi chạy lại check_env.py")
print("=" * 50)
