#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


_ENV_FILES = (
    Path.home() / '.hermes' / '.env',
    Path('/home/ubuntu/.hermes/.env'),
    Path.cwd() / '.env',
    Path(__file__).resolve().parents[1] / '.env',
)


def load_env_files() -> None:
    """Load simple KEY=VALUE lines without overriding the live environment."""
    for env_file in _ENV_FILES:
        try:
            lines = env_file.read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, value = stripped.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return 'unknown'
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return 'unknown'
    if total < 0:
        return 'unknown'
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def normalized_title_filename(title: str, fallback: str = 'youtube-transcript') -> str:
    title = (title or fallback).strip().lower()
    title = title.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'}))
    title = re.sub(r'[^a-z0-9]+', '-', title)
    title = re.sub(r'-{2,}', '-', title).strip('-')
    return (title[:140].strip('-') or fallback)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for idx in range(2, 1000):
        candidate = path.with_name(f'{path.stem}-{idx}{path.suffix}')
        if not candidate.exists():
            return candidate
    raise FileExistsError(f'Could not find unique filename for {path}')


def job_video_info(job: dict) -> dict:
    meta = job.get('metadata') or {}
    return {
        'title': job.get('title') or meta.get('title') or 'Transcript',
        'channel': meta.get('channel') or 'unknown',
        'duration': job.get('duration') or meta.get('duration'),
    }


def video_print_line(info: dict) -> str:
    return f'{info.get("channel") or "unknown"} - {info.get("duration") or "unknown"} - {info.get("title") or "unknown"}'


def markdown_path_for_info(out_dir: Path, info: dict, fallback: str = 'youtube-transcript') -> Path:
    slug = normalized_title_filename(info.get('title') or fallback, fallback=fallback)
    return unique_path(out_dir / f'{slug}.md')


def markdown_path_for_job(out_dir: Path, job: dict, fetched_info: dict | None = None) -> Path:
    info = merge_video_info(job, fetched_info)
    return markdown_path_for_info(out_dir, info, fallback=job.get('id') or 'youtube-transcript')


def request_json(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = None
    headers = {'Authorization': f'Bearer {token}'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{method} {url} failed with HTTP {exc.code}: {body}') from exc


def download_file(url: str, token: str, output: Path) -> None:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=3600) as response:
        output.write_bytes(response.read())


def download_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=3600) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_video_info(api_base: str, token: str, url: str, timeout: int, poll_interval: float) -> dict:
    job = create_job(api_base, token, {'url': url, 'kind': 'info'})
    job = wait_for_job(api_base, token, job['id'], timeout, poll_interval)
    return download_json(f'{api_base.rstrip("/")}/v1/jobs/{job["id"]}/file', token)


def merge_video_info(job: dict, fetched_info: dict | None = None) -> dict:
    info = job_video_info(job)
    if fetched_info:
        info.update({
            'title': fetched_info.get('title') or info.get('title'),
            'channel': fetched_info.get('channel') or fetched_info.get('uploader') or fetched_info.get('creator') or info.get('channel'),
            'duration': fetched_info.get('duration') or info.get('duration'),
        })
    return info


def rewrite_markdown_header(
    md_path: Path,
    *,
    source_url: str,
    info: dict,
    engine: str,
    language: str,
    raw_captions: str | None = None,
) -> None:
    current = md_path.read_text(encoding='utf-8')
    body = current.split('## Text', 1)[1].strip() if '## Text' in current else current.strip()
    raw_line = f'Raw captions: {raw_captions}\n' if raw_captions else ''
    title = info.get('title') or 'Transcript'
    updated = (
        f'# {title}\n\n'
        f'Source: {source_url}\n'
        f'Title: {title}\n'
        f'Channel: {info.get("channel") or "unknown"}\n'
        f'Duration: {format_duration(info.get("duration"))}\n'
        f'Engine: {engine}\n'
        f'Language: {language}\n'
        f'{raw_line}\n'
        '## Text\n\n'
        f'{body}\n'
    )
    md_path.write_text(updated, encoding='utf-8')


def call_parakeet(audio_path: Path, out_json: Path, endpoint: str) -> None:
    # Use curl for multipart because Python stdlib has no pleasant multipart encoder.
    import subprocess
    cmd = [
        'curl', '-sS', '-X', 'POST', endpoint,
        '-F', f'file=@{audio_path}',
        '-F', 'model=parakeet',
        '-F', 'response_format=json',
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=7200)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    out_json.write_text(result.stdout, encoding='utf-8')


def transcript_json_to_md(json_path: Path, md_path: Path, source_url: str, audio_path: Path, info: dict) -> None:
    data = json.loads(json_path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        text = data.get('text') or data.get('transcript') or data.get('content') or json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = str(data)
    title = info.get('title') or 'Transcript'
    md = (
        f'# {title}\n\n'
        f'Source: {source_url}\n'
        f'Title: {title}\n'
        f'Channel: {info.get("channel") or "unknown"}\n'
        f'Duration: {format_duration(info.get("duration"))}\n'
        'Engine: Parakeet FastAPI\n'
        'Language: ASR\n'
        f'Audio: {audio_path}\n\n'
        '## Text\n\n'
        f'{text.strip()}\n'
    )
    md_path.write_text(md, encoding='utf-8')


def wait_for_job(api_base: str, token: str, job_id: str, timeout: int, poll_interval: float) -> dict:
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        job = request_json('GET', f'{api_base.rstrip("/")}/v1/jobs/{job_id}', token)
        progress = job.get('metadata', {}).get('progress_percent')
        status_line = f"status={job['status']}"
        if progress is not None:
            status_line += f" progress={progress}%"
        if status_line != last_status:
            print(status_line)
            last_status = status_line
        if job['status'] == 'completed':
            return job
        if job['status'] == 'error':
            raise RuntimeError(json.dumps(job, indent=2))
        time.sleep(poll_interval)
    raise TimeoutError('Timed out waiting for job')


def create_job(api_base: str, token: str, payload: dict) -> dict:
    job = request_json('POST', f'{api_base.rstrip("/")}/v1/downloads', token, payload)
    print(f"Queued {payload['kind']} job: {job['id']}")
    return job


def main() -> int:
    load_env_files()

    parser = argparse.ArgumentParser(description='Download YouTube audio from homelab yt-dlp API and transcribe via local Parakeet.')
    parser.add_argument('url', help='YouTube URL')
    parser.add_argument('--api-base', default=os.getenv('YTDLP_DOWNLOADER_BASE', 'http://127.0.0.1:8088'))
    parser.add_argument('--token', default=os.getenv('YTDLP_API_TOKEN', ''))
    parser.add_argument('--out-dir', default='/home/ubuntu/workspace/youtube-transcriptions')
    parser.add_argument('--parakeet-endpoint', default=os.getenv('PARAKEET_ENDPOINT', 'http://127.0.0.1:5092/v1/audio/transcriptions'))
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--poll-interval', type=float, default=5.0)
    parser.add_argument('--audio-format', default='original', choices=['original', 'm4a', 'mp3', 'wav', 'opus', 'flac'])
    parser.add_argument('--yt-format', default=os.getenv('YTDLP_YT_FORMAT', 'bestaudio[abr<=64]/bestaudio[abr<=96]/bestaudio/best'))
    parser.add_argument('--preferred-langs', default=os.getenv('YTDLP_CAPTION_LANGS', 'en,en-US,en-GB,de-DE,de'))
    parser.add_argument('--no-captions-first', action='store_true', help='Skip captions/subtitles and force audio+Parakeet ASR')
    args = parser.parse_args()

    if not args.token:
        print('Missing token: pass --token or set YTDLP_API_TOKEN', file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    preferred_langs = [item.strip() for item in args.preferred_langs.split(',') if item.strip()]
    try:
        fetched_info = fetch_video_info(args.api_base, args.token, args.url, args.timeout, args.poll_interval)
        print(f'Video: {video_print_line(merge_video_info({}, fetched_info))}')
    except Exception as exc:
        fetched_info = None
        print(f'Video metadata lookup failed; continuing without channel/title enrichment: {exc}', file=sys.stderr)

    if not args.no_captions_first:
        try:
            caption_job = create_job(args.api_base, args.token, {
                'url': args.url,
                'kind': 'captions',
                'preferred_langs': preferred_langs,
            })
            caption_job = wait_for_job(args.api_base, args.token, caption_job['id'], args.timeout, args.poll_interval)
            filename = caption_job.get('metadata', {}).get('filename') or f'{caption_job["id"]}.md'
            md_path = out_dir / filename
            download_file(f'{args.api_base.rstrip("/")}/v1/jobs/{caption_job["id"]}/file', args.token, md_path)
            final_md_path = markdown_path_for_job(out_dir, caption_job, fetched_info)
            if final_md_path != md_path:
                md_path.rename(final_md_path)
                md_path = final_md_path
            finished_at = time.perf_counter()
            meta = caption_job.get('metadata', {})
            info = merge_video_info(caption_job, fetched_info)
            rewrite_markdown_header(
                md_path,
                source_url=args.url,
                info=info,
                engine=f'yt-dlp {meta.get("caption_source") or "captions"}',
                language=meta.get('caption_language') or 'unknown',
                raw_captions=meta.get('raw_caption_filename'),
            )
            print(f'Created Markdown transcript from captions: {md_path}')
            print(f"Captions: language={meta.get('caption_language')} source={meta.get('caption_source')} format={meta.get('caption_format')}")
            print(f'Timing: captions-total={finished_at - started:.1f}s')
            return 0
        except Exception as exc:
            print(f'Captions path failed; falling back to audio+Parakeet: {exc}', file=sys.stderr)

    job = create_job(args.api_base, args.token, {
        'url': args.url,
        'kind': 'audio',
        'audio_format': args.audio_format,
        'yt_format': args.yt_format,
    })
    job_id = job['id']

    try:
        job = wait_for_job(args.api_base, args.token, job_id, args.timeout, args.poll_interval)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    info = merge_video_info(job, fetched_info)
    filename = job.get('metadata', {}).get('filename') or f'{job_id}.m4a'
    audio_path = out_dir / filename
    download_file(f'{args.api_base.rstrip("/")}/v1/jobs/{job_id}/file', args.token, audio_path)
    downloaded_at = time.perf_counter()
    print(f'Downloaded audio: {audio_path} ({audio_path.stat().st_size / 1024 / 1024:.1f} MiB)')

    json_path = audio_path.with_suffix(audio_path.suffix + '.transcript.json')
    md_path = markdown_path_for_job(out_dir, job, fetched_info)
    call_parakeet(audio_path, json_path, args.parakeet_endpoint)
    transcribed_at = time.perf_counter()
    transcript_json_to_md(json_path, md_path, args.url, audio_path, info)
    print(f'Created Markdown transcript: {md_path}')
    print(f'Raw Parakeet JSON: {json_path}')
    print(
        'Timing: '
        f'download-job+file={downloaded_at - started:.1f}s, '
        f'transcription={transcribed_at - downloaded_at:.1f}s, '
        f'total={transcribed_at - started:.1f}s'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
