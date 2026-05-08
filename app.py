import os
import sys
import json
import time
import threading
import subprocess
import uuid
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import whisper
import requests

# ── Fix FFmpeg path trên Windows ──────────────────────────────────────────────
def _find_ffmpeg():
    """Tìm ffmpeg/ffprobe trong các vị trí phổ biến trên Windows và Linux."""
    import shutil
    candidates = ["ffmpeg"]
    if sys.platform == "win32":
        # Thêm các thư mục phổ biến trên Windows
        win_paths = [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\Program Files (x86)\ffmpeg\bin",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin"),
            os.path.join(os.environ.get("USERPROFILE", ""), "ffmpeg", "bin"),
        ]
        for p in win_paths:
            fp = os.path.join(p, "ffmpeg.exe")
            if os.path.isfile(fp):
                # Thêm vào PATH để subprocess và whisper đều tìm thấy
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                print(f"✅ Tìm thấy FFmpeg tại: {p}")
                return p
    found = shutil.which("ffmpeg")
    if found:
        print(f"✅ FFmpeg trong PATH: {found}")
        return str(Path(found).parent)
    return None

FFMPEG_DIR = _find_ffmpeg()
FFMPEG_BIN  = os.path.join(FFMPEG_DIR, "ffmpeg")  if FFMPEG_DIR else "ffmpeg"
FFPROBE_BIN = os.path.join(FFMPEG_DIR, "ffprobe") if FFMPEG_DIR else "ffprobe"
if sys.platform == "win32":
    FFMPEG_BIN  += ".exe"
    FFPROBE_BIN += ".exe"

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = None      # Không giới hạn kích thước upload
app.config["MAX_FORM_MEMORY_SIZE"] = None    # Werkzeug 3.x: không giới hạn form memory
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": f"Bad request: {str(e)}"}), 400

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": f"Lỗi server: {str(e)}"}), 500

@app.errorhandler(Exception)
def unhandled(e):
    import traceback
    return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

UPLOAD_FOLDER = Path("uploads")
OUTPUT_FOLDER = Path("output")
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

jobs = {}
VIDEO_EXTS = {'mp4', 'mkv', 'avi', 'mov', 'webm'}
AUDIO_EXTS = {'mp3', 'wav', 'm4a', 'flac', 'ogg'}

# ── Tự động xóa file sau khi hoàn thành ──────────────────────────────────────
FILE_TTL = int(os.environ.get("SUBVIET_TTL", 1800))  # giây, mặc định 30 phút

def _cleanup_loop():
    """Chạy ngầm, quét mỗi 60s, xóa job hết hạn."""
    while True:
        time.sleep(60)
        now = time.time()
        expired = [jid for jid, j in list(jobs.items())
                   if j.get("status") in ("done", "error")
                   and now - j.get("done_at", now) >= FILE_TTL]
        for jid in expired:
            j = jobs.pop(jid, {})
            # Xóa tất cả output files
            for fp in j.get("outputs", {}).values():
                try: Path(fp).unlink(missing_ok=True)
                except: pass
            # Xóa file upload còn sót (nếu có)
            for ext in ("mp4","mkv","avi","mov","webm","mp3","wav","m4a","flac","ogg"):
                stray = UPLOAD_FOLDER / f"{jid}.{ext}"
                try: stray.unlink(missing_ok=True)
                except: pass
            print(f"[cleanup] Job {jid} da xoa (het TTL {FILE_TTL}s)")
        # Xóa orphan files trong output/ không thuộc job nào
        known_files = set()
        for j in jobs.values():
            for fp in j.get("outputs", {}).values():
                known_files.add(str(Path(fp).resolve()))
        for f in OUTPUT_FOLDER.iterdir():
            if f.is_file() and str(f.resolve()) not in known_files:
                age = now - f.stat().st_mtime
                if age > FILE_TTL * 2:
                    try: f.unlink(); print(f"[cleanup] Orphan xoa: {f.name}")
                    except: pass

threading.Thread(target=_cleanup_loop, daemon=True).start()
ALLOWED_EXTENSIONS = VIDEO_EXTS | AUDIO_EXTS

def allowed_file(f): return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS
def is_video(f):     return '.' in f and f.rsplit('.',1)[1].lower() in VIDEO_EXTS

def sync_segments(segments, snap_ms=80, min_dur=0.4, gap_ms=30):
    """
    Hậu xử lý timing để phụ đề khớp khít với âm thanh:
    - snap_ms : kéo start lên sớm hơn snap_ms ms (mắt người cần ~80ms để nhận chữ)
    - min_dur : thời lượng tối thiểu mỗi segment (giây) để không bị flash
    - gap_ms  : khoảng cách tối thiểu giữa 2 segment liên tiếp (ms), tránh chồng chéo
    """
    if not segments:
        return segments
    snap = snap_ms / 1000.0
    gap  = gap_ms  / 1000.0
    out = []
    for s in segments:
        seg = dict(s)
        text = seg.get("text", "").strip()
        if not text:
            continue
        # Loại bỏ segment Whisper hallucinate (no_speech_prob cao)
        if seg.get("no_speech_prob", 0) > 0.6:
            continue
        # Nếu có word_timestamps → dùng timestamp từ đầu/cuối thay vì timestamp segment
        # (chính xác hơn vì Whisper tính segment timestamp theo chunk audio 30s)
        words = seg.get("words", [])
        if words:
            valid_words = [w for w in words if w.get("start") is not None and w.get("end") is not None]
            if valid_words:
                seg["start"] = valid_words[0]["start"]
                seg["end"]   = valid_words[-1]["end"]
        # Kéo start lên sớm hơn để chữ xuất hiện đúng lúc miệng mở
        seg["start"] = max(0.0, seg["start"] - snap)
        # Đảm bảo thời lượng tối thiểu
        if seg["end"] - seg["start"] < min_dur:
            seg["end"] = seg["start"] + min_dur
        out.append(seg)
    # Giải quyết chồng chéo: end trước không vượt quá start sau
    for i in range(len(out) - 1):
        max_end = out[i + 1]["start"] - gap
        if out[i]["end"] > max_end:
            out[i]["end"] = max(out[i]["start"] + min_dur * 0.5, max_end)
    return out

def fmt_srt(s):
    h,m=int(s//3600),int((s%3600)//60); sec,ms=int(s%60),int((s%1)*1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
def fmt_vtt(s):
    h,m=int(s//3600),int((s%3600)//60); sec,ms=int(s%60),int((s%1)*1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"

def generate_srt(segs, key="vi_text"):
    out=[]
    for i,s in enumerate(segs,1):
        out.append(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{s.get(key,s['text']).strip()}\n")
    return "\n".join(out)

def generate_vtt(segs, key="vi_text"):
    out=["WEBVTT\n"]
    for s in segs:
        out.append(f"{fmt_vtt(s['start'])} --> {fmt_vtt(s['end'])}\n{s.get(key,s['text']).strip()}\n")
    return "\n".join(out)

def generate_bilingual_srt(segs):
    out=[]
    for i,s in enumerate(segs,1):
        vi=s.get("vi_text",s["text"]).strip(); orig=s["text"].strip()
        out.append(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{orig}\n{vi}\n")
    return "\n".join(out)

def generate_ass(segs, style_name="classic", bilingual=False, font_size=None):
    STYLES = {
        "classic": dict(fn="DejaVu Sans",fs=24,pc="&H00FFFFFF",oc="&H00000000",bc="&H80000000",bold=0,bs=1,ol=2.5,sh=1.5,mv=28),
        "cinema":  dict(fn="DejaVu Sans",fs=26,pc="&H00FFE566",oc="&H00000000",bc="&H80000000",bold=1,bs=1,ol=2.5,sh=2,mv=32),
        "netflix": dict(fn="DejaVu Sans",fs=26,pc="&H00FFFFFF",oc="&H00000000",bc="&HB4000000",bold=0,bs=3,ol=0,sh=0,mv=35),
        "neon":    dict(fn="DejaVu Sans",fs=26,pc="&H0000FFFF",oc="&H00003366",bc="&H80000000",bold=1,bs=1,ol=3,sh=2,mv=30),
        "minimal": dict(fn="DejaVu Sans",fs=22,pc="&H00FFFFFF",oc="&H00000000",bc="&H00000000",bold=0,bs=1,ol=1.5,sh=0,mv=25),
        "drama":   dict(fn="DejaVu Sans",fs=24,pc="&H00FFFFFF",oc="&H001A1A1A",bc="&H96000000",bold=0,bs=4,ol=1,sh=0,mv=30),
    }
    st = STYLES.get(style_name, STYLES["classic"])
    if font_size is not None:
        st = {**st, "fs": int(font_size)}
    style_row = (f"Style: Default,{st['fn']},{st['fs']},"
                 f"{st['pc']},&H00FFFF00,{st['oc']},{st['bc']},"
                 f"{st['bold']},0,0,0,100,100,0,0,"
                 f"{st['bs']},{st['ol']},{st['sh']},2,30,30,{st['mv']},1")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_row}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def at(s):
        h=int(s//3600); m=int((s%3600)//60); sv=s%60; cs=int((sv%1)*100)
        return f"{h}:{m:02d}:{int(sv):02d}.{cs:02d}"
    events=[]
    for seg in segs:
        vi=seg.get("vi_text",seg["text"]).strip().replace("\n","\\N")
        orig=seg["text"].strip().replace("\n","\\N")
        ts,te=at(seg["start"]),at(seg["end"])
        if bilingual:
            text=f"{{\\an2\\fs16\\c&H00AAAAAA&\\bord1.5}}{orig}\\N{{\\r}}{vi}"
        else:
            text=vi
        events.append(f"Dialogue: 0,{ts},{te},Default,,0,0,0,,{text}")
    return header+"\n".join(events)

def translate_batch_nim(segments, nim_api_key, genre="general", pronoun_mode="auto"):
    genre_prompts={
        "general": "Đây là hội thoại thông thường — hãy dịch như cách người Việt thực sự nói chuyện với nhau, không cần văn hoa cũng không quá bình dân. Câu cảm thán giữ nguyên cảm xúc, câu bình thường thì tự nhiên, gọn.",
        "romance": "Đây là phim tình cảm — ngôn ngữ cần mềm mại, ấm áp, đôi khi bỏ ngỏ. Những câu thổ lộ hay nhớ nhung nên có chút nao lòng, không quá sến nhưng đủ rung cảm như lời người ta nói thật lúc yêu.",
        "action": "Đây là phim hành động — lời thoại phải gọn, nảy, có lực. Câu lệnh ngắn và dứt khoát, câu đe doạ phải lạnh và sắc, không cần giải thích vòng vo. Ưu tiên nhịp điệu hơn là độ chính xác từng chữ.",
        "comedy": "Đây là phim hài — cần giữ được cái buồn cười trong từng câu. Nếu nguyên bản chơi chữ hay có nhịp hài thì tìm cách Việt hoá thay vì dịch thẳng. Tiếng lóng, cách nói quen thuộc của giới trẻ Việt có thể dùng thoải mái khi phù hợp.",
        "horror": "Đây là phim kinh dị — câu chữ phải tạo ra sự bất an ngay cả khi đọc. Những câu nhẹ nhàng đôi khi đáng sợ hơn câu hét to — hãy giữ lại sự mơ hồ và lạnh người đó. Tránh dùng từ quá thông thường khi nguyên bản có ý rùng rợn.",
        "documentary": "Đây là phim tài liệu hoặc nội dung học thuật — cần chính xác, rõ ràng, và đáng tin. Thuật ngữ chuyên ngành giữ nguyên hoặc dịch kèm chú thích ngắn trong dấu ngoặc nếu cần. Giọng văn trang trọng nhưng không khô cứng.",
        "anime": "Đây là anime hoặc manga — hãy dịch theo cách cộng đồng fan Việt quen đọc. Giữ nguyên các từ Nhật đã phổ biến như senpai, onii-chan, nani, sugoi... Câu cảm thán đặc trưng anime nên giữ năng lượng gốc, không san phẳng thành câu văn xuôi bình thường.",
        "drama": "Đây là phim tâm lý hoặc chính kịch — cảm xúc trong từng câu rất quan trọng. Những lúc nhân vật đau, giận, hay vỡ oà cần được dịch sao cho người xem cảm nhận được, không chỉ hiểu nghĩa. Câu dài có thể giữ nhịp chậm, câu đứt quãng cũng giữ nguyên nếu ý đồ như vậy.",
    }

    # ── Hướng dẫn xưng hô ────────────────────────────────────────────────────
    pronoun_guides = {
        "auto": """Xưng hô: Tiếng Việt không có đại từ trung lập như "I/you" trong tiếng Anh — mỗi cặp đại từ mang theo tuổi tác, vai vế và quan hệ. Hãy suy luận từ ngữ cảnh các câu thoại xung quanh và chọn cặp phù hợp nhất:
- Hai người trẻ đồng trang lứa, thân thiết → tao/mày hoặc tớ/cậu
- Nam nói với nữ lớn hơn hoặc lạ mặt → anh/chị, em xưng em
- Nữ nói với nam nhỏ hơn → chị/em
- Người lớn tuổi nói với trẻ → chú/bác/cô/ông/bà xưng với cháu/con
- Cấp trên với cấp dưới (công sở) → anh/chị xưng tôi hoặc theo vai
- Trang trọng, không rõ quan hệ → tôi/bạn hoặc tôi/anh/chị
- Nhân vật xấu, côn đồ đe doạ → mày/tao thẳng thắn
Giữ nhất quán cặp đại từ cho cùng một nhân vật xuyên suốt cả batch. Nếu ngữ cảnh quá mơ hồ, ưu tiên "tôi" cho ngôi thứ nhất và "anh/chị" cho ngôi thứ hai hơn là để trống hoặc dùng "bạn" vô hồn.""",

        "anh-em": """Xưng hô cố định: Nhân vật nam chính xưng ANH, gọi đối phương là EM. Dùng nhất quán toàn bộ. Phù hợp phim tình cảm nam-nữ lệch tuổi, hoặc khi anh/em đã rõ từ ngữ cảnh. Các nhân vật phụ xung quanh vẫn suy luận theo ngữ cảnh.""",

        "chi-em": """Xưng hô cố định: Nhân vật nữ lớn hơn xưng CHỊ, gọi đối phương là EM. Dùng nhất quán toàn bộ. Phù hợp phim có cặp nữ lệch tuổi hoặc chị-em gái. Các nhân vật phụ xung quanh vẫn suy luận theo ngữ cảnh.""",

        "may-tao": """Xưng hô cố định: Dùng MÀY/TAO xuyên suốt cho các nhân vật chính. Phù hợp phim bạn bè thân thiết, môi trường lao động bình dân, hoặc khi cần giữ sắc thái thô ráp, chân thực. Lưu ý: không dùng mày/tao cho câu trang trọng hay cảnh gặp người lạ — hãy tự điều chỉnh cho tự nhiên.""",

        "co-chau": """Xưng hô cố định: Người lớn xưng CÔ/CHÚ/BÁC, gọi người trẻ là CHÁU. Người trẻ xưng CHÁU, gọi lại CÔ/CHÚ/BÁC. Phù hợp phim gia đình, thầy-trò, hoặc khi có nhân vật chênh lệch tuổi rõ rệt.""",

        "ong-ba": """Xưng hô cố định: Người cao tuổi xưng ÔNG/BÀ, gọi người trẻ là CON hoặc CHÁU. Người trẻ xưng CON/CHÁU, gọi lại ÔNG/BÀ. Phù hợp phim cổ trang, gia đình có ông bà, hoặc nhân vật lớn tuổi đáng kính.""",
    }

    style_note = genre_prompts.get(genre, genre_prompts["general"])
    pronoun_note = pronoun_guides.get(pronoun_mode, pronoun_guides["auto"])
    numbered = "\n".join([f"{i+1}. {s['text'].strip()}" for i,s in enumerate(segments)])

    system_prompt = f"""Bạn là người dịch phụ đề phim lâu năm, quen với nhiều thể loại và hiểu rằng dịch phụ đề khác hoàn toàn với dịch văn bản thông thường — người xem chỉ có vài giây để đọc, nên mỗi câu cần vừa chính xác vừa tự nhiên vừa gọn.

{style_note}

{pronoun_note}

Một vài điều cần nhớ khi làm việc:
Tên người, tên địa danh nước ngoài thì giữ nguyên, không phiên âm trừ khi đã có tên Việt thông dụng. Đừng thêm giải thích hay chú thích vào trong phụ đề — nếu câu gốc không có, bản dịch cũng không cần. Khi gặp câu khó dịch sát nghĩa, ưu tiên giữ đúng cảm xúc và ý chính hơn là bám từng chữ.

Bạn sẽ nhận được {len(segments)} dòng thoại đánh số. Trả về đúng {len(segments)} dòng theo định dạng:
1. [bản dịch tiếng Việt]
2. [bản dịch tiếng Việt]
...

Chỉ trả về các dòng dịch, không thêm bất cứ nội dung nào khác."""
    headers={"Authorization":f"Bearer {nim_api_key}","Content-Type":"application/json"}
    payload={"model":"meta/llama-3.3-70b-instruct",
             "messages":[{"role":"system","content":system_prompt},
                         {"role":"user","content":f"Dịch các dòng thoại sau sang tiếng Việt:\n\n{numbered}"}],
             "temperature":0.3,"max_tokens":4096,"top_p":0.9}
    try:
        resp=requests.post("https://integrate.api.nvidia.com/v1/chat/completions",
                           headers=headers,json=payload,timeout=90)
        resp.raise_for_status()
        content=resp.json()["choices"][0]["message"]["content"].strip()
        translations={}
        for line in content.split("\n"):
            line=line.strip()
            if not line or not line[0].isdigit(): continue
            for sep in [". ",")"]:
                if sep in line:
                    parts=line.split(sep,1)
                    if len(parts)==2:
                        try: translations[int(parts[0].strip())-1]=parts[1].strip(); break
                        except: continue
        return [translations.get(i,s["text"]) for i,s in enumerate(segments)]
    except Exception as e:
        print(f"NIM API error: {e}")
        return [s["text"] for s in segments]

def burn_subtitles(video_path, ass_path, output_path, quality=23):
    ass_esc=str(ass_path).replace("\\","/").replace(":","\\:")
    cmd=[FFMPEG_BIN,"-i",str(video_path),
         "-vf",f"ass={ass_esc}",
         "-c:v","libx264","-crf",str(quality),"-preset","fast",
         "-c:a","copy","-movflags","+faststart",
         str(output_path),"-y","-loglevel","error"]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=7200)
    if r.returncode!=0:
        raise RuntimeError(f"FFmpeg: {r.stderr[:500]}")

def get_video_info(vp):
    try:
        cmd=[FFPROBE_BIN,"-v","quiet","-print_format","json","-show_streams","-show_format",str(vp)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        data=json.loads(r.stdout)
        dur=float(data.get("format",{}).get("duration",0))
        w=h=0
        for st in data.get("streams",[]):
            if st.get("codec_type")=="video": w=st.get("width",0); h=st.get("height",0); break
        return {"duration":dur,"width":w,"height":h}
    except: return {"duration":0,"width":0,"height":0}

def process_video(job_id, file_path, nim_api_key, whisper_model_size, genre,
                  source_lang, bilingual, burn_in, sub_style, video_quality,
                  font_size=None, pronoun_mode="auto"):
    job=jobs[job_id]
    def upd(status,progress,message,**kw): job.update({"status":status,"progress":progress,"message":message,**kw})
    file_path=Path(file_path)
    is_vid=is_video(file_path.name)
    try:
        # Kiểm tra FFmpeg trước khi load Whisper
        if not FFMPEG_DIR:
            import shutil
            if not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "Không tìm thấy FFmpeg! Hãy cài FFmpeg và thêm vào PATH.\n"
                    "Tải tại: https://www.gyan.dev/ffmpeg/builds/ (Windows)\n"
                    "Sau khi cài, thêm thư mục bin vào biến môi trường PATH rồi khởi động lại ứng dụng."
                )
        upd("processing",5,f"⚙️ Đang tải model Whisper ({whisper_model_size})...")
        model=whisper.load_model(whisper_model_size)
        upd("processing",18,"🎙️ Đang nhận dạng giọng nói...")
        opts={
            "verbose": False,
            "task": "transcribe",
            # Cải thiện độ chính xác timing
            "word_timestamps": True,          # bật timestamp cấp độ từ để sync chính xác hơn
            "condition_on_previous_text": True,# giữ ngữ cảnh câu trước → ít hallucination hơn
            "no_speech_threshold": 0.6,        # lọc segment im lặng bị nhận nhầm
            "compression_ratio_threshold": 2.4,# loại bỏ output lặp (hallucination)
            "prepend_punctuations": "\"'¿([{-",
            "append_punctuations": "\"'.。,，!！?？:：\")]}、",
            "temperature": (0.0, 0.2, 0.4),   # thử lại với nhiệt độ cao hơn nếu thất bại
        }
        if source_lang!="auto": opts["language"]=source_lang
        result=model.transcribe(str(file_path),**opts)
        segments=result["segments"]; detected_lang=result.get("language","unknown")
        # Hậu xử lý timing: snap sớm 80ms, lọc hallucination, fix chồng chéo
        segments=sync_segments(segments, snap_ms=80, min_dur=0.4, gap_ms=30)
        upd("processing",38,f"✅ Nhận dạng xong — {len(segments)} đoạn | Ngôn ngữ: {detected_lang.upper()}")
        upd("processing",42,"🌐 Bắt đầu dịch sang tiếng Việt tự nhiên...")
        BATCH=20; translated=[]; total_b=(len(segments)+BATCH-1)//BATCH
        for bi,bs in enumerate(range(0,len(segments),BATCH)):
            batch=segments[bs:bs+BATCH]
            pct=42+int(((bi+1)/total_b)*38)
            upd("processing",pct,f"🔄 Dịch batch {bi+1}/{total_b} ({len(batch)} đoạn)...")
            trans=translate_batch_nim(batch,nim_api_key,genre,pronoun_mode)
            for seg,vi in zip(batch,trans): translated.append({**seg,"vi_text":vi})
            time.sleep(0.3)
        upd("processing",82,"📝 Đang tạo file phụ đề...")
        base=file_path.stem; outputs={}
        p=OUTPUT_FOLDER/f"{job_id}_{base}_vi.srt"; p.write_text(generate_srt(translated),encoding="utf-8"); outputs["srt_vi"]=str(p)
        p=OUTPUT_FOLDER/f"{job_id}_{base}_vi.vtt"; p.write_text(generate_vtt(translated),encoding="utf-8"); outputs["vtt_vi"]=str(p)
        p=OUTPUT_FOLDER/f"{job_id}_{base}_original.srt"; p.write_text(generate_srt(translated,key="text"),encoding="utf-8"); outputs["srt_original"]=str(p)
        if bilingual:
            p=OUTPUT_FOLDER/f"{job_id}_{base}_bilingual.srt"; p.write_text(generate_bilingual_srt(translated),encoding="utf-8"); outputs["srt_bilingual"]=str(p)
        ass_path=OUTPUT_FOLDER/f"{job_id}_{base}_vi.ass"
        ass_path.write_text(generate_ass(translated,style_name=sub_style,bilingual=bilingual,font_size=font_size),encoding="utf-8")
        outputs["ass_vi"]=str(ass_path)
        video_burned=None
        if burn_in and is_vid:
            upd("processing",86,"🎬 Đang render phụ đề vào video (có thể mất vài phút)...")
            burned_path=OUTPUT_FOLDER/f"{job_id}_{base}_subtitled.mp4"
            try:
                burn_subtitles(file_path,ass_path,burned_path,quality=video_quality)
                outputs["video_burned"]=str(burned_path); video_burned=str(burned_path)
                upd("processing",96,"✅ Render video xong!")
            except Exception as e:
                upd("processing",96,f"⚠️ Render thất bại: {e} — Bạn vẫn có thể tải SRT/ASS")
        preview=[{"start":fmt_srt(s["start"]),"end":fmt_srt(s["end"]),
                  "original":s["text"].strip(),"vietnamese":s.get("vi_text","")}
                 for s in translated[:12]]
        vinfo=get_video_info(file_path) if is_vid else {}
        now=time.time()
        upd("done",100,"✅ Hoàn thành!",outputs=outputs,preview=preview,is_video=is_vid,
            done_at=now,expires_at=now+FILE_TTL,
            video_burned=video_burned,
            stats={"total_segments":len(translated),"detected_language":detected_lang,
                   "genre":genre,"sub_style":sub_style,"font_size":font_size,
                   "pronoun_mode":pronoun_mode,
                   "burned":burn_in and is_vid and bool(video_burned),"video_info":vinfo})
    except Exception as e:
        import traceback; upd("error",0,f"❌ Lỗi: {str(e)}",error=traceback.format_exc())
    finally:
        try: file_path.unlink(missing_ok=True)
        except: pass

@app.route("/upload_preview",methods=["POST"])
def upload_preview():
    """Lưu file tạm để preview frame, không xử lý. Trả về preview_id."""
    if "file" not in request.files:
        return jsonify({"error":"Không có file"}),400
    file=request.files["file"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error":"Định dạng không hỗ trợ"}),400
    preview_id="pv_"+str(uuid.uuid4())[:8]
    ext=file.filename.rsplit(".",1)[1].lower()
    save_path=UPLOAD_FOLDER/f"{preview_id}.{ext}"
    file.save(str(save_path))
    jobs[preview_id]={"status":"preview","filename":file.filename,"outputs":{},"preview":[]}
    return jsonify({"preview_id":preview_id})

@app.route("/")
def index(): return render_template("index.html")

@app.route("/preview_raw_frame",methods=["POST"])
def preview_raw_frame():
    """Trích 1 frame từ video đã upload (dùng job_id, không upload lại file)."""
    import base64
    job_id=request.form.get("job_id","").strip()
    if not job_id or job_id not in jobs:
        return jsonify({"error":"job_id không hợp lệ hoặc chưa upload file"}),400
    # Tìm file video đã lưu
    job=jobs[job_id]
    filename=job.get("filename","")
    if not filename:
        return jsonify({"error":"Không tìm thấy thông tin file"}),400
    ext=filename.rsplit(".",1)[1].lower() if "." in filename else ""
    video_path=UPLOAD_FOLDER/f"{job_id}.{ext}"
    if not video_path.exists():
        return jsonify({"error":"File video đã bị xóa hoặc chưa upload"}),400
    tmp_id=str(uuid.uuid4())[:8]
    tmp_png=UPLOAD_FOLDER/f"pf_{tmp_id}.png"
    try:
        info=get_video_info(video_path)
        dur=info.get("duration",0)
        seek=min(max(dur*0.3,2),dur-1) if dur>3 else 0
        cmd=[FFMPEG_BIN,"-ss",str(seek),"-i",str(video_path),
             "-frames:v","1","-q:v","3","-update","1",
             str(tmp_png),"-y","-loglevel","error"]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        if r.returncode!=0:
            return jsonify({"error":f"FFmpeg lỗi: {r.stderr[:300]}"}),500
        b64=base64.b64encode(tmp_png.read_bytes()).decode()
        return jsonify({"ok":True,"image":f"data:image/png;base64,{b64}",
                        "width":info.get("width",0),"height":info.get("height",0)})
    except Exception as e:
        import traceback
        return jsonify({"error":str(e),"detail":traceback.format_exc()}),500
    finally:
        try: tmp_png.unlink(missing_ok=True)
        except: pass

@app.route("/preview_frame",methods=["POST"])
def preview_frame():
    """Overlay ASS lên frame PNG base64 đã có sẵn, trả về PNG base64 mới. Không cần re-upload video."""
    import base64
    frame_b64=request.form.get("frame","")
    if not frame_b64:
        return jsonify({"error":"Thiếu frame"}),400
    # Xóa data URI prefix nếu có
    if "," in frame_b64:
        frame_b64=frame_b64.split(",",1)[1]
    sub_style=request.form.get("sub_style","classic")
    raw_fs=request.form.get("font_size","")
    font_size=int(raw_fs) if raw_fs.isdigit() and 10<=int(raw_fs)<=72 else None
    bilingual=request.form.get("bilingual","false").lower()=="true"
    tmp_id=str(uuid.uuid4())[:8]
    tmp_png_in=UPLOAD_FOLDER/f"pfo_{tmp_id}_in.png"
    tmp_ass=UPLOAD_FOLDER/f"pfo_{tmp_id}.ass"
    tmp_png_out=UPLOAD_FOLDER/f"pfo_{tmp_id}_out.png"
    try:
        tmp_png_in.write_bytes(base64.b64decode(frame_b64))
        sample_seg=[{"start":0,"end":5,"text":"Sample subtitle","vi_text":"Phụ đề mẫu — Thử nghiệm cỡ chữ và kiểu hiển thị"}]
        ass_content=generate_ass(sample_seg,style_name=sub_style,bilingual=bilingual,font_size=font_size)
        tmp_ass.write_text(ass_content,encoding="utf-8")
        ass_esc=str(tmp_ass).replace("\\","/").replace(":","\\:")
        cmd=[FFMPEG_BIN,"-i",str(tmp_png_in),
             "-vf",f"ass={ass_esc}",
             "-frames:v","1","-update","1",
             str(tmp_png_out),"-y","-loglevel","error"]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        if r.returncode!=0:
            return jsonify({"error":f"FFmpeg lỗi: {r.stderr[:300]}"}),500
        b64=base64.b64encode(tmp_png_out.read_bytes()).decode()
        return jsonify({"ok":True,"image":f"data:image/png;base64,{b64}"})
    except Exception as e:
        import traceback
        return jsonify({"error":str(e),"detail":traceback.format_exc()}),500
    finally:
        for p in [tmp_png_in,tmp_ass,tmp_png_out]:
            try: p.unlink(missing_ok=True)
            except: pass

@app.route("/upload",methods=["POST"])
def upload():
    if "file" not in request.files: return jsonify({"error":"Không có file"}),400
    file=request.files["file"]
    if not file.filename or not allowed_file(file.filename): return jsonify({"error":"Định dạng không hỗ trợ"}),400
    nim_api_key=request.form.get("nim_api_key","").strip()
    if not nim_api_key: return jsonify({"error":"Cần nhập NVIDIA NIM API Key"}),400
    whisper_model=request.form.get("whisper_model","base")
    genre=request.form.get("genre","general")
    source_lang=request.form.get("source_lang","auto")
    bilingual=request.form.get("bilingual","false").lower()=="true"
    burn_in=request.form.get("burn_in","false").lower()=="true"
    sub_style=request.form.get("sub_style","classic")
    video_quality=int(request.form.get("video_quality","23"))
    raw_fs=request.form.get("font_size","")
    font_size=int(raw_fs) if raw_fs.isdigit() and 10<=int(raw_fs)<=72 else None
    pronoun_mode="auto"
    job_id=str(uuid.uuid4())[:8]
    ext=file.filename.rsplit(".",1)[1].lower()
    file_path=UPLOAD_FOLDER/f"{job_id}.{ext}"
    file.save(str(file_path))
    jobs[job_id]={"status":"queued","progress":0,"message":"⏳ Đang xếp hàng...",
                  "filename":file.filename,"is_video":is_video(file.filename),
                  "outputs":{},"preview":[],"stats":{}}
    threading.Thread(target=process_video,
        args=(job_id,file_path,nim_api_key,whisper_model,genre,
              source_lang,bilingual,burn_in,sub_style,video_quality,font_size,pronoun_mode),daemon=True).start()
    return jsonify({"job_id":job_id,"is_video":is_video(file.filename)})

@app.route("/status/<job_id>")
def status(job_id):
    if job_id not in jobs: return jsonify({"error":"Không tồn tại"}),404
    return jsonify(jobs[job_id])

@app.route("/download/<job_id>/<file_type>")
def download(job_id,file_type):
    if job_id not in jobs: return jsonify({"error":"Không tồn tại"}),404
    outputs=jobs[job_id].get("outputs",{})
    if file_type not in outputs: return jsonify({"error":"File không tồn tại"}),404
    fp=Path(outputs[file_type])
    if not fp.exists(): return jsonify({"error":"File đã bị xóa"}),404
    return send_file(str(fp),as_attachment=True,download_name=fp.name)

@app.route("/file_status/<job_id>")
def file_status(job_id):
    """Kiểm tra xem các file output của job còn tồn tại không."""
    if job_id not in jobs:
        return jsonify({"exists":False,"expired":True,"files":{}})
    j = jobs[job_id]
    outputs = j.get("outputs", {})
    files = {k: Path(v).exists() for k, v in outputs.items()}
    any_exists = any(files.values())
    expires_at = j.get("expires_at", 0)
    now = time.time()
    remaining = max(0, int(expires_at - now)) if expires_at else None
    return jsonify({
        "exists": any_exists,
        "expired": not any_exists,
        "expires_at": expires_at,
        "remaining_seconds": remaining,
        "files": files
    })

if __name__=="__main__":
    print("🎬 SubViet — Auto Subtitle + Burn-In")
    print("Truy cập: http://localhost:5000")
    app.run(debug=True,host="0.0.0.0",port=5000)
