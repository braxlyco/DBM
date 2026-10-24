from django.urls import path

from . import views

app_name = "workshop"

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("jobs/new/", views.job_create, name="job_create"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    path("jobs/<int:pk>/transition/", views.job_transition, name="job_transition"),
    path("jobs/<int:pk>/items/add/", views.item_add, name="item_add"),
    path("jobs/<int:pk>/items/<int:item_pk>/remove/", views.item_remove, name="item_remove"),
    path("jobs/<int:pk>/comeback/", views.job_comeback, name="job_comeback"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
]
