from django.urls import path

from . import views

app_name = "invoicer"

urlpatterns = [
    path("", views.week_select, name="week_select"),
    path("entries/", views.entries, name="entries"),
    path("done/", views.done, name="done"),
    path("download/<str:kind>/", views.download, name="download"),
]
