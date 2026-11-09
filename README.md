# SUBTITLE AI — Automatic Subtitle Studio

Xây dựng mới nền tảng web tiếng Việt để tạo phụ đề tự động, trích xuất subtitle track, chia nhỏ SRT/VTT và biên tập phụ đề.

## Kiến trúc
- Frontend: React + TypeScript + Vite, deploy trên Vercel.
- Media chunking: FFmpeg.wasm chạy trong trình duyệt, chia âm thanh thành đoạn 8 phút.
- Speech-to-text: Groq Whisper Large V3 Turbo.
- API gateway: Cloudflare Worker giữ Groq API key ở phía server.
- Editor: nhập, sửa câu/timestamp và xuất SRT/VTT.
- PWA: manifest + service worker.

## Chạy local
1. npm install
2. Sao chép .env.example thành .env và điền VITE_WORKER_URL.
3. npm run dev

## Deploy Cloudflare Worker
1. Tạo Groq API key tại https://console.groq.com/keys.
2. Cài Wrangler: npm install -g wrangler
3. cd worker && wrangler login
4. wrangler secret put GROQ_API_KEY
5. wrangler deploy
6. Sao chép URL Worker.

## Deploy Vercel
1. Import repository khahdihdz/Auto-Subtitle vào Vercel.
2. Framework: Vite; Build command: npm run build; Output: dist.
3. Thêm Environment Variable VITE_WORKER_URL bằng URL Worker.
4. Redeploy.

## GitHub Actions
CI chạy typecheck và build. Worker tự deploy nếu đã cấu hình GitHub Actions secrets CLOUDFLARE_API_TOKEN và CLOUDFLARE_ACCOUNT_ID. Groq key phải được đặt riêng trong Worker bằng Wrangler secret.

## Tính năng hiện có
- Tạo phụ đề từ video/audio qua Groq Whisper.
- Chia audio thành đoạn 8 phút, gửi tuần tự và ghép timestamp.
- Retry giới hạn khi gặp lỗi 429 hoặc lỗi tạm thời.
- Tách subtitle track văn bản có sẵn qua FFmpeg.wasm nếu codec được hỗ trợ.
- Nhập SRT, chỉnh sửa nội dung và timestamp, thêm/xóa câu.
- Chia file theo thời lượng; xuất SRT và VTT.
- PWA cài đặt được trên thiết bị hỗ trợ.

## Giới hạn kỹ thuật
- Groq nhận tối đa 25 MB mỗi request; mỗi audio chunk được kiểm tra dưới 24 MB.
- FFmpeg.wasm cần tải WebAssembly core lần đầu và dùng RAM đáng kể trên điện thoại. Video rất lớn hoặc thiết bị ít RAM nên dùng media-processing service riêng chạy FFmpeg + queue + R2.
- Subtitle bitmap (PGS/VobSub) cần OCR riêng, không thể trích xuất thành text bằng cách remux thông thường.
- Bản này chưa bật dịch phụ đề, burn-in video, diarization hay lưu trữ cloud job dài hạn.
- Không chạy FFmpeg nặng trên Cloudflare Workers hoặc Vercel Functions.

## Bảo mật
Groq API key chỉ nằm trong Cloudflare Worker secret. Không đặt key trong frontend, biến VITE_* hoặc commit GitHub.

## License
MIT
