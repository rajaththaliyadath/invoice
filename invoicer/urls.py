from django.urls import path

from . import views

app_name = "invoicer"

urlpatterns = [
    path("", views.week_select, name="week_select"),
    path("entries/", views.entries, name="entries"),
    path("job/<uuid:public_id>/", views.job_progress, name="job_progress"),
    path("job/<uuid:public_id>/status/", views.job_status, name="job_status"),
    path(
        "job/<uuid:public_id>/download/<str:kind>/",
        views.download_job,
        name="download_job",
    ),
    path("done/", views.done, name="done"),
]
