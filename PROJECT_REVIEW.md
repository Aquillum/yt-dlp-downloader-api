# Review notes: adepanges/yt-dlp-host

Repository inspected: https://github.com/adepanges/yt-dlp-host
Local read-only clone: `/home/ubuntu/workspace/projects/_research_yt-dlp-host`

I did not execute the third-party code. I only cloned and read it.

## Useful ideas copied/adapted

- `yt-dlp[default,curl-cffi]` dependency to enable browser impersonation.
- Dockerfile imports `curl_cffi` during build to fail fast if impersonation support is missing.
- Optional cookies file support.
- `ImpersonateTarget('chrome')` usage.
- YouTube extractor workaround: `player_client: ['default', '-tv_simply']`.
- Async job model: create job, poll status, fetch file.

## Concerns with using it directly

- It is broader than needed: key creation/deletion endpoints, quota accounting, live endpoints, info filtering.
- It exposes a path-based `/files/<path>` endpoint. It does validate path boundaries, but a per-job file endpoint is narrower.
- It stores mutable API key data in JSON files and creates an admin key if none exists. For a single trusted Hermes-to-homelab service, a single environment token is simpler.
- It is designed as a general downloader; this project defaults to YouTube-only to reduce abuse/SSRF risk.

## Decision

I built a purpose-specific service in `yt-dlp-downloader-api` instead of forking the repo. This keeps the attack surface small and makes the Hermes integration cleaner.
