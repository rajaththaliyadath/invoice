from django import forms

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
        help_text="Australian week runs Monday–Sunday. Any day in that week is fine.",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": _CONTROL,
            }
        ),
    )


class DeliveryLineForm(forms.Form):
    delivery_date = forms.ChoiceField(
        label="Delivery date",
        widget=forms.Select(attrs={"class": _CONTROL}),
    )
    parcels = forms.IntegerField(
        min_value=1,
        label="Number of parcels",
        widget=forms.NumberInput(
            attrs={
                "class": f"{_CONTROL} no-spinner",
                "min": 1,
            }
        ),
    )

    def __init__(self, *args, week_days_iso: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if week_days_iso:
            from datetime import date

            choices = []
            for iso in week_days_iso:
                d = date.fromisoformat(iso)
                label = f"{d.strftime('%A')} {d.strftime('%d/%m/%Y')}"
                choices.append((iso, label))
            self.fields["delivery_date"].choices = choices
