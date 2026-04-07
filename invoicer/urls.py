from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView

from . import views
from .forms import LoginForm

app_name = "invoicer"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
            authentication_form=LoginForm,
        ),
        name="login",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("account/settings/", views.account_settings, name="account_settings"),
    path("history/", views.invoice_history, name="invoice_history"),
    path("income/", views.income_report, name="income_report"),
    path("job/<uuid:public_id>/save/", views.save_invoice, name="save_invoice"),
    path("", views.week_select, name="week_select"),
    path("entries/", views.entries, name="entries"),
    path("job/<uuid:public_id>/", views.job_progress, name="job_progress"),
    path("job/<uuid:public_id>/status/", views.job_status, name="job_status"),
    path("job/<uuid:public_id>/resend-email/", views.resend_email, name="resend_email"),
    path(
        "job/<uuid:public_id>/download/<str:kind>/",
        views.download_job,
        name="download_job",
    ),
    path("done/", views.done, name="done"),
]
