"""Local copies of Sejm MP portraits.

The official photo endpoints answer in a few seconds once a page asks for dozens
of them at once, and they send no cache headers. Pages therefore use files under
static/portraits/, collected into the image and served by WhiteNoise.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from django.conf import settings
from django.templatetags.static import static

JPEG_MAGIC = b"\xff\xd8\xff"


def relative_name(term_id: int, sejm_id: int, variant: str) -> str:
    suffix = "-mini" if variant == "mini" else ""
    return f"portraits/{term_id}/{sejm_id}{suffix}.jpg"


def file_path(term_id: int, sejm_id: int, variant: str) -> Path:
    return Path(settings.BASE_DIR) / "static" / relative_name(term_id, sejm_id, variant)


def remote_url(term_id: int, sejm_id: int, variant: str) -> str:
    endpoint = "photo-mini" if variant == "mini" else "photo"
    base = settings.SEJM_API_URL.rstrip("/")
    return f"{base}/term{term_id}/MP/{sejm_id}/{endpoint}"


def local_url(term_id: int, sejm_id: int, variant: str) -> str | None:
    if not file_path(term_id, sejm_id, variant).is_file():
        return None
    name = relative_name(term_id, sejm_id, variant)
    try:
        return static(name)
    except ValueError:
        return settings.STATIC_URL + name


def fetch_portraits(
    term_id: int,
    sejm_ids: list[int],
    *,
    client: httpx.Client,
    force: bool = False,
    workers: int = 4,
    on_progress=None,
) -> dict[str, int]:
    """Download missing portraits. Returns counts: saved, skipped, failed."""
    jobs: list[tuple[int, str]] = []
    skipped = 0
    for sejm_id in sejm_ids:
        for variant in ("mini", "full"):
            if not force and file_path(term_id, sejm_id, variant).is_file():
                skipped += 1
                continue
            jobs.append((sejm_id, variant))

    saved = failed = 0

    def one(job: tuple[int, str]) -> bool:
        sejm_id, variant = job
        try:
            resp = client.get(remote_url(term_id, sejm_id, variant))
        except httpx.HTTPError:
            return False
        if resp.status_code != 200 or not resp.content.startswith(JPEG_MAGIC):
            return False
        path = file_path(term_id, sejm_id, variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return True

    if not jobs:
        return {"saved": 0, "skipped": skipped, "failed": 0}

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for ok in pool.map(one, jobs):
            done += 1
            if ok:
                saved += 1
            else:
                failed += 1
            if on_progress and done % 25 == 0:
                on_progress(done, len(jobs), saved, failed)
    return {"saved": saved, "skipped": skipped, "failed": failed}
