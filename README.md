# media-dl-api - Video Download API

A **FastAPI** REST API that wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download videos from YouTube, Instagram, Facebook, and TikTok. Generic arbitrary HTTP(S) URLs are disabled by default and must be explicitly enabled.

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| fastapi | latest |
| uvicorn | latest |
| yt-dlp | latest |
| slowapi | latest |
| pydantic | latest |

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

For production, drop `--reload` and consider multiple workers:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

### Recommended production environment

Set these before exposing the service publicly:

```bash
# Linux / macOS
export ALLOWED_HOSTS=media.example.com
export MEDIA_DL_API_KEY=replace-with-a-long-random-secret
export CORS_ORIGINS=https://app.example.com
export ENABLE_GENERIC_DOWNLOADS=false
```

```powershell
# Windows PowerShell
$env:ALLOWED_HOSTS="media.example.com"
$env:MEDIA_DL_API_KEY="replace-with-a-long-random-secret"
$env:CORS_ORIGINS="https://app.example.com"
$env:ENABLE_GENERIC_DOWNLOADS="false"
```

Notes:
- `ALLOWED_HOSTS` should include the exact public hostname(s) you will serve.
- `MEDIA_DL_API_KEY` makes both download endpoints require `X-API-Key`.
- `ENABLE_GENERIC_DOWNLOADS` is intentionally off by default because it materially increases SSRF risk.

---

## Interactive API Docs

Once the server is running, open:

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/openapi.json` | Raw OpenAPI schema |

If `MEDIA_DL_API_KEY` is set, include `X-API-Key` when trying endpoints from Swagger.

---

## API Reference

### `POST /api/download`

Submit a supported video URL for download.

Headers:
- `Content-Type: application/json`
- `X-API-Key: <secret>` if `MEDIA_DL_API_KEY` is configured

**Request body**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

**Success response**

```json
{
  "status": "success",
  "message": "Download completed via YouTube.",
  "filename": "Rick_Astley-Never_Gonna_Give_You_Up-dQw4w9WgXcQ.mp4",
  "download_url": "/downloads/6fY0v8m8p8l4f8S4dW2P4A",
  "download_token": "6fY0v8m8p8l4f8S4dW2P4A",
  "expires_in_seconds": 900
}
```

Error responses:

| Status | Meaning |
|--------|---------|
| `400` | Internal/private destination blocked |
| `401` | Missing or invalid API key |
| `413` | Video file exceeds the size limit |
| `422` | Invalid URL or yt-dlp download failure |
| `429` | Rate limit exceeded |
| `503` | yt-dlp is not installed or storage quota reached |
| `504` | Download timed out |

### `GET /downloads/{token}`

Retrieve a previously downloaded file.

- `token` must be the `download_token` from `POST /api/download`
- the token is one-time use
- the token is bound to the same client IP that created it
- the file is deleted after it is served

Error responses:

| Status | Meaning |
|--------|---------|
| `401` | Missing or invalid API key |
| `403` | Token does not belong to this client |
| `404` | Token expired, already used, or file missing |

---

## Security

| Feature | Detail |
|---------|--------|
| Rate limiting | 5 download requests per minute per IP |
| File TTL | Downloaded files expire after 15 minutes |
| One-time tokens | File retrieval uses short-lived random tokens |
| IP binding | Download tokens are bound to the creating client IP |
| Delete-after-serve | Files are removed after successful retrieval |
| Max file size | 1 GB hard limit with probe plus yt-dlp guard |
| Disk quota | New jobs are rejected when `downloads/` is too full |
| Trusted hosts | Requests must match `ALLOWED_HOSTS` |
| Optional API key | `MEDIA_DL_API_KEY` protects both endpoints |
| SSRF protection | Hostnames are resolved and blocked if they map to private/internal IPs |
| Extension whitelist | Only approved media extensions are served |
| Security headers | CSP, HSTS, X-Frame-Options, no-sniff, and related headers |
| Error sanitization | Raw yt-dlp stderr is sanitized before returning to clients |
| Safer logging | Request URLs are redacted before logging |
| CORS | Controlled through `CORS_ORIGINS` |

---

## Supported Services

| Service | Matched pattern |
|---------|-----------------|
| YouTube | `youtube.com`, `youtu.be` |
| Instagram | `instagram.com` |
| Facebook | `facebook.com`, `fb.watch` |
| TikTok | `tiktok.com` |
| Generic | Optional and disabled by default |

---

## Example - curl

```bash
# 1. Trigger a download
curl -X POST http://localhost:8000/api/download \
     -H "Content-Type: application/json" \
     -H "X-API-Key: replace-with-a-long-random-secret" \
     -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# 2. Save the file locally using the returned download_token
curl -OJ \
     -H "X-API-Key: replace-with-a-long-random-secret" \
     http://localhost:8000/downloads/<download_token>
```
