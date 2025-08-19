# subscription_app/decorators.py
from functools import wraps
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

SAFE_URL_NAMES = {
    "login", "logout",
    "password_reset", "password_reset_done",
    "password_reset_confirm", "password_reset_complete",
    "my_plans", "expired_notice",
}

def subscription_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        # 1) safelist by url_name (namespace থাকলেও url_name শুধু 'login'/'logout' ই থাকে)
        match = getattr(request, "resolver_match", None)
        if match and match.url_name in SAFE_URL_NAMES:
            return view_func(request, *args, **kwargs)

        # 2) not logged in → login পেজে (next সহ)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)

        # 3) superuser bypass করবেন কিনা (ইচ্ছে হলে True করুন)
        if getattr(user, "is_superuser", False) and getattr(settings, "SUBSCRIPTION_SUPERUSER_BYPASS", False):
            return view_func(request, *args, **kwargs)

        # 4) subscription state নির্ধারণ
        company = getattr(request, "current_company", None) or getattr(user, "company", None)
        sub = getattr(company, "subscription", None) if company else None

        # সাব না থাকলেও expired হিসেবে ধরুন (সব private ব্লক হবে)
        is_expired = True
        if sub is not None:
            if hasattr(sub, "is_expired"):
                is_expired = bool(sub.is_expired)
            else:
                from django.utils import timezone
                end_date = getattr(sub, "end_date", None)
                if end_date is not None:
                    today = timezone.localdate()
                    if hasattr(end_date, "date"):
                        end_date = end_date.date()
                    is_expired = end_date < today

        if is_expired:
            # expired হলে শুধু my_plans/renew/payment ইত্যাদি allow করুন
            if match and match.url_name in {"my_plans", "expired_notice"}:
                return view_func(request, *args, **kwargs)
            try:
                return redirect(reverse("subscription_app:expired_notice"))
            except Exception:
                return redirect("my_plans")

        # ok
        return view_func(request, *args, **kwargs)
    return _wrapped
