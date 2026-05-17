from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(',') if item.strip()}


@dataclass(frozen=True)
class Settings:
    api_token: str = field(default_factory=lambda: os.getenv('YTDLP_API_TOKEN', ''))
    download_dir: Path = field(default_factory=lambda: Path(os.getenv('DOWNLOAD_DIR', '/data/downloads')))
    state_dir: Path = field(default_factory=lambda: Path(os.getenv('STATE_DIR', '/data/state')))
    cookies_file: str = field(default_factory=lambda: os.getenv('YOUTUBE_COOKIES_FILE', '').strip())
    max_workers: int = field(default_factory=lambda: int(os.getenv('MAX_WORKERS', '2')))
    cleanup_after_hours: int = field(default_factory=lambda: int(os.getenv('CLEANUP_AFTER_HOURS', '24')))
    default_audio_format: str = field(default_factory=lambda: os.getenv('DEFAULT_AUDIO_FORMAT', 'm4a'))
    default_video_format: str = field(default_factory=lambda: os.getenv('DEFAULT_VIDEO_FORMAT', 'mp4'))
    default_yt_format: str = field(default_factory=lambda: os.getenv('DEFAULT_YT_FORMAT', 'bestaudio/best'))
    max_duration_seconds: int = field(default_factory=lambda: int(os.getenv('MAX_DURATION_SECONDS', '21600')))
    allow_playlists: bool = field(default_factory=lambda: os.getenv('ALLOW_PLAYLISTS', 'false').lower() in {'1', 'true', 'yes'})
    allowed_domains: set[str] = field(
        default_factory=lambda: _split_csv(
            os.getenv(
                'ALLOWED_DOMAINS',
                'youtube.com,www.youtube.com,m.youtube.com,music.youtube.com,youtu.be'
            )
        )
    )
    enable_impersonation: bool = field(default_factory=lambda: os.getenv('ENABLE_IMPERSONATION', 'true').lower() in {'1', 'true', 'yes'})
    youtube_player_clients: str = field(default_factory=lambda: os.getenv('YOUTUBE_PLAYER_CLIENTS', 'default,-tv_simply'))


settings = Settings()
