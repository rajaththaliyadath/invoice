from __future__ import annotations

from .models import AccountProfile


def current_profile(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"current_profile": None}
    profile = AccountProfile.objects.filter(user=request.user).first()
    return {"current_profile": profile}
