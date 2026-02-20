# Vicatch  Video Download API

A **FastAPI** REST API that wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download videos from YouTube, Instagram, Facebook, TikTok, and any generic HTTP(S) URL.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10 + |
| fastapi | latest |
| uvicorn | latest |
| yt-dlp | latest |

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/your-org/vicatch.git
cd vicatch
```

**2. Create and activate a virtual environment** *(recommended)*

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

**3. Install dependencies**

```bash
pip install fastapi uvicorn yt-dlp
```

---

## Running the server

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

| Flag | Purpose |
|---|---|
| `--reload` | Auto-restart on code changes (development only) |
| `--host 0.0.0.0` | Listen on all network interfaces |
| `--port 8000` | TCP port (change freely) |

For production, drop `--reload` and consider multiple workers:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## Interactive API docs

Once the server is running, open your browser:

| URL | Description |
|---|---|
| http://localhost:8000/docs | Swagger UI  try endpoints interactively |
| http://localhost:8000/redoc | ReDoc  clean reference view |
| http://localhost:8000/openapi.json | Raw OpenAPI 3.1 schema |

---

## API Reference

### `POST /api/download`

Submit a video URL for download.

**Request body** (`application/json`)

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

**Success response** `200 OK`

```json
{
  "status": "success",
  "message": "Download completed via YouTube.",
  "filename": "Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
  "download_url": "/downloads/Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4"
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `422` | yt-dlp reported a download error or URL is invalid |
| `503` | yt-dlp is not installed or cannot be executed |

---

### `GET /downloads/{filename}`

Stream a previously downloaded file back to the caller.

- `filename` must exactly match the value returned by `POST /api/download`.
- The file is served as `application/octet-stream` (binary download).

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Path traversal attempt detected |
| `404` | File does not exist in the `downloads/` directory |

---

## Supported services

| Service | Matched pattern |
|---|---|
| YouTube | `youtube.com`, `youtu.be` |
| Instagram | `instagram.com` |
| Facebook | `facebook.com`, `fb.watch` |
| TikTok | `tiktok.com` |
| Generic | Any `http://` or `https://` URL |

---

## Project structure

```
vicatch/
 app.py          # FastAPI application
 downloads/      # Downloaded files (auto-created)
 README.md
```

---

## Example  curl

```bash
# 1. Trigger a download
curl -X POST http://localhost:8000/api/download \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# 2. Save the file locally (use the filename from step 1)
curl -OJ http://localhost:8000/downloads/<filename>
```
