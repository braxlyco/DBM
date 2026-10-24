from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Workshop DMS — master data"
admin.site.site_title = "Workshop DMS admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("workshop.urls")),
]
