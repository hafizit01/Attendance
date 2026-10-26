
# Create your views here.
from django.shortcuts import render, get_object_or_404,redirect
from django.contrib.auth.decorators import login_required,user_passes_test
from django.utils.timezone import now, localdate
from .models import SubscriptionPlan, UserSubscription
from django.utils import timezone
from datetime import datetime, time
from django.db import models
from django.core.paginator import Paginator
from django.db.models import Q

from django.utils.dateparse import parse_date   # ⬅️ এটা যোগ করো

from .models import UserSubscription


from .utils import is_subscription_active  # ✅ একমাত্র সাবস্ক্রিপশন চেক

@login_required
def post_login_router(request):
    """
    লগইনের পর এখানে এসে সাবস্ক্রিপশন দেখে পাঠাবে:
    Active -> dashboard
    Expired -> expired
    """
    user = request.user

    # চাইলে staff/superuser bypass করো
    if user.is_superuser or user.is_staff:
        return redirect('attendance_app:dashboard')

    if is_subscription_active(user):
        return redirect('attendance_app:dashboard')
    return redirect("subscription_app:expired_notice")





@login_required
def view_plans(request):
    plans = SubscriptionPlan.objects.all()
    return render(request, 'subscription_app/view_plans.html', {'plans': plans})




@login_required
def subscription_expired(request):
    # ইউজারের লাস্ট সাবস্ক্রিপশন
    last_sub = (
        UserSubscription.objects
        .filter(user=request.user)
        .select_related("plan")
        .order_by("-end_date")
        .first()
    )

    # ✅ এখানে user পাঠান, subscription নয়
    active = is_subscription_active(request.user)

    # days_left হিসাব (টেমপ্লেটের জন্য)
    today = timezone.localdate()
    days_left = None
    last_end_date = None
    if last_sub and last_sub.end_date:
        end = last_sub.end_date.date() if hasattr(last_sub.end_date, "date") else last_sub.end_date
        last_end_date = end
        days_left = max((end - today).days, 0)

    return render(
        request,
        "subscription_app/expired.html",
        {
            "has_plan": bool(last_sub),
            "is_active": active,
            "last_end_date": last_end_date,
            "days_left": days_left,
            "today": today,
        },
    )


def subscription_list(request):
    """
    তালিকা + সার্চ + ফিল্টার + পেজিনেশন।
    GET params:
      q           -> user.username / user.get_full_name / plan.name সার্চ
      status      -> 'active' | 'inactive' | '' (all)
      start_from  -> YYYY-MM-DD (start_date >=)
      end_to      -> YYYY-MM-DD (end_date <=)
      per         -> per page (default 20, max 200)
    export=csv    -> CSV এক্সপোর্ট (current filters)
    """
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()
    start_from = parse_date(request.GET.get("start_from") or "")
    end_to = parse_date(request.GET.get("end_to") or "")

    try:
        per = int(request.GET.get("per") or 20)
        if per <= 0 or per > 200:
            per = 20
    except ValueError:
        per = 20

    qs = (UserSubscription.objects
          .select_related("user", "plan")
          .order_by("-end_date", "-id"))

    if q:
        qs = qs.filter(
            Q(user__username__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(plan__name__icontains=q)
        )

    if status == "active":
        qs = qs.filter(active=True)
    elif status == "inactive":
        qs = qs.filter(active=False)

    if start_from:
        qs = qs.filter(start_date__gte=start_from)
    if end_to:
        qs = qs.filter(end_date__lte=end_to)

    # CSV export (optional)
    if (request.GET.get("export") or "").lower() == "csv":
        import csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="subscriptions.csv"'
        writer = csv.writer(resp)
        writer.writerow(["User", "Plan", "Active", "Start Date", "End Date"])
        for s in qs:
            writer.writerow([
                getattr(s.user, "get_full_name", lambda: s.user.username)() or s.user.username,
                getattr(s.plan, "name", ""),
                "Active" if s.active else "Inactive",
                s.start_date,
                s.end_date,
            ])
        return resp

    paginator = Paginator(qs, per)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "subscription_app/subscription_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "start_from": start_from,
            "end_to": end_to,
            "per": per,
            "per_choices": [10, 20, 50, 100, 200],
        }
    )