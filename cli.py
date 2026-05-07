#!/usr/bin/env python3
"""
SubViet CLI — Phụ đề tiếng Việt tự nhiên + Burn-in vào video
"""
import argparse, sys, time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="SubViet CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python cli.py video.mp4 --key nvapi-xxx
  python cli.py movie.mkv --key nvapi-xxx --genre romance --burn-in --style cinema
  python cli.py anime.mp4 --key nvapi-xxx --genre anime --lang ja --bilingual --burn-in
        """)
    parser.add_argument("file")
    parser.add_argument("--key", required=True)
    parser.add_argument("--model", default="base",
        choices=["tiny","base","small","medium","large"])
    parser.add_argument("--lang", default="auto")
    parser.add_argument("--genre", default="general",
        choices=["general","romance","action","comedy","horror","documentary","anime","drama"])
    parser.add_argument("--bilingual", action="store_true")
    parser.add_argument("--burn-in", action="store_true", dest="burn_in")
    parser.add_argument("--style", default="classic",
        choices=["classic","cinema","netflix","neon","minimal","drama"])
    parser.add_argument("--quality", type=int, default=23, help="CRF 18-32 (default 23)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ File không tồn tại: {args.file}"); sys.exit(1)

    output_dir = Path(args.output) if args.output else file_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    import whisper
    sys.path.insert(0, str(Path(__file__).parent))
    from app import (translate_batch_nim, generate_srt, generate_vtt,
                     generate_bilingual_srt, generate_ass, burn_subtitles, fmt_srt)

    VIDEO_EXTS = {'.mp4','.mkv','.avi','.mov','.webm'}
    is_vid = file_path.suffix.lower() in VIDEO_EXTS

    print(f"\n🎬 SubViet — Phụ đề tiếng Việt tự nhiên")
    print("="*52)
    print(f"  📁 File   : {file_path.name}")
    print(f"  ⚙️  Whisper: {args.model}")
    print(f"  🌐 Ngôn ngữ: {args.lang}")
    print(f"  🎭 Thể loại: {args.genre}")
    if args.burn_in and is_vid:
        print(f"  🔥 Burn-in : ON  |  Style: {args.style}  |  CRF: {args.quality}")
    elif args.burn_in and not is_vid:
        print(f"  🔥 Burn-in : ⚠️ Bỏ qua (file audio)")
    print()

    print(f"⏳ Tải model Whisper {args.model}...")
    model = whisper.load_model(args.model)
    print(f"✅ Model sẵn sàng\n")

    print(f"🎙️  Nhận dạng giọng nói...")
    t0 = time.time()
    opts = {"verbose": False, "task": "transcribe"}
    if args.lang != "auto": opts["language"] = args.lang
    result = model.transcribe(str(file_path), **opts)
    segments = result["segments"]
    detected = result.get("language", "?")
    print(f"✅ Xong: {len(segments)} đoạn | Ngôn ngữ: {detected.upper()} | {time.time()-t0:.1f}s\n")

    print(f"🌐 Dịch sang tiếng Việt ({args.genre})...")
    BATCH = 20
    translated = []
    total = (len(segments)+BATCH-1)//BATCH
    for i in range(0, len(segments), BATCH):
        batch = segments[i:i+BATCH]
        bn = i//BATCH+1
        print(f"  Batch {bn}/{total} ({len(batch)} đoạn)...", end=" ", flush=True)
        t1 = time.time()
        trans = translate_batch_nim(batch, args.key, args.genre)
        for seg, vi in zip(batch, trans):
            translated.append({**seg, "vi_text": vi})
        print(f"✓ {time.time()-t1:.1f}s")
        time.sleep(0.3)

    print(f"\n📝 Tạo file phụ đề...")
    stem = file_path.stem
    saved = []

    p = output_dir / f"{stem}_vi.srt"
    p.write_text(generate_srt(translated), encoding="utf-8"); saved.append(("🇻🇳 SRT tiếng Việt", p))

    p = output_dir / f"{stem}_vi.vtt"
    p.write_text(generate_vtt(translated), encoding="utf-8"); saved.append(("🌐 VTT tiếng Việt", p))

    p = output_dir / f"{stem}_original.srt"
    p.write_text(generate_srt(translated, key="text"), encoding="utf-8"); saved.append(("📄 SRT gốc", p))

    if args.bilingual:
        p = output_dir / f"{stem}_bilingual.srt"
        p.write_text(generate_bilingual_srt(translated), encoding="utf-8"); saved.append(("📋 Song ngữ SRT", p))

    ass_path = output_dir / f"{stem}_vi.ass"
    ass_path.write_text(generate_ass(translated, style_name=args.style, bilingual=args.bilingual), encoding="utf-8")
    saved.append(("🎨 ASS styled", ass_path))

    if args.burn_in and is_vid:
        print(f"\n🔥 Đang burn-in phụ đề [{args.style}] vào video...")
        burned = output_dir / f"{stem}_subtitled.mp4"
        t2 = time.time()
        try:
            burn_subtitles(file_path, ass_path, burned, quality=args.quality)
            saved.append(("🎬 VIDEO burn-in ✨", burned))
            print(f"✅ Render xong! {time.time()-t2:.1f}s")
        except Exception as e:
            print(f"⚠️  Render thất bại: {e}")

    print(f"\n{'='*52}")
    print("📂 Files đã lưu:")
    for label, p in saved:
        sz = p.stat().st_size
        sz_str = f"{sz//1024}KB" if sz<1048576 else f"{sz//1048576:.1f}MB"
        print(f"  {label}: {p.name} ({sz_str})")

    print(f"\n👁  Preview (5 đoạn đầu):")
    print("─"*60)
    for s in translated[:5]:
        print(f"[{fmt_srt(s['start'])}] {s['text'].strip()}")
        print(f"          → {s.get('vi_text','')}")
        print()
    print("🎉 Hoàn thành!")

if __name__ == "__main__":
    main()
