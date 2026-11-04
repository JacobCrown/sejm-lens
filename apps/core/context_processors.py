from .models import IngestionRun


def site(request):
    last_sync = (
        IngestionRun.objects.filter(kind="sync_sejm", status=IngestionRun.Status.SUCCESS)
        .only("finished_at")
        .first()
    )
    return {"last_sync": last_sync.finished_at if last_sync else None}
