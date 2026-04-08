from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import AccountProfile

# Tailwind utility classes (compiled into static/invoicer/css/app.css).
_CONTROL = (
    "mt-1 block w-full min-h-[2.75rem] rounded-lg border border-slate-200 bg-white px-3 py-2.5 "
    "text-base text-slate-900 shadow-sm placeholder:text-slate-400 "
    "focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 "
    "sm:min-h-[2.5rem] sm:py-2 touch-manipulation"
)


class WeekAnchorForm(forms.Form):
    reference_date = forms.DateField(
        label="Pick any day in the week you are invoicing",
        help_text="Australian week runs Monday–Sunday. Only today or past dates — future days are disabled.",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": _CONTROL,
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate().isoformat()
        self.fields["reference_date"].widget.attrs["max"] = today

    def clean_reference_date(self):
        d = self.cleaned_data["reference_date"]
        if d > timezone.localdate():
            raise forms.ValidationError("Choose today or an earlier date.")
        return d


class DeliveryLineForm(forms.Form):
    delivery_date = forms.ChoiceField(
        label="Delivery date",
        widget=forms.Select(attrs={"class": _CONTROL}),
    )
    parcels = forms.IntegerField(
        min_value=1,
        label="Number of parcels",
        widget=forms.TextInput(
            attrs={
                "class": f"{_CONTROL} no-spinner tabular-nums",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "autocomplete": "off",
                "placeholder": "e.g. 12",
                "title": "Digits only (whole number)",
            }
        ),
    )

    def __init__(self, *args, week_days_iso: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if week_days_iso:
            from datetime import date

            today = timezone.localdate()
            choices = []
            for iso in week_days_iso:
                d = date.fromisoformat(iso)
                if d > today:
                    continue
                label = f"{d.strftime('%A')} {d.strftime('%d/%m/%Y')}"
                choices.append((iso, label))
            self.fields["delivery_date"].choices = choices


class SignupForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "class": _CONTROL,
                "autocomplete": "email",
                "placeholder": "you@example.com",
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {
                "class": _CONTROL,
                "autocomplete": "username",
                "placeholder": "Choose a username",
            }
        )
        self.fields["password1"].widget.attrs.update(
            {
                "class": _CONTROL,
                "autocomplete": "new-password",
                "placeholder": "Create a password",
            }
        )
        self.fields["password2"].widget.attrs.update(
            {
                "class": _CONTROL,
                "autocomplete": "new-password",
                "placeholder": "Repeat password",
            }
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {
                "class": _CONTROL,
                "autocomplete": "username",
                "placeholder": "Username or email",
            }
        )
        self.fields["username"].label = "Username or email"
        self.fields["password"].widget.attrs.update(
            {
                "class": _CONTROL,
                "autocomplete": "current-password",
                "placeholder": "Password",
            }
        )

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and "@" in username:
            User = get_user_model()
            u = User.objects.filter(email__iexact=username).first()
            if u:
                username = getattr(u, User.USERNAME_FIELD)
        if username and password:
            self.cleaned_data["username"] = username
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class AccountProfileForm(forms.ModelForm):
    class Meta:
        model = AccountProfile
        fields = (
            "profile_photo",
            "employer_name",
            "employer_abn",
            "contractor_name",
            "contractor_abn",
            "rate_per_parcel",
            "bank_name",
            "bsb_number",
            "account_number",
            "account_name",
        )
        widgets = {
            "profile_photo": forms.ClearableFileInput(attrs={"class": _CONTROL}),
            "employer_name": forms.TextInput(attrs={"class": _CONTROL}),
            "employer_abn": forms.TextInput(attrs={"class": _CONTROL}),
            "contractor_name": forms.TextInput(attrs={"class": _CONTROL}),
            "contractor_abn": forms.TextInput(attrs={"class": _CONTROL}),
            "rate_per_parcel": forms.TextInput(
                attrs={
                    "class": f"{_CONTROL} no-spinner",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                    "placeholder": "e.g. 3",
                }
            ),
            "bank_name": forms.TextInput(attrs={"class": _CONTROL}),
            "bsb_number": forms.TextInput(attrs={"class": _CONTROL}),
            "account_number": forms.TextInput(attrs={"class": _CONTROL}),
            "account_name": forms.TextInput(attrs={"class": _CONTROL}),
        }


class MappingSettingsForm(forms.ModelForm):
    class Meta:
        model = AccountProfile
        fields = (
            "use_custom_mapping",
            "map_data_first_row",
            "map_data_last_row",
            "map_sum_row",
            "map_table_header_row",
            "map_invoice_number_cell",
            "map_rate_cell",
            "map_employer_name_cell",
            "map_employer_abn_cell",
            "map_contractor_name_cell",
            "map_contractor_abn_cell",
            "map_contractor_name_line_cell",
            "map_bank_name_cell",
            "map_bsb_cell",
            "map_account_number_cell",
            "map_account_name_cell",
            "map_total_label_cell",
            "map_date_cell",
        )
        widgets = {
            "use_custom_mapping": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
            "map_data_first_row": forms.NumberInput(attrs={"class": _CONTROL, "min": "1"}),
            "map_data_last_row": forms.NumberInput(attrs={"class": _CONTROL, "min": "1"}),
            "map_sum_row": forms.NumberInput(attrs={"class": _CONTROL, "min": "1"}),
            "map_table_header_row": forms.NumberInput(attrs={"class": _CONTROL, "min": "1"}),
            "map_invoice_number_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_rate_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_employer_name_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_employer_abn_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_contractor_name_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_contractor_abn_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_contractor_name_line_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_bank_name_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_bsb_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_account_number_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_account_name_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_total_label_cell": forms.TextInput(attrs={"class": _CONTROL}),
            "map_date_cell": forms.TextInput(attrs={"class": _CONTROL}),
        }
