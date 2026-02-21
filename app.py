# See README.md for setup instructions, API reference, and examples.
# Run: uvicorn app:app --reload --host 0.0.0.0 --port 8000
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.background import BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("media-dl-api")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How long (seconds) a downloaded file lives on disk if never retrieved.
FILE_TTL_SECONDS: int = 900          # 15 minutes

# How often (seconds) the background cleaner runs.
CLEANUP_INTERVAL_SECONDS: int = 60

# Maximum time (seconds) yt-dlp is allowed to run before being killed.
YTDLP_TIMEOUT_SECONDS: int = 300     # 5 minutes

# Rate-limit: max downloads per minute per IP.
RATE_LIMIT: str = "5/minute"

# Allowed media file extensions that the /downloads/ endpoint will serve.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".webm", ".mkv", ".mp3", ".m4a", ".opus", ".ogg", ".flv", ".avi", ".mov"}
)

# Allowed CORS origins.
CORS_ORIGINS: list[str] = ["http://localhost:3000"]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Media DL – Video Download API",
    description=(
        "Download videos from YouTube, Instagram, Facebook, TikTok, "
        "and any other HTTP(S) URL using **yt-dlp** under the hood.\n\n"
        "### Workflow\n"
        "1. `POST /api/download` – Submit a URL; receive the download path.\n"
        "2. `GET  /downloads/{filename}` – Retrieve the downloaded file "
        "(**file is deleted after serving**).\n\n"
        "> **Requirement:** `yt-dlp` must be installed (`pip install yt-dlp`)."
    ),
    version="2.0.0",
    contact={"name": "Media DL", "url": "https://github.com/AmmrYsir/media-dl-api"},
    license_info={"name": "MIT"},
)

# Attach rate-limit error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware: CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],          # No wildcard – restrict to what's needed
    allow_headers=["Content-Type", "Authorization"],
)

# ---------------------------------------------------------------------------
# Middleware: Security headers
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject defensive HTTP security headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceExtension:
    """Represents a named service whose URLs match a compiled regex pattern."""

    name: str
    pattern: re.Pattern[str]

    def matches(self, url: str) -> bool:
        return bool(self.pattern.search(url))


class ServiceRegistry:
    """Ordered registry that maps a URL to the first matching :class:`ServiceExtension`."""

    def __init__(self) -> None:
        self._extensions: list[ServiceExtension] = []

    def register(self, extension: ServiceExtension) -> None:
        self._extensions.append(extension)

    def resolve(self, url: str) -> ServiceExtension | None:
        for ext in self._extensions:
            if ext.matches(url):\
                return ext
        return None


registry = ServiceRegistry()
registry.register(ServiceExtension("YouTube",   re.compile(r"(youtube\.com|youtu\.be)", re.I)))
registry.register(ServiceExtension("Instagram", re.compile(r"instagram\.com", re.I)))
registry.register(ServiceExtension("Facebook",  re.compile(r"facebook\.com|fb\.watch", re.I)))
registry.register(ServiceExtension("TikTok",    re.compile(r"tiktok\.com", re.I)))
registry.register(ServiceExtension("Generic",   re.compile(r"https?://", re.I)))

# ---------------------------------------------------------------------------
# yt-dlp helpers
# ---------------------------------------------------------------------------


def _get_ytdlp_command() -> list[str] | None:
    """Return the shell command prefix for yt-dlp, or ``None`` if unavailable."""
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "yt_dlp"]


# Redacts absolute-path-like strings from error messages before sending to callers.
_PATH_PATTERN = re.compile(r"([A-Za-z]:\\|/)[^\s\"']+")


def _sanitize_error(raw: str) -> str:
    """
    Strip raw yt-dlp error output to at most 2 lines and redact filesystem paths.
    Prevents leaking server directory structure or credentials to clients.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    excerpt = " | ".join(lines[-2:]) if lines else "Unknown error."
    return _PATH_PATTERN.sub("<redacted>", excerpt)


def _run_download(url: str, service_name: str) -> tuple[str, str]:
    """
    Invoke yt-dlp for *url* and return ``(message, filename)`` on success.

    Raises :class:`HTTPException` on any failure.
    """
    cmd_prefix = _get_ytdlp_command()
    if not cmd_prefix:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="yt-dlp is not installed. Run: pip install yt-dlp",
        )

    output_template = str(DOWNLOADS_DIR / "%(title).120s-%(id)s.%(ext)s")
    command = [
        *cmd_prefix,
        "--no-playlist",
        "--restrict-filenames",
        "--print", "after_move:filepath",
        "-o", output_template,
        url,
    ]

    logger.info("Starting download [service=%s] url=%s", service_name, url)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=YTDLP_TIMEOUT_SECONDS,   # FIX: prevents indefinite hang
        )
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp timed out after %ds for url=%s", YTDLP_TIMEOUT_SECONDS, url)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Download timed out after {YTDLP_TIMEOUT_SECONDS} seconds.",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to execute yt-dlp: {exc}",
        ) from exc

    if result.returncode != 0:
        # FIX: sanitize error – never expose raw stderr
        raw_error = result.stderr.strip() or result.stdout.strip() or "Unknown download error."
        safe_error = _sanitize_error(raw_error)
        logger.warning("yt-dlp failed [rc=%d] url=%s error=%s", result.returncode, url, raw_error)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{service_name} download failed: {safe_error}",
        )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    file_path = Path(lines[-1]) if lines else None

    # Validate the output path is inside DOWNLOADS_DIR (extra safety)
    if file_path:
        try:
            file_path.resolve().relative_to(DOWNLOADS_DIR.resolve())
        except ValueError:
            file_path = None

    if not file_path or not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Download completed but the output file could not be located.",
        )

    logger.info("Download succeeded: %s", file_path.name)
    return f"Download completed via {service_name}.", file_path.name

# ---------------------------------------------------------------------------
# Background: TTL-based cleanup
# ---------------------------------------------------------------------------


def _delete_file(path: Path) -> None:
    """Safely remove *path* from disk, logging outcome."""
    try:
        path.unlink(missing_ok=True)
        logger.info("Deleted file: %s", path.name)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to delete %s: %s", path.name, exc)


async def _cleanup_loop() -> None:
    """Periodically delete files in DOWNLOADS_DIR older than FILE_TTL_SECONDS."""
    logger.info(
        "File cleanup task started (TTL=%ds, interval=%ds)",
        FILE_TTL_SECONDS,
        CLEANUP_INTERVAL_SECONDS,
    )
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        now = time.time()
        for f in DOWNLOADS_DIR.iterdir():
            if not f.is_file():
                continue
            age = now - f.stat().st_mtime
            if age > FILE_TTL_SECONDS:
                logger.info("TTL expired (age=%.0fs): removing %s", age, f.name)
                _delete_file(f)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(_cleanup_loop())

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DownloadRequest(BaseModel):
    """Request body for the download endpoint."""

    url: HttpUrl

    @field_validator("url", mode="before")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not str(v).startswith(("http://", "https://")):
            raise ValueError("Only HTTP and HTTPS URLs are supported.")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
            ]
        }
    }


class DownloadResponse(BaseModel):
    """Successful response returned after a video is downloaded."""

    status: str
    message: str
    filename: str
    download_url: str
    expires_in_seconds: int

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "success",
                    "message": "Download completed via YouTube.",
                    "filename": "Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
                    "download_url": "/downloads/Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
                    "expires_in_seconds": 900,
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    """Error detail returned on failure."""

    detail: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post(
    "/api/download",
    response_model=DownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Download a video",
    description=(
        "Submit any HTTP/HTTPS URL. The server resolves the appropriate service "
        "(YouTube, Instagram, Facebook, TikTok, or generic), invokes **yt-dlp**, "
        "saves the file temporarily to the `downloads/` directory, and returns the "
        "file name plus a direct download URL.\n\n"
        f"> ⏱ File expires in **{FILE_TTL_SECONDS // 60} minutes** or on first fetch."
    ),
    responses={
        200: {"model": DownloadResponse, "description": "Video downloaded successfully."},
        422: {"model": ErrorResponse, "description": "yt-dlp reported a download error."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        503: {"model": ErrorResponse, "description": "yt-dlp is not installed or cannot run."},
        504: {"model": ErrorResponse, "description": "Download timed out."},
    },
    tags=["Video Download"],
)
@limiter.limit(RATE_LIMIT)
def download_video(request: Request, body: DownloadRequest) -> DownloadResponse:
    url = str(body.url)
    extension = registry.resolve(url)
    if not extension:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No extension available for this URL.",
        )

    message, filename = _run_download(url, extension.name)
    return DownloadResponse(
        status="success",
        message=message,
        filename=filename,
        download_url=f"/downloads/{filename}",
        expires_in_seconds=FILE_TTL_SECONDS,
    )


@app.get(
    "/downloads/{filename}",
    summary="Retrieve a downloaded file",
    description=(
        "Stream a previously downloaded file back to the client. "
        "The `filename` must exactly match a file in the server's `downloads/` directory.\n\n"
        "> ⚠️ **The file is permanently deleted from the server after this request.**"
    ),
    responses={
        200: {"description": "The requested file is returned as a binary download."},
        400: {"model": ErrorResponse, "description": "Path traversal attempt or disallowed extension."},
        404: {"model": ErrorResponse, "description": "File not found or already deleted."},
    },
    tags=["Video Download"],
)
def get_downloaded_file(filename: str, background_tasks: BackgroundTasks) -> FileResponse:
    """Stream the file to the caller, then delete it from disk."""

    # --- Guard 1: Path traversal ---
    safe = (DOWNLOADS_DIR / filename).resolve()
    try:
        safe.relative_to(DOWNLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path.",
        )

    # --- Guard 2: Extension whitelist ---
    if safe.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File type not permitted.",
        )

    # --- Guard 3: Existence check ---
    if not safe.exists() or not safe.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or has already been deleted.",  # no raw filename reflection
        )

    # Schedule deletion after response is sent (delete-after-serve)
    background_tasks.add_task(_delete_file, safe)
    logger.info("Serving and scheduling deletion: %s", safe.name)

    return FileResponse(
        path=safe,
        filename=filename,
        media_type="application/octet-stream",
        background=background_tasks,
    )
