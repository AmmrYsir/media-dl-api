# See README.md for setup instructions, API reference, and examples.
# Run: uvicorn app:app --reload --host 0.0.0.0 --port 8000
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl, field_validator

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Vicatch – Video Download API",
    description=(
        "Download videos from YouTube, Instagram, Facebook, TikTok, "
        "and any other HTTP(S) URL using **yt-dlp** under the hood.\n\n"
        "### Workflow\n"
        "1. `POST /api/download` – Submit a URL; receive the download path.\n"
        "2. `GET  /downloads/{filename}` – Retrieve the downloaded file.\n\n"
        "> **Requirement:** `yt-dlp` must be installed (`pip install yt-dlp`)."
    ),
    version="1.0.0",
    contact={"name": "Vicatch", "url": "https://github.com/your-org/vicatch"},
    license_info={"name": "MIT"},
)


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
            if ext.matches(url):
                return ext
        return None


registry = ServiceRegistry()
registry.register(ServiceExtension("YouTube", re.compile(r"(youtube\.com|youtu\.be)", re.I)))
registry.register(ServiceExtension("Instagram", re.compile(r"instagram\.com", re.I)))
registry.register(ServiceExtension("Facebook", re.compile(r"facebook\.com|fb\.watch", re.I)))
registry.register(ServiceExtension("TikTok", re.compile(r"tiktok\.com", re.I)))
registry.register(ServiceExtension("Generic", re.compile(r"https?://", re.I)))

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

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to execute yt-dlp: {exc}",
        ) from exc

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "Unknown download error."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{service_name} download failed: {error}",
        )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    file_path = Path(lines[-1]) if lines else None
    if not file_path or not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Download completed but the output file could not be located.",
        )

    return f"Download completed via {service_name}.", file_path.name


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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "success",
                    "message": "Download completed via YouTube.",
                    "filename": "Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
                    "download_url": "/downloads/Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
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
        "saves the file to the `downloads/` directory, and returns the file name "
        "plus a direct download URL."
    ),
    responses={
        200: {"model": DownloadResponse, "description": "Video downloaded successfully."},
        422: {"model": ErrorResponse, "description": "yt-dlp reported a download error."},
        503: {"model": ErrorResponse, "description": "yt-dlp is not installed or cannot run."},
    },
    tags=["Video Download"],
)
def download_video(body: DownloadRequest) -> DownloadResponse:
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
    )


@app.get(
    "/downloads/{filename}",
    summary="Retrieve a downloaded file",
    description=(
        "Stream a previously downloaded file back to the client. "
        "The `filename` must exactly match a file in the server's `downloads/` directory."
    ),
    responses={
        200: {"description": "The requested file is returned as a binary download."},
        400: {"model": ErrorResponse, "description": "Path traversal attempt detected."},
        404: {"model": ErrorResponse, "description": "File not found."},
    },
    tags=["Video Download"],
)
def get_downloaded_file(filename: str) -> FileResponse:
    """Return the raw file so the caller can save it locally."""
    # Guard against path traversal
    safe = (DOWNLOADS_DIR / filename).resolve()
    try:
        safe.relative_to(DOWNLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path.",
        )

    if not safe.exists() or not safe.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{filename}' not found.",
        )

    return FileResponse(path=safe, filename=filename, media_type="application/octet-stream")
