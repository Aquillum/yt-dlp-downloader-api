from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.downloader import run_job
from app.models import DownloadRequest, JobRecord
from app.store import JobStore

app = FastAPI(
    title='Homelab yt-dlp Downloader API',
    description='Minimal API for Hermes/Quillie: fetch YouTube captions first, or download audio/video on a Raspberry Pi residential IP for VPS transcription fallback.',
    version='0.1.0',
)
store = JobStore()
executor = ThreadPoolExecutor(max_workers=settings.max_workers)


@app.on_event('startup')
def startup() -> None:
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store.mark_stale_processing_as_error()


def require_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias='X-API-Key'),
) -> None:
    if not settings.api_token:
        raise HTTPException(status_code=500, detail='YTDLP_API_TOKEN is not configured')
    supplied = None
    if authorization and authorization.lower().startswith('bearer '):
        supplied = authorization.split(' ', 1)[1].strip()
    elif x_api_key:
        supplied = x_api_key.strip()
    if supplied != settings.api_token:
        raise HTTPException(status_code=401, detail='Invalid or missing API token')


@app.get('/health')
def health() -> dict:
    return {
        'ok': True,
        'download_dir': str(settings.download_dir),
        'cookies_file_configured': bool(settings.cookies_file),
        'cookies_file_present': bool(settings.cookies_file and Path(settings.cookies_file).exists()),
        'impersonation_enabled': settings.enable_impersonation,
        'max_workers': settings.max_workers,
    }


@app.post('/v1/downloads', response_model=JobRecord)
def create_download(req: DownloadRequest, _: None = Depends(require_token)) -> JobRecord:
    job_id = uuid4().hex[:16]
    job = JobRecord.new(job_id, req)
    store.put(job)
    executor.submit(run_job, job_id, req, store)
    return job


@app.get('/v1/jobs', response_model=list[JobRecord])
def list_jobs(limit: int = 50, _: None = Depends(require_token)) -> list[JobRecord]:
    return store.list(limit=max(1, min(limit, 200)))


@app.get('/v1/jobs/{job_id}', response_model=JobRecord)
def get_job(job_id: str, _: None = Depends(require_token)) -> JobRecord:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return job


@app.get('/v1/jobs/{job_id}/file')
def get_job_file(job_id: str, _: None = Depends(require_token)) -> FileResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    if job.status != 'completed' or not job.output_path:
        raise HTTPException(status_code=409, detail=f'Job is not completed; status={job.status}')

    path = Path(job.output_path).resolve()
    root = settings.download_dir.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=404, detail='Output file not found')
    return FileResponse(path, filename=path.name)


@app.get('/v1/jobs/{job_id}/info')
def get_job_info(job_id: str, _: None = Depends(require_token)) -> FileResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    path = (settings.download_dir / job_id / 'info.json').resolve()
    root = settings.download_dir.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=404, detail='info.json not found')
    return FileResponse(path, filename='info.json', media_type='application/json')
