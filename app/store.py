from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models import JobRecord, JobStatus


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.state_dir / 'jobs.json'
        if not self.path.exists():
            self.path.write_text('{}', encoding='utf-8')

    def _load_raw(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            backup = self.path.with_suffix('.broken.json')
            self.path.replace(backup)
            self.path.write_text('{}', encoding='utf-8')
            return {}

    def _save_raw(self, data: dict) -> None:
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)

    def put(self, job: JobRecord) -> None:
        with self._lock:
            data = self._load_raw()
            data[job.id] = job.model_dump(mode='json')
            self._save_raw(data)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            data = self._load_raw()
            raw = data.get(job_id)
            return JobRecord.model_validate(raw) if raw else None

    def list(self, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            data = self._load_raw()
            jobs = [JobRecord.model_validate(raw) for raw in data.values()]
            jobs.sort(key=lambda job: job.created_at, reverse=True)
            return jobs[:limit]

    def update(self, job_id: str, **changes) -> JobRecord:
        with self._lock:
            data = self._load_raw()
            if job_id not in data:
                raise KeyError(job_id)
            record = JobRecord.model_validate(data[job_id])
            payload = record.model_dump(mode='json')
            payload.update(changes)
            payload['updated_at'] = datetime.now(timezone.utc).isoformat()
            data[job_id] = payload
            self._save_raw(data)
            return JobRecord.model_validate(payload)

    def append_log(self, job_id: str, line: str, max_lines: int = 120) -> None:
        with self._lock:
            data = self._load_raw()
            if job_id not in data:
                return
            logs = data[job_id].setdefault('logs', [])
            logs.append(line)
            del logs[:-max_lines]
            data[job_id]['updated_at'] = datetime.now(timezone.utc).isoformat()
            self._save_raw(data)

    def mark_stale_processing_as_error(self) -> None:
        with self._lock:
            data = self._load_raw()
            changed = False
            now = datetime.now(timezone.utc).isoformat()
            for raw in data.values():
                if raw.get('status') in {JobStatus.queued.value, JobStatus.processing.value}:
                    raw['status'] = JobStatus.error.value
                    raw['error'] = 'Service restarted while this job was active'
                    raw['updated_at'] = now
                    changed = True
            if changed:
                self._save_raw(data)
