from django.db import models


class RawDocument(models.Model):
    """Raw copy of a fetched source (API JSON or a PDF file)."""

    source = models.CharField(max_length=32, db_index=True)
    source_id = models.CharField(max_length=255)
    url = models.URLField(max_length=500, blank=True)
    sha256 = models.CharField(max_length=64)
    content_type = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(null=True, blank=True)
    file = models.FileField(upload_to="raw/%Y/%m/", null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "surowy dokument"
        verbose_name_plural = "surowe dokumenty"
        constraints = [
            models.UniqueConstraint(fields=["source", "source_id"], name="uniq_raw_source"),
        ]

    def __str__(self):
        return f"{self.source}:{self.source_id}"


class IngestionRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "W toku"
        SUCCESS = "success", "Sukces"
        FAILED = "failed", "Błąd"

    kind = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "przebieg synchronizacji"
        verbose_name_plural = "przebiegi synchronizacji"

    def __str__(self):
        return f"{self.kind} {self.started_at:%Y-%m-%d %H:%M} ({self.status})"
