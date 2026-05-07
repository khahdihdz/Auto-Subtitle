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

UPLOAD_FOLDER = Path("uploads")
OUTPUT_FOLDER = Path("output")
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

jobs = {}
VIDEO_EXTS = {'mp4', 'mkv', 'avi', 'mov', 'webm'}
AUDIO_EXTS = {'mp3', 'wav', 'm4a', 'flac', 'ogg'}
ALLOWED_EXTENSIONS = VIDEO_EXTS | AUDIO_EXTS

def allowed_file(f): return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS
def is_video(f):     return '.' in f and f.rsplit('.',1)[1].lower() in VIDEO_EXTS

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

def generate_ass(segs, style_name="classic", bilingual=False):
    STYLES = {
        "classic": dict(fn="DejaVu Sans",fs=24,pc="&H00FFFFFF",oc="&H00000000",bc="&H80000000",bold=0,bs=1,ol=2.5,sh=1.5,mv=28),
        "cinema":  dict(fn="DejaVu Sans",fs=26,pc="&H00FFE566",oc="&H00000000",bc="&H80000000",bold=1,bs=1,ol=2.5,sh=2,mv=32),
        "netflix": dict(fn="DejaVu Sans",fs=26,pc="&H00FFFFFF",oc="&H00000000",bc="&HB4000000",bold=0,bs=3,ol=0,sh=0,mv=35),
        "neon":    dict(fn="DejaVu Sans",fs=26,pc="&H0000FFFF",oc="&H00003366",bc="&H80000000",bold=1,bs=1,ol=3,sh=2,mv=30),
        "minimal": dict(fn="DejaVu Sans",fs=22,pc="&H00FFFFFF",oc="&H00000000",bc="&H00000000",bold=0,bs=1,ol=1.5,sh=0,mv=25),
        "drama":   dict(fn="DejaVu Sans",fs=24,pc="&H00FFFFFF",oc="&H001A1A1A",bc="&H96000000",bold=0,bs=4,ol=1,sh=0,mv=30),
    }
    st = STYLES.get(style_name, STYLES["classic"])
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

def translate_batch_nim(segments, nim_api_key, genre="general"):
    genre_prompts={
        "general":"Dịch tự nhiên, rõ ràng như người Việt bản địa nói chuyện hàng ngày.",
        "romance":"Dịch lãng mạn, ngọt ngào, tình cảm như phim tình cảm Việt.",
        "action":"Dịch mạnh mẽ, dứt khoát, năng động như phim hành động.",
        "comedy":"Dịch hài hước, vui tươi, giữ được tính hài. Dùng tiếng lóng Việt tự nhiên.",
        "horror":"Dịch rùng rợn, căng thẳng, tạo cảm giác bí ẩn và đáng sợ.",
        "documentary":"Dịch chính xác, học thuật nhưng dễ hiểu. Trang trọng và rõ ràng.",
        "anime":"Dịch theo phong cách anime/manga Việt. Giữ từ Nhật quen thuộc như onii-chan, senpai.",
        "drama":"Dịch đầy cảm xúc, sâu sắc, chân thực như phim tâm lý Việt Nam.",
    }
    style_note=genre_prompts.get(genre,genre_prompts["general"])
    numbered="\n".join([f"{i+1}. {s['text'].strip()}" for i,s in enumerate(segments)])
    system_prompt=f"""Bạn là chuyên gia dịch phụ đề phim chuyên nghiệp người Việt Nam.
Nhiệm vụ: Dịch các dòng thoại sang tiếng Việt TỰ NHIÊN, KHÔNG MÁY MÓC.
Nguyên tắc:
- Dịch như người Việt thật sự nói, không dịch word-by-word
- Giữ cảm xúc và sắc thái của câu gốc
- Câu ngắn gọn, dễ đọc khi xem phim
- {style_note}
- KHÔNG thêm giải thích hay ngoặc đơn
- KHÔNG dịch tên riêng, địa danh nước ngoài
Trả về ĐÚNG {len(segments)} dòng đánh số:
1. [bản dịch]
Không có text nào khác."""
    headers={"Authorization":f"Bearer {nim_api_key}","Content-Type":"application/json"}
    payload={"model":"meta/llama-3.3-70b-instruct",
             "messages":[{"role":"system","content":system_prompt},
                         {"role":"user","content":f"Dịch sang tiếng Việt:\n\n{numbered}"}],
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
                  source_lang, bilingual, burn_in, sub_style, video_quality):
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
        opts={"verbose":False,"task":"transcribe"}
        if source_lang!="auto": opts["language"]=source_lang
        result=model.transcribe(str(file_path),**opts)
        segments=result["segments"]; detected_lang=result.get("language","unknown")
        upd("processing",38,f"✅ Nhận dạng xong — {len(segments)} đoạn | Ngôn ngữ: {detected_lang.upper()}")
        upd("processing",42,"🌐 Bắt đầu dịch sang tiếng Việt tự nhiên...")
        BATCH=20; translated=[]; total_b=(len(segments)+BATCH-1)//BATCH
        for bi,bs in enumerate(range(0,len(segments),BATCH)):
            batch=segments[bs:bs+BATCH]
            pct=42+int(((bi+1)/total_b)*38)
            upd("processing",pct,f"🔄 Dịch batch {bi+1}/{total_b} ({len(batch)} đoạn)...")
            trans=translate_batch_nim(batch,nim_api_key,genre)
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
        ass_path.write_text(generate_ass(translated,style_name=sub_style,bilingual=bilingual),encoding="utf-8")
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
        upd("done",100,"✅ Hoàn thành!",outputs=outputs,preview=preview,is_video=is_vid,
            video_burned=video_burned,
            stats={"total_segments":len(translated),"detected_language":detected_lang,
                   "genre":genre,"sub_style":sub_style,
                   "burned":burn_in and is_vid and bool(video_burned),"video_info":vinfo})
    except Exception as e:
        import traceback; upd("error",0,f"❌ Lỗi: {str(e)}",error=traceback.format_exc())
    finally:
        try: file_path.unlink(missing_ok=True)
        except: pass

@app.route("/")
def index(): return render_template("index.html")

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
    job_id=str(uuid.uuid4())[:8]
    ext=file.filename.rsplit(".",1)[1].lower()
    file_path=UPLOAD_FOLDER/f"{job_id}.{ext}"
    file.save(str(file_path))
    jobs[job_id]={"status":"queued","progress":0,"message":"⏳ Đang xếp hàng...",
                  "filename":file.filename,"is_video":is_video(file.filename),
                  "outputs":{},"preview":[],"stats":{}}
    threading.Thread(target=process_video,
        args=(job_id,file_path,nim_api_key,whisper_model,genre,
              source_lang,bilingual,burn_in,sub_style,video_quality),daemon=True).start()
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

if __name__=="__main__":
    print("🎬 SubViet — Auto Subtitle + Burn-In")
    print("Truy cập: http://localhost:5000")
    app.run(debug=True,host="0.0.0.0",port=5000)
