# attendance_project/middleware.py
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, resolve, Resolver404
from django.utils.deprecation import MiddlewareMixin

from subscription_app.utils import is_subscription_active

# সবসময়ই যেগুলো চলতে দিতে হবে (public)
ALWAYS_ALLOWED_URL_NAMES = {
    "login", "logout", "my_plans",
    "account_login", "account_logout",   # (allauth থাকলে)
    "password_reset", "password_reset_done",
    "password_reset_confirm", "password_reset_complete",
    "expired_notice",                    # লুপ এড়াতে
    "expired_notice", "my_plans", "logout",
    "subscription_pricing", "subscription_checkout",
    "subscription_renew", "subscription_payment_return",
    "get_payment_status",

    # ⬇️ payment অ্যাপের views:
    "create_bkash_payment",
    "execute_bkash_payment",
    "success",
    "cancel",
    "payment_details",             # e.g., bkash callback
}
# /admin/ এর নিজস্ব staff লগইন আছে; এটা ব্লক করলে superuser নিজেই লক হয়ে যাবে
ALWAYS_ALLOWED_PREFIXES = ("/static/", "/media/", "/favicon.ico", "/robots.txt", "/api/webhooks/", "/admin/")

# সাবস্ক্রিপশন EXPIRED হলে কেবল এগুলো চলবে (renew/pay/expired/display)
EXPIRED_ALLOWED_URL_NAMES = {
    "expired_notice", "logout",
    "subscription_pricing", "subscription_checkout",
    "subscription_renew", "subscription_payment_return",
    "get_payment_status",
}
# সাবস্ক্রিপশন-সংক্রান্ত যেকোনো রুটে ঢুকতে দিতে চাইলে প্রিফিক্স দিয়ে দিন
EXPIRED_ALLOWED_PREFIXES = ("/subscription/",
    "/payment/",          
    "/Payment-Status/",)

# superuser কে Allow করবেন কিনা (আপনি বলেছেন: "অন্য কেউ কাজ করবে না")
SUBSCRIPTION_SUPERUSER_BYPASS = getattr(settings, "SUBSCRIPTION_SUPERUSER_BYPASS", False)

def _url_name(request):
    m = getattr(request, "resolver_match", None)
    if m:
        return m.url_name
    try:
        return resolve(request.path_info).url_name
    except Resolver404:
        return None

def _wants_json(request) -> bool:
    a = (request.headers.get("Accept") or "").lower()
    x = (request.headers.get("X-Requested-With") or "").lower()
    return ("application/json" in a) or (x == "xmlhttprequest")

class SubscriptionExpiryMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path or ""

        # 1) public safelist (static/media/login/logout/reset/expired/webhooks)
        if path.startswith(ALWAYS_ALLOWED_PREFIXES):
            return None
        url_name = _url_name(request)
        if url_name in ALWAYS_ALLOWED_URL_NAMES:
            return None

        # 2) Unauthenticated হলে—কিছু করবেন না (login flow চলবে)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        # 3) superuser bypass (আপনি চাইলে বন্ধ)
        if SUBSCRIPTION_SUPERUSER_BYPASS and user.is_superuser:
            return None

        # 4) সাবস্ক্রিপশন স্টেট (request.user.profile.company.subscription → utils এ)
        if is_subscription_active(user):
            return None  # সক্রিয় সাবস্ক্রিপশন → পাস

        # 5) EXPIRED — কেবল নির্দিষ্ট রুটগুলোই চলবে
        if url_name in EXPIRED_ALLOWED_URL_NAMES or any(path.startswith(p) for p in EXPIRED_ALLOWED_PREFIXES):
            return None

        # 6) JSON/AJAX হলে redirect নয় → JSON error
        if _wants_json(request):
            return JsonResponse(
                {"detail": "Subscription expired", "code": "subscription_expired"},
                status=402,  # Payment Required (semantic)
            )

        # 7) অন্য সবক্ষেত্রে expiry পেজে রিডাইরেক্ট (লুপ এড়ান)
        expired_url = reverse("subscription_app:expired_notice")

        if path != expired_url:
            return redirect(expired_url)

        return None
