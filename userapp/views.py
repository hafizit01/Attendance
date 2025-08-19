# userapp/views.py  (আপনার login_view যেখানে আছে)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect,resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse

from subscription_app.models import UserSubscription
from django.utils import timezone

def _is_expired(user):
    company = getattr(user, "current_company", None) or getattr(user, "company", None)
    sub = getattr(company, "subscription", None) or \
          UserSubscription.objects.filter(user=user).order_by("-end_date").first()
    if not sub:
        return True  # প্ল্যানই না থাকলে expired-এর মত আচরণ করতে চাইলে True রাখুন
    today = timezone.localdate()
    end_date = sub.end_date.date() if hasattr(sub.end_date, "date") else sub.end_date
    return end_date < today or getattr(sub, "is_expired", False)

def _safe_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        login_url = settings.LOGIN_URL if isinstance(settings.LOGIN_URL, str) else "/login/"
        if not str(nxt).startswith(str(login_url)):
            return nxt
    return "dashboard"  # fallback named url (না থাকলে LOGIN_REDIRECT_URL ব্যবহার করুন)

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username') or request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # ✅ লগইনের পরই expired চেক
            if _is_expired(user):
                messages.warning(request, "Your subscription has expired.")
                return redirect("my_plans")  # 🔁 সরাসরি My Plans/Expired পেজে

            messages.success(request, f'Welcome back, {user.get_username()}!')
            return redirect(_safe_next(request))  # next বা dashboard

        else:
            messages.error(request, '⚠️ Invalid username or password. Please try again.')

    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect(resolve_url(getattr(settings, "LOGIN_URL", "/login/")))
