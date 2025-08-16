# subscription_app/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from .utils import is_subscription_active

def subscription_required(view_func=None, *, expired_url_name="subscription_app:expired"):
    def decorator(fn):
        @login_required
        @wraps(fn)
        def _wrapped(request, *args, **kwargs):
            # চাইলে staff/superuser bypass
            if request.user.is_superuser or request.user.is_staff:
                return fn(request, *args, **kwargs)
            if not is_subscription_active(request.user):
                return redirect(expired_url_name)
            return fn(request, *args, **kwargs)
        return _wrapped
    return decorator(view_func) if view_func else decorator
