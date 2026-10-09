import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse
from yt_dlp import YoutubeDL

MODEL_NAME = os.getenv("WHISPER_MODEL", "tiny")
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "90"))
MAX_FRAMES = int(os.getenv("MAX_FRAMES", "6"))
MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "80"))
ALLOWED_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}

mcp = FastMCP("TikTok Video Watch", host="0.0.0.0", port=10000)

def validate_url(value: str) -> None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host not in ALLOWED_HOSTS and not host.endswith(".tiktok.com")):
        raise ValueError("Only public HTTPS TikTok links are supported.")

def run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Video processing timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Media processing failed")[-900:]
        raise ValueError(detail)

def analyze_video(url: str) -> dict:
    validate_url(url)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("FFmpeg is not available on this server.")

    with tempfile.TemporaryDirectory(prefix="tiktok-watch-") as tmp:
        work = Path(tmp)
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
        except Exception as exc:
            raise ValueError(f"Could not fetch this TikTok video: {str(exc)[:400]}") from exc

        candidates = [p for p in work.iterdir() if p.is_file() and p.name.startswith("source.")]
        if not candidates:
            raise ValueError("TikTok did not provide a downloadable video.")
        video_path = candidates[0]

        probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)], timeout=15)
        try:
            duration = float(probe.stdout.strip())
        except Exception as exc:
            raise ValueError("Could not determine video duration.") from exc
        if duration <= 0:
            raise ValueError("Could not determine video duration.")
        if duration > MAX_VIDEO_SECONDS:
            raise ValueError(f"Video is {int(duration)} seconds; the limit is {MAX_VIDEO_SECONDS} seconds.")

        frames_dir = work / "frames"
        frames_dir.mkdir()
        fps = min(0.5, MAX_FRAMES / max(duration, 1))
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-vf", f"fps={fps},scale=640:-1", "-q:v", "6", "-frames:v", str(MAX_FRAMES), str(frames_dir / "frame-%02d.jpg")], timeout=45)
        frames = []
        for frame in sorted(frames_dir.glob("*.jpg")):
            frames.append({"filename": frame.name, "mime_type": "image/jpeg", "base64": base64.b64encode(frame.read_bytes()).decode("ascii")})
        if not frames:
            raise ValueError("No usable video frames could be extracted.")

        audio_path = work / "audio.wav"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-t", str(MAX_VIDEO_SECONDS), "-y", str(audio_path)], timeout=45)
        transcript = ""
        transcript_error = None
        if audio_path.exists() and audio_path.stat().st_size > 1000:
            try:
                from faster_whisper import WhisperModel
                model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=2)
                segments, _ = model.transcribe(str(audio_path), beam_size=1, vad_filter=True)
                transcript = " ".join(segment.text.strip() for segment in segments).strip()
                del model
            except Exception as exc:
                transcript_error = f"Audio transcription unavailable: {str(exc)[:200]}"

        return {
            "title": info.get("title") or "TikTok video",
            "duration_seconds": round(duration, 2),
            "source_url": url,
            "frame_count": len(frames),
            "frames": frames,
            "transcript": transcript,
            "transcript_error": transcript_error,
            "note": "These are extracted video frames and an audio transcript, not a generated summary. The AI caller must inspect the frames and summarize them with the transcript."
        }

@mcp.tool()
def watch_tiktok(url: str) -> list[TextContent | ImageContent]:
    """Watch a public TikTok link by extracting real video frames and transcribing its audio. Returns images and transcript for an AI to summarize."""
    result = analyze_video(url)
    transcript = result["transcript"] or "(No speech transcript available.)"
    details = (
        f"Title: {result['title']}\n"
        f"Duration: {result['duration_seconds']} seconds\n"
        f"Frames extracted: {result['frame_count']}\n"
        f"Audio transcript: {transcript}\n"
        f"Transcript note: {result['transcript_error'] or 'none'}\n"
        "Inspect each attached image in order. Summarize only what the frames and transcript support."
    )
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=details)]
    for frame in result["frames"]:
        content.append(ImageContent(type="image", data=frame["base64"], mime_type="image/jpeg"))
    return content

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    return JSONResponse({"service": "TikTok Video Watch MCP", "status": "ok", "mcp_endpoint": "/mcp"})

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok"})

@mcp.custom_route("/analyze", methods=["POST"])
async def analyze_route(request: Request):
    try:
        body = await request.json()
        url = str(body.get("url", "")).strip()
        result = await asyncio.to_thread(analyze_video, url)
        return JSONResponse({key: value for key, value in result.items() if key != "frames"})
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:800]}, status_code=422)

app = mcp.streamable_http_app()
