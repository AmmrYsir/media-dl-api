# media-dl-api — Video Download API

A **FastAPI** REST API that wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download videos from YouTube, Instagram, Facebook, TikTok, and any generic HTTP(S) URL.

---

## Requirements

| Requirement | Version   |
|-------------|-----------|
| Python      | 3.10 +    |
| fastapi     | latest    |
| uvicorn     | latest    |
| yt-dlp      | latest    |
| slowapi     | latest    |
| pydantic    | latest    |

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/AmmrYsir/media-dl-api.git
cd media-dl-api
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
pip install -r requirements.txt
```

---

## Running the server

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

| Flag              | Purpose                                          |
|-------------------|--------------------------------------------------|
| `--reload`        | Auto-restart on code changes (development only)  |
| `--host 0.0.0.0`  | Listen on all network interfaces                 |
| `--port 8000`     | TCP port (change freely)                         |

For production, drop `--reload` and consider multiple workers:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## Interactive API Docs (Swagger UI)

Once the server is running, open your browser:

| URL                                | Description                               |
|------------------------------------|-------------------------------------------|
| http://localhost:8000/docs         | **Swagger UI** — try endpoints interactively |
| http://localhost:8000/redoc        | **ReDoc** — clean reference view          |
| http://localhost:8000/openapi.json | Raw OpenAPI 3.1 schema                    |

### Using Swagger UI

1. Open **http://localhost:8000/docs**
2. Click on an endpoint (e.g. `POST /api/download`) to expand it
3. Click **Try it out**
4. Fill in the request body (e.g. a YouTube URL)
5. Click **Execute** — the response appears inline with status code, headers, and body

> **Note:** The security middleware applies a strict `Content-Security-Policy` to all API routes, but automatically relaxes it for `/docs` and `/redoc` to allow the Swagger UI CDN assets to load correctly.

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
  "download_url": "/downloads/Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
  "expires_in_seconds": 900
}
```

**Error responses**

| Status | Meaning                                          |
|--------|--------------------------------------------------|
| `413`  | Video file exceeds the 1 GB size limit           |
| `422`  | yt-dlp reported a download error or invalid URL  |
| `429`  | Rate limit exceeded (5 requests/minute per IP)   |
| `503`  | yt-dlp is not installed or cannot be executed    |
| `504`  | Download timed out (> 5 minutes)                 |

---

### `GET /downloads/{filename}`

Stream a previously downloaded file back to the caller.

- `filename` must exactly match the value returned by `POST /api/download`.
- The file is served as `application/octet-stream` (binary download).
- **The file is permanently deleted from the server after this request.**
- Files also expire automatically after **15 minutes** if never retrieved.

**Error responses**

| Status | Meaning                                              |
|--------|------------------------------------------------------|
| `400`  | Path traversal attempt or disallowed file extension  |
| `404`  | File does not exist or has already been deleted      |

---

## Security

| Feature                 | Detail                                                          |
|-------------------------|-----------------------------------------------------------------|
| Rate limiting           | 5 downloads per minute per IP (via slowapi)                     |
| File TTL                | Downloaded files auto-deleted after 15 minutes                  |
| Delete-after-serve      | Files are deleted immediately after being retrieved             |
| Max file size           | 1 GB hard limit (pre-download probe + yt-dlp `--max-filesize`)  |
| Disk quota              | Server refuses new jobs if `downloads/` exceeds 500 MB / 30 files |
| Path traversal guard    | All filename inputs are validated against `downloads/` directory |
| Extension whitelist     | Only `.mp4 .webm .mkv .mp3 .m4a .opus .ogg .flv .avi .mov` served |
| Security headers        | X-Content-Type-Options, X-Frame-Options, HSTS, CSP, and more   |
| Error sanitization      | Raw yt-dlp stderr is sanitized before being sent to callers     |
| CORS                    | Restricted to `http://localhost:3000` by default                |

---

## Supported Services

| Service   | Matched pattern                        |
|-----------|----------------------------------------|
| YouTube   | `youtube.com`, `youtu.be`              |
| Instagram | `instagram.com`                        |
| Facebook  | `facebook.com`, `fb.watch`             |
| TikTok    | `tiktok.com`                           |
| Generic   | Any `http://` or `https://` URL        |

---

## Project Structure

```
media-dl-api/
├── app.py           # FastAPI application (routes, middleware, yt-dlp logic)
├── requirements.txt # Python dependencies
├── downloads/       # Temporary download directory (auto-created)
└── README.md
```

---

## Example — curl

```bash
# 1. Trigger a download
curl -X POST http://localhost:8000/api/download \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# 2. Save the file locally (use the filename from step 1's response)
curl -OJ http://localhost:8000/downloads/<filename>
```
