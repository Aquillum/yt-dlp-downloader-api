#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = None
    headers = {'Authorization': f'Bearer {token}'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def download_file(url: str, token: str, output: Path) -> None:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=3600) as response:
        output.write_bytes(response.read())


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


def transcript_json_to_md(json_path: Path, md_path: Path, source_url: str, audio_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        text = data.get('text') or data.get('transcript') or data.get('content') or json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = str(data)
    md = f'# Transcript\n\nSource: {source_url}\nAudio: {audio_path}\nEngine: Parakeet FastAPI\n\n## Text\n\n{text.strip()}\n'
    md_path.write_text(md, encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Download YouTube audio from homelab yt-dlp API and transcribe via local Parakeet.')
    parser.add_argument('url', help='YouTube URL')
    parser.add_argument('--api-base', default=os.getenv('YTDLP_DOWNLOADER_BASE', 'http://127.0.0.1:8088'))
    parser.add_argument('--token', default=os.getenv('YTDLP_API_TOKEN', ''))
    parser.add_argument('--out-dir', default='/home/ubuntu/workspace/youtube-transcriptions')
    parser.add_argument('--parakeet-endpoint', default=os.getenv('PARAKEET_ENDPOINT', 'http://127.0.0.1:5092/v1/audio/transcriptions'))
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()

    if not args.token:
        print('Missing token: pass --token or set YTDLP_API_TOKEN', file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    job = request_json('POST', f'{args.api_base.rstrip("/")}/v1/downloads', args.token, {
        'url': args.url,
        'kind': 'audio',
        'audio_format': 'm4a',
    })
    job_id = job['id']
    print(f'Queued job: {job_id}')

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        job = request_json('GET', f'{args.api_base.rstrip("/")}/v1/jobs/{job_id}', args.token)
        print(f"status={job['status']}")
        if job['status'] == 'completed':
            break
        if job['status'] == 'error':
            print(json.dumps(job, indent=2), file=sys.stderr)
            return 1
        time.sleep(3)
    else:
        print('Timed out waiting for job', file=sys.stderr)
        return 1

    filename = job.get('metadata', {}).get('filename') or f'{job_id}.m4a'
    audio_path = out_dir / filename
    download_file(f'{args.api_base.rstrip("/")}/v1/jobs/{job_id}/file', args.token, audio_path)
    print(f'Downloaded audio: {audio_path}')

    json_path = audio_path.with_suffix(audio_path.suffix + '.transcript.json')
    md_path = audio_path.with_suffix('.md')
    call_parakeet(audio_path, json_path, args.parakeet_endpoint)
    transcript_json_to_md(json_path, md_path, args.url, audio_path)
    print(f'Created Markdown transcript: {md_path}')
    print(f'Raw Parakeet JSON: {json_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
