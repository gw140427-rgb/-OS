# TikTok Video Watch API

A small API that downloads a public TikTok video, extracts representative frames with FFmpeg, and transcribes audio with Faster-Whisper. It is intended as a backend prototype for an AI video-summary plugin.

## Deploy on Render

- Runtime: Python
- Build command: `apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Plan: Free (limited RAM/CPU; first request may be slow while Whisper downloads its tiny model)

## API

- `GET /` service status
- `GET /health` health check
- `POST /analyze` JSON body: `{"url":"https://www.tiktok.com/@user/video/123"}`

The endpoint only accepts TikTok HTTPS URLs, rejects videos longer than 90 seconds by default, and limits downloads to 80 MB. Video processing is temporary and files are deleted after the request. It does not bypass private-video access controls; some TikTok URLs may fail due to platform restrictions.

## Important limitations

This API returns base64-encoded frames and a transcript; it does not itself produce a natural-language summary. The calling AI must inspect the frames and summarize them together with the transcript. The free Render plan may run out of memory or time during Whisper inference, so the deployed service must be tested before claiming full support.
