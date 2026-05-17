from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(str, Enum):
    queued = 'queued'
    processing = 'processing'
    completed = 'completed'
    error = 'error'


class DownloadKind(str, Enum):
    audio = 'audio'
    video = 'video'
    info = 'info'


class DownloadRequest(BaseModel):
    url: HttpUrl
    kind: DownloadKind = DownloadKind.audio
    audio_format: Literal['original', 'm4a', 'mp3', 'wav', 'opus', 'flac'] = 'original'
    video_format: Literal['mp4', 'mkv', 'webm'] = 'mp4'
    yt_format: str | None = Field(default=None, description='Optional yt-dlp format selector')
    filename_prefix: str | None = Field(default=None, max_length=80)
    allow_playlist: bool = False


class JobRecord(BaseModel):
    id: str
    status: JobStatus
    kind: DownloadKind
    url: str
    created_at: str
    updated_at: str
    title: str | None = None
    extractor: str | None = None
    duration: float | None = None
    output_path: str | None = None
    file_url: str | None = None
    info_url: str | None = None
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def new(cls, job_id: str, req: DownloadRequest) -> 'JobRecord':
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=job_id,
            status=JobStatus.queued,
            kind=req.kind,
            url=str(req.url),
            created_at=now,
            updated_at=now,
        )
