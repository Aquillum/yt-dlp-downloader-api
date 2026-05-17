from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import traceback
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse
from xml.etree import ElementTree

import yt_dlp

from app.config import settings
from app.models import DownloadKind, DownloadRequest, JobStatus
from app.store import JobStore

_SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9._-]+')
_CAPTION_FORMAT_PREFERENCE = ('json3', 'vtt', 'srv3', 'ttml', 'xml')


def safe_name(value: str | None, fallback: str) -> str:
    value = (value or fallback).strip()[:80]
    value = _SAFE_NAME_RE.sub('_', value).strip('._-')
    return value or fallback


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError('Only http/https URLs are allowed')
    if not host:
        raise ValueError('URL has no host')
    if settings.allowed_domains and host not in settings.allowed_domains:
        raise ValueError(f'Host {host!r} is not allowed. Allowed hosts: {sorted(settings.allowed_domains)}')


def cookies_option() -> dict:
    if settings.cookies_file and Path(settings.cookies_file).exists():
        return {'cookiefile': settings.cookies_file}
    return {}


def impersonation_option() -> dict:
    if not settings.enable_impersonation:
        return {}
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return {'impersonate': ImpersonateTarget('chrome')}
    except Exception as exc:
        return {'_impersonation_warning': f'curl_cffi impersonation unavailable: {exc}'}


def youtube_extractor_args() -> dict:
    clients = [item.strip() for item in settings.youtube_player_clients.split(',') if item.strip()]
    if not clients:
        return {}
    return {'extractor_args': {'youtube': {'player_client': clients}}}


def _lang_matches(candidate: str, preferred: str) -> bool:
    candidate = candidate.lower()
    preferred = preferred.lower()
    return candidate == preferred or candidate.startswith(preferred + '-') or preferred.startswith(candidate + '-')


def _caption_lang_order(info: dict, preferred_langs: list[str] | None = None) -> list[str]:
    order: list[str] = []

    def add(value: str | None) -> None:
        if value and value not in order:
            order.append(value)

    video_lang = info.get('language') or info.get('language_preference')
    if isinstance(video_lang, str) and video_lang.lower().startswith('de'):
        for lang in ('de-DE', 'de'):
            add(lang)
    elif isinstance(video_lang, str) and video_lang.lower().startswith('en'):
        for lang in ('en', 'en-US', 'en-GB'):
            add(lang)
    elif isinstance(video_lang, str):
        add(video_lang)

    for lang in preferred_langs or settings.default_caption_langs:
        add(lang)
    return order


def _pick_caption_format(formats: list[dict]) -> dict | None:
    downloadable = [fmt for fmt in formats if fmt.get('url')]
    if not downloadable:
        return None
    for ext in _CAPTION_FORMAT_PREFERENCE:
        for fmt in downloadable:
            if (fmt.get('ext') or '').lower() == ext:
                return fmt
    return downloadable[0]


def _select_from_caption_map(captions: dict, source: str, lang_order: list[str]) -> dict | None:
    if not captions:
        return None
    keys = list(captions.keys())
    ordered_keys: list[str] = []
    for preferred in lang_order:
        for key in keys:
            if key not in ordered_keys and _lang_matches(key, preferred):
                ordered_keys.append(key)
    for key in keys:
        if key not in ordered_keys:
            ordered_keys.append(key)

    for lang in ordered_keys:
        fmt = _pick_caption_format(captions.get(lang) or [])
        if fmt:
            return {'language': lang, 'source': source, 'format': fmt}
    return None


def select_caption_track(info: dict, preferred_langs: list[str] | None = None) -> dict | None:
    """Pick the best caption track, preferring manual subtitles over auto captions."""
    lang_order = _caption_lang_order(info, preferred_langs)
    return (
        _select_from_caption_map(info.get('subtitles') or {}, 'subtitles', lang_order)
        or _select_from_caption_map(info.get('automatic_captions') or {}, 'automatic_captions', lang_order)
    )


def _join_caption_lines(lines: list[str]) -> str:
    parts: list[str] = []
    previous = None
    for line in lines:
        cleaned = ' '.join(line.split())
        if cleaned and cleaned != previous:
            parts.append(cleaned)
            previous = cleaned
    return ' '.join(parts).strip()


def caption_payload_to_text(payload: str, ext: str | None) -> str:
    payload = payload.strip()
    if not payload:
        return ''

    ext = (ext or '').lower()
    if ext == 'json3' or payload.startswith('{'):
        data = json.loads(payload)
        parts = []
        for event in data.get('events', []):
            text = ''.join(seg.get('utf8', '') for seg in event.get('segs') or [])
            text = ' '.join(text.split())
            if text:
                parts.append(text)
        return _join_caption_lines(parts)

    if ext in {'srv3', 'xml', 'ttml'} or payload.startswith('<'):
        root = ElementTree.fromstring(payload)
        parts = []
        for node in root.iter():
            tag = node.tag.lower().split('}', 1)[-1]
            if tag in {'text', 'p'} and node.text:
                parts.append(html.unescape(node.text.strip()))
        return _join_caption_lines(parts)

    lines = []
    for line in payload.splitlines():
        line = line.strip()
        if not line or line == 'WEBVTT' or '-->' in line or line.isdigit() or line.startswith(('NOTE', 'STYLE', 'REGION')):
            continue
        line = re.sub(r'<[^>]+>', '', line)
        line = html.unescape(line).strip()
        if line:
            lines.append(line)
    return _join_caption_lines(lines)


def base_ydl_options(job_id: str, store: JobStore) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': False,
        'noplaylist': True,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 30,
        **cookies_option(),
        **youtube_extractor_args(),
    }
    imp = impersonation_option()
    warning = imp.pop('_impersonation_warning', None)
    if warning:
        store.append_log(job_id, warning)
    opts.update(imp)
    return opts


class ProgressHook:
    def __init__(self, store: JobStore, job_id: str) -> None:
        self.store = store
        self.job_id = job_id

    def __call__(self, data: dict) -> None:
        status = data.get('status')
        if status == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded = data.get('downloaded_bytes')
            if total and downloaded:
                pct = round(downloaded * 100 / total, 1)
                self.store.update(self.job_id, metadata={'progress_percent': pct})
        elif status == 'finished':
            filename = data.get('filename')
            if filename:
                self.store.append_log(self.job_id, f'Download finished: {Path(filename).name}')


def preflight_info(url: str, req: DownloadRequest, store: JobStore, job_id: str) -> dict:
    opts = {
        **base_ydl_options(job_id, store),
        'skip_download': True,
        'extract_flat': False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get('_type') == 'playlist' and not (req.allow_playlist or settings.allow_playlists):
        raise ValueError('Playlists are disabled. Submit a single video URL or set allow_playlist=true.')

    duration = info.get('duration') or 0
    if duration and duration > settings.max_duration_seconds:
        raise ValueError(f'Video duration {duration}s exceeds MAX_DURATION_SECONDS={settings.max_duration_seconds}')

    store.update(
        job_id,
        title=info.get('title'),
        extractor=info.get('extractor_key') or info.get('extractor'),
        duration=duration or None,
    )
    return info


def choose_output_path(job_dir: Path, prefix: str, desired_ext: str) -> Path:
    candidate = job_dir / f'{prefix}.{desired_ext}'
    if not candidate.exists():
        return candidate
    stem = hashlib.sha1(str(candidate).encode()).hexdigest()[:8]
    return job_dir / f'{prefix}_{stem}.{desired_ext}'


def newest_file(job_dir: Path, exclude_suffixes: tuple[str, ...] = ('.part', '.json')) -> Path:
    files = [p for p in job_dir.iterdir() if p.is_file() and not p.name.endswith(exclude_suffixes)]
    if not files:
        raise FileNotFoundError(f'No media file found in {job_dir}')
    return max(files, key=lambda p: p.stat().st_mtime)


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg failed: {proc.stderr[-2000:]}')


def convert_audio(source: Path, target: Path, fmt: str) -> Path:
    if fmt == 'original':
        return source
    if source.resolve() == target.resolve():
        return source

    cmd = ['ffmpeg', '-y', '-i', str(source), '-vn']
    if fmt == 'wav':
        cmd += ['-ar', '16000', '-ac', '1', str(target)]
    elif fmt == 'mp3':
        cmd += ['-codec:a', 'libmp3lame', '-b:a', '128k', str(target)]
    elif fmt == 'm4a':
        cmd += ['-codec:a', 'aac', '-b:a', '128k', str(target)]
    elif fmt == 'opus':
        cmd += ['-codec:a', 'libopus', '-b:a', '96k', str(target)]
    elif fmt == 'flac':
        cmd += ['-codec:a', 'flac', str(target)]
    else:
        raise ValueError(f'Unsupported audio format: {fmt}')

    run_ffmpeg(cmd)
    return target


def download_info(job_id: str, req: DownloadRequest, store: JobStore, job_dir: Path, info: dict) -> Path:
    info_path = job_dir / 'info.json'
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return info_path


def download_captions(job_id: str, req: DownloadRequest, store: JobStore, job_dir: Path, info: dict) -> Path:
    track = select_caption_track(info, req.preferred_langs)
    if not track:
        raise RuntimeError('No subtitles or automatic captions were exposed by YouTube')

    fmt = track['format']
    ext = (fmt.get('ext') or 'vtt').lower()
    prefix = safe_name(req.filename_prefix or info.get('id'), 'captions')
    raw_path = choose_output_path(job_dir, f'{prefix}.{track["language"]}.{track["source"]}', ext)

    try:
        with urlrequest.urlopen(fmt['url'], timeout=60) as response:
            payload = response.read().decode('utf-8', 'ignore')
    except urlerror.URLError as exc:
        raise RuntimeError(f'Could not download caption payload: {exc}') from exc

    raw_path.write_text(payload, encoding='utf-8')
    text = caption_payload_to_text(payload, ext)
    if not text:
        raise RuntimeError(f'Caption payload was empty for language {track["language"]}')

    txt_path = choose_output_path(job_dir, prefix, 'txt')
    md_path = choose_output_path(job_dir, prefix, 'md')
    txt_path.write_text(text + '\n', encoding='utf-8')
    md_path.write_text(
        '# Transcript\n\n'
        f'Source: {req.url}\n'
        f'Engine: yt-dlp {track["source"]}\n'
        f'Language: {track["language"]}\n'
        f'Raw captions: {raw_path.name}\n\n'
        '## Text\n\n'
        f'{text}\n',
        encoding='utf-8',
    )
    store.update(
        job_id,
        metadata={
            'caption_language': track['language'],
            'caption_source': track['source'],
            'caption_format': ext,
            'raw_caption_filename': raw_path.name,
            'txt_filename': txt_path.name,
        },
    )
    store.append_log(job_id, f'Downloaded {track["source"]} captions: {track["language"]} ({ext})')
    return md_path


def download_audio(job_id: str, req: DownloadRequest, store: JobStore, job_dir: Path, info: dict) -> Path:
    prefix = safe_name(req.filename_prefix or info.get('id'), 'audio')
    opts = {
        **base_ydl_options(job_id, store),
        'format': req.yt_format or settings.default_yt_format,
        'outtmpl': str(job_dir / 'source.%(ext)s'),
        'progress_hooks': [ProgressHook(store, job_id)],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([str(req.url)])

    source = newest_file(job_dir)
    if req.audio_format == 'original':
        target = choose_output_path(job_dir, prefix, source.suffix.lstrip('.') or 'audio')
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
        final = target
    else:
        target = choose_output_path(job_dir, prefix, req.audio_format)
        final = convert_audio(source, target, req.audio_format)
    return final


def download_video(job_id: str, req: DownloadRequest, store: JobStore, job_dir: Path, info: dict) -> Path:
    prefix = safe_name(req.filename_prefix or info.get('id'), 'video')
    output_format = req.video_format or settings.default_video_format
    opts = {
        **base_ydl_options(job_id, store),
        'format': req.yt_format or 'bv*+ba/b',
        'merge_output_format': output_format,
        'outtmpl': str(job_dir / f'{prefix}.%(ext)s'),
        'progress_hooks': [ProgressHook(store, job_id)],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([str(req.url)])
    return newest_file(job_dir)


def run_job(job_id: str, req: DownloadRequest, store: JobStore) -> None:
    job_dir = settings.download_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        validate_url(str(req.url))
        store.update(job_id, status=JobStatus.processing)
        store.append_log(job_id, 'Starting yt-dlp preflight metadata extraction')
        info = preflight_info(str(req.url), req, store, job_id)

        if req.kind == DownloadKind.info:
            output = download_info(job_id, req, store, job_dir, info)
        elif req.kind in {DownloadKind.captions, DownloadKind.subtitles}:
            output = download_captions(job_id, req, store, job_dir, info)
        elif req.kind == DownloadKind.video:
            output = download_video(job_id, req, store, job_dir, info)
        else:
            output = download_audio(job_id, req, store, job_dir, info)

        rel = output.relative_to(settings.download_dir)
        current = store.get(job_id)
        metadata = dict(current.metadata if current else {})
        metadata.update({'filename': output.name, 'size': output.stat().st_size})
        store.update(
            job_id,
            status=JobStatus.completed,
            output_path=str(output),
            file_url=f'/v1/jobs/{job_id}/file',
            info_url=f'/v1/jobs/{job_id}/info',
            metadata=metadata,
        )
        store.append_log(job_id, f'Completed: {rel}')
    except Exception as exc:
        store.update(
            job_id,
            status=JobStatus.error,
            error=str(exc),
            metadata={'traceback_tail': traceback.format_exc()[-3000:]},
        )
        store.append_log(job_id, f'ERROR: {exc}')
