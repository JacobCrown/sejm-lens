from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sejm Watch"
admin.site.site_title = "Sejm Watch"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.parliament.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
