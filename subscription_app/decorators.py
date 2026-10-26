from functools import wraps
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from .utils import is_subscription_expired  # ✅ একমাত্র সাবস্ক্রিপশন চেক

SAFE_URL_NAMES = {
    "login", "logout",
    "password_reset", "password_reset_done",
    "password_reset_confirm", "password_reset_complete",
    "my_plans", "expired_notice",
    # payment flow (যদি ভুল করে গার্ডে ঢুকে পড়ে, ব্লক না করতে চাইলে)
    "create_bkash_payment", "execute_bkash_payment",
    "get_payment_status", "success", "cancel", "payment_details",
}

def subscription_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        match = getattr(request, "resolver_match", None)
        url_name = match.url_name if match else None

        # 1) safelist
        if url_name in SAFE_URL_NAMES:
            return view_func(request, *args, **kwargs)

        # 2) login guard
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)

        # 3) subscription guard (🔑 এক জায়গার truth)
        if is_subscription_expired(user):
            if url_name in {"my_plans", "expired_notice"}:
                return view_func(request, *args, **kwargs)
            try:
                return redirect(reverse("subscription_app:my_plans"))
            except Exception:
                return redirect("subscription_app:my_plans")

        return view_func(request, *args, **kwargs)
    return _wrapped
