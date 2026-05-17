# Homelab yt-dlp Downloader API

Small Docker service for the Raspberry Pi 4 / homelab side of Harry's YouTube transcription pipeline.

Goal:

1. Hermes/Quillie on the VPS receives a YouTube link in chat, Telegram, or Discord.
2. The VPS calls this downloader API over Tailnet / secure tunnel.
3. This service first tries to fetch YouTube captions/subtitles with yt-dlp.
4. If captions exist, the VPS fetches a finished Markdown transcript in seconds.
5. If captions are unavailable, the service downloads audio/video from the Raspberry Pi's residential network and the VPS transcribes it with the local Parakeet endpoint.

This project intentionally does captions/download only. ASR transcription stays on the VPS.

## Why this exists

The previous transcript-only Flask app depended on YouTube caption endpoints. Those often fail with empty XML responses or bot checks. This service uses yt-dlp for captions first and audio download fallback, and is designed for agent automation.

It borrows the useful ideas from `adepanges/yt-dlp-host`:

- yt-dlp with `curl_cffi` browser impersonation support
- optional cookies file
- YouTube `player_client` workaround: `default,-tv_simply`
- async jobs with status polling
- file endpoint for the agent to download the result
- captions/subtitles jobs that return cleaned Markdown transcripts

I did not directly fork that repo because this use case is narrower: one trusted agent, one homelab downloader, audio-first, simple Bearer token auth, and fewer admin/key/quota features.

## Security model

Do not expose this API openly to the internet.

Recommended:

- bind to localhost on the Pi
- expose via Tailscale Serve, an SSH tunnel, or Nginx Proxy Manager with strong auth
- set a long random `YTDLP_API_TOKEN`
- keep cookies private and never commit `data/cookies.txt`

The service only allows these hosts by default:

- youtube.com
- www.youtube.com
- m.youtube.com
- music.youtube.com
- youtu.be

This is deliberate to avoid turning your Pi into a general-purpose public downloader. Frech, but safe. ✒️

## Quick start on Raspberry Pi

```bash
cd /home/pi/docker-data
git clone <this-repo-url> yt-dlp-downloader-api
cd yt-dlp-downloader-api

# Generate a real token
TOKEN=$(openssl rand -hex 32)
python3 - <<PY
from pathlib import Path
p = Path('docker-compose.yaml')
s = p.read_text()
s = s.replace('change-me-generate-a-long-random-token', '$TOKEN')
p.write_text(s)
PY

docker compose build
docker compose up -d
```

Health check on the Pi:

```bash
curl http://127.0.0.1:8088/health
```

## Expose over Tailscale

Recommended: keep Docker bound to localhost and expose the host-local port with Tailscale Serve on the Raspberry Pi.

Do **not** make the downloader public unless you put strong auth in front of it. The API token remains required either way.

On the Raspberry Pi:

```bash
# Check the container locally first
curl http://127.0.0.1:8088/health

# Inspect existing Serve routes before changing anything
tailscale serve status --json || true

# Serve this API over tailnet HTTPS on the Pi's MagicDNS hostname
tailscale serve --bg --yes --https=443 --set-path / http://127.0.0.1:8088

# Re-check the route
tailscale serve status --json
```

From the VPS/agent machine, verify using the Pi's Tailnet hostname:

```bash
curl -sS https://<raspberry-pi-tailnet-name>/health
```

Example authenticated API call from the VPS:

```bash
curl -sS -X POST 'https://<raspberry-pi-tailnet-name>/v1/downloads' \
  -H "Authorization: Bearer $YTDLP_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=iXd0t60YmMw","kind":"audio","audio_format":"original"}'
```

## Optional cookies

If YouTube blocks the Pi anyway, export cookies from a logged-in browser as Netscape `cookies.txt`.

Then place it here:

```text
./data/cookies.txt
```

Uncomment these lines in `docker-compose.yaml`:

```yaml
volumes:
  - ./data/cookies.txt:/cookies/cookies.txt:ro
environment:
  YOUTUBE_COOKIES_FILE: /cookies/cookies.txt
```

Restart:

```bash
docker compose up -d
```

Security warning: `cookies.txt` is basically a browser session. Keep it local, private, and out of Git.

## API

Authentication accepts either:

```text
Authorization: Bearer <YTDLP_API_TOKEN>
```

or:

```text
X-API-Key: <YTDLP_API_TOKEN>
```

### Create captions/subtitles job

This is the fastest path. It returns a cleaned Markdown transcript when YouTube exposes manual subtitles or automatic captions.
Manual subtitles are preferred over automatic captions. Language selection prefers the video's detected language first; defaults are English first, then German (`en,en-US,en-GB,de-DE,de`). Override with `preferred_langs` when needed.

```bash
curl -sS -X POST 'http://127.0.0.1:8088/v1/downloads' \
  -H "Authorization: Bearer ***" \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.youtube.com/watch?v=iXd0t60YmMw",
    "kind": "captions",
    "preferred_langs": ["en", "en-US", "en-GB", "de-DE", "de"]
  }'
```

Use `"kind": "subtitles"` as an alias if you prefer that wording.

### Create audio download job

```bash
curl -sS -X POST 'http://127.0.0.1:8088/v1/downloads' \
  -H "Authorization: Bearer $YTDLP_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.youtube.com/watch?v=iXd0t60YmMw",
    "kind": "audio",
    "audio_format": "original",
    "yt_format": "bestaudio[abr<=64]/bestaudio[abr<=96]/bestaudio/best"
  }'
```

Response:

```json
{
  "id": "...",
  "status": "queued",
  "file_url": null
}
```

### Poll job

```bash
curl -sS 'http://127.0.0.1:8088/v1/jobs/<job_id>' \
  -H "Authorization: Bearer $YTDLP_API_TOKEN"
```

When complete, the job contains:

```json
{
  "status": "completed",
  "file_url": "/v1/jobs/<job_id>/file",
  "info_url": "/v1/jobs/<job_id>/info"
}
```

### Download file to VPS

```bash
curl -L -o youtube_audio.webm \
  -H "Authorization: Bearer $YTDLP_API_TOKEN" \
  'http://pi-tailnet-host:8088/v1/jobs/<job_id>/file'
```

## VPS / Hermes flow

The helper script tries captions first and falls back to audio download + Parakeet ASR automatically:

```bash
python3 scripts/download_and_transcribe.py 'https://www.youtube.com/watch?v=iXd0t60YmMw'
```

Force ASR fallback only:

```bash
python3 scripts/download_and_transcribe.py --no-captions-first 'https://www.youtube.com/watch?v=iXd0t60YmMw'
```

After an audio fallback file is on the VPS, transcribe with the local Parakeet endpoint:

```bash
curl -sS \
  -X POST 'http://127.0.0.1:5092/v1/audio/transcriptions' \
  -F 'file=@youtube_audio.webm' \
  -F 'model=parakeet' \
  -F 'response_format=json'
```

See `scripts/download_and_transcribe.py` for a compact end-to-end client script.

## Configuration

Environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `YTDLP_API_TOKEN` | empty | Required API token. Service refuses authenticated calls if unset. |
| `DOWNLOAD_DIR` | `/data/downloads` | Download output root. |
| `STATE_DIR` | `/data/state` | Persistent job JSON state. |
| `YOUTUBE_COOKIES_FILE` | empty | Optional mounted browser cookies file. |
| `DEFAULT_AUDIO_FORMAT` | `original` | Default output when request omits `audio_format`; avoids slow Pi-side transcoding. |
| `DEFAULT_YT_FORMAT` | `bestaudio[abr<=64]/bestaudio[abr<=96]/bestaudio/best` | Prefer small audio-only streams for ASR. |
| `DEFAULT_CAPTION_LANGS` | `en,en-US,en-GB,de-DE,de` | Caption language preference when the request omits `preferred_langs`. |
| `MAX_WORKERS` | `2` | Concurrent jobs. Keep low on Pi 4. |
| `MAX_DURATION_SECONDS` | `21600` | Reject videos longer than 6h by default. |
| `ALLOWED_DOMAINS` | YouTube hosts | Comma-separated allowlist. |
| `ENABLE_IMPERSONATION` | `true` | Enables yt-dlp curl_cffi Chrome impersonation when available. |
| `YOUTUBE_PLAYER_CLIENTS` | `default,-tv_simply` | YouTube extractor client workaround. |

## Notes from reviewing adepanges/yt-dlp-host

Useful patterns copied/adapted:

- `yt-dlp[default,curl-cffi]` in requirements
- Docker image verifies `curl_cffi` import during build
- cookies via mounted file
- `ImpersonateTarget('chrome')` when available
- `extractor_args: {'youtube': {'player_client': ['default', '-tv_simply']}}`

Things intentionally simplified:

- no dynamic API key management endpoints
- no quota system
- no live-stream-specific endpoints
- no broad multi-site downloader by default
- no public `/files/<path>` traversal surface; only per-job authenticated file endpoints

## Troubleshooting

Logs:

```bash
docker compose logs -f
```

Common errors:

- `Sign in to confirm you're not a bot`: add cookies.txt.
- `curl_cffi impersonation unavailable`: rebuild image; the Dockerfile checks this at build time.
- `Host ... is not allowed`: URL is not from an allowed YouTube host.
- `Job is not completed`: poll `/v1/jobs/<id>` until status is `completed`.
