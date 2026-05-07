# 🎬 SubViet — Auto Subtitle Tiếng Việt Tự Nhiên

Tool tự động tạo phụ đề tiếng Việt **tự nhiên** từ video/audio, powered by:
- 🎙️ **OpenAI Whisper** — Nhận dạng giọng nói chính xác cao
- 🤖 **NVIDIA NIM API** (Llama 3.3 70B) — Dịch tự nhiên theo thể loại

---

## 🚀 Cài đặt

```bash
cd auto-subtitle
pip install -r requirements.txt
```

> Lần đầu chạy sẽ tự tải Whisper model (tiny ~75MB, base ~145MB, medium ~1.5GB)

---

## 🖥️ Dùng giao diện Web

```bash
python app.py
```

Truy cập: **http://localhost:5000**

### Tính năng Web UI:
- Drag & drop file video/audio
- Chọn thể loại phim (tình cảm, hành động, anime...)
- Xem trước phụ đề trực tiếp
- Tải về SRT / VTT / Song ngữ

---

## ⌨️ Dùng CLI (Terminal)

```bash
# Cơ bản
python cli.py video.mp4 --key nvapi-xxx

# Nâng cao
python cli.py movie.mkv --key nvapi-xxx --genre romance --model small --bilingual

# Anime/phim Nhật
python cli.py anime.mp4 --key nvapi-xxx --genre anime --lang ja --model medium
```

### Tham số CLI:
| Tham số | Mô tả | Mặc định |
|---------|-------|---------|
| `--key` | NVIDIA NIM API Key | Bắt buộc |
| `--model` | Whisper: tiny/base/small/medium/large | base |
| `--lang` | Ngôn ngữ gốc: auto/en/ja/ko/zh | auto |
| `--genre` | Thể loại: general/romance/action/comedy/horror/documentary/anime/drama | general |
| `--bilingual` | Xuất thêm file song ngữ | False |
| `--output` | Thư mục output | Cùng thư mục video |

---

## 📁 Định dạng hỗ trợ

**Video:** mp4, mkv, avi, mov, webm  
**Audio:** mp3, wav, m4a, flac, ogg

## 📤 Output

- `*_vi.srt` — Phụ đề tiếng Việt (SubRip)
- `*_vi.vtt` — Phụ đề tiếng Việt (WebVTT)
- `*_original.srt` — Ngôn ngữ gốc
- `*_bilingual.srt` — Song ngữ (khi dùng --bilingual)

---

## 🔑 Lấy NVIDIA NIM API Key

1. Truy cập [build.nvidia.com](https://build.nvidia.com)
2. Đăng ký / đăng nhập
3. Vào **API Keys** → Tạo key mới
4. Key có format: `nvapi-xxxxxxxxx`

---

## 💡 Mẹo chọn Model Whisper

| Model | VRAM | Tốc độ | Độ chính xác |
|-------|------|--------|--------------|
| tiny | ~1GB | ⚡⚡⚡ | ★★☆ |
| base | ~1GB | ⚡⚡ | ★★★ |
| small | ~2GB | ⚡ | ★★★★ |
| medium | ~5GB | 🐢 | ★★★★★ |
| large | ~10GB | 🐢🐢 | ★★★★★ |
