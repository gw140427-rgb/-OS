import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from yt_dlp import YoutubeDL

app = FastAPI(title="TikTok Video Watch API", version="0.1.0")
MODEL_NAME = os.getenv("WHISPER_MODEL", "tiny")
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "90"))
MAX_FRAMES = int(os.getenv("MAX_FRAMES", "8"))
MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "80"))
ALLOWED_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}

class AnalyzeRequest(BaseModel):
    url: HttpUrl

def validate_url(value: str):
    host = (urlparse(value).hostname or "").lower()
    if host not in ALLOWED_HOSTS and not host.endswith(".tiktok.com"):
        raise HTTPException(status_code=400, detail="Only TikTok links are supported.")
    if urlparse(value).scheme != "https":
        raise HTTPException(status_code=400, detail="HTTPS TikTok links only.")

def run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Video processing timed out.")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "Media processing failed")[-1200:]
        raise HTTPException(status_code=422, detail=detail)

@app.get("/")
def root():
    return {"service":"TikTok Video Watch API", "status":"ok", "usage":"POST /analyze with {url: https://www.tiktok.com/...}"}

@app.get("/health")
def health():
    return {"status":"ok"}

@app.post("/analyze")
def analyze(body: AnalyzeRequest):
    url = str(body.url)
    validate_url(url)
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail="ffmpeg is not installed on this service.")

    with tempfile.TemporaryDirectory(prefix="tiktok-watch-") as tmp:
        work = Path(tmp)
        video_path = work / "source.mp4"
        opts = {
            "outtmpl": str(work / "source.%(ext)s"),
            "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 1,
            "max_filesize": MAX_DOWNLOAD_MB * 1024 * 1024,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            candidates = [p for p in work.iterdir() if p.is_file() and p.name.startswith("source.")]
            if not candidates:
                raise HTTPException(status_code=422, detail="TikTok did not provide a downloadable video.")
            video_path = candidates[0]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not fetch TikTok video: {str(e)[:500]}")

        probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)], timeout=15)
        try:
            duration = float(probe.stdout.strip())
        except Exception:
            duration = 0
        if duration <= 0:
            raise HTTPException(status_code=422, detail="Could not determine video duration.")
        if duration > MAX_VIDEO_SECONDS:
            raise HTTPException(status_code=413, detail=f"Video is {int(duration)} seconds; limit is {MAX_VIDEO_SECONDS} seconds.")

        frames_dir = work / "frames"
        frames_dir.mkdir()
        fps = min(0.5, MAX_FRAMES / max(duration, 1))
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-vf", f"fps={fps},scale=640:-1", "-frames:v", str(MAX_FRAMES), str(frames_dir / "frame-%02d.jpg")], timeout=45)
        frames = []
        for frame in sorted(frames_dir.glob("*.jpg")):
            frames.append({
                "filename": frame.name,
                "mime_type": "image/jpeg",
                "base64": base64.b64encode(frame.read_bytes()).decode("ascii")
            })

        audio_path = work / "audio.wav"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-t", str(MAX_VIDEO_SECONDS), "-y", str(audio_path)], timeout=45)
        transcript = ""
        transcript_error = None
        if audio_path.exists() and audio_path.stat().st_size > 1000:
            try:
                from faster_whisper import WhisperModel
                model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=2)
                segments, _ = model.transcribe(str(audio_path), beam_size=1, vad_filter=True)
                transcript = " ".join(s.text.strip() for s in segments).strip()
                del model
            except Exception as e:
                transcript_error = f"Audio transcription unavailable: {str(e)[:250]}"

        return {
            "title": info.get("title"),
            "duration_seconds": round(duration, 2),
            "source_url": url,
            "frame_count": len(frames),
            "frames": frames,
            "transcript": transcript,
            "transcript_error": transcript_error,
            "note": "Frames and transcript are extracted from the video. The calling AI should summarize the visual events and spoken content together."
        }
