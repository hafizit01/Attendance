
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


from .utils import is_subscription_active  # utils.py তে helper ফাংশন রেখেছিলাম

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
        return redirect("dashboard")

    if is_subscription_active(user):
        return redirect("dashboard")
    return redirect("subscription_app:expired")





@login_required
def view_plans(request):
    plans = SubscriptionPlan.objects.all()
    return render(request, 'subscription_app/view_plans.html', {'plans': plans})




def is_subscription_active(sub: UserSubscription) -> bool:
    """end_date যদি DateField হয় → localdate দিয়ে তুলনা।
       DateTimeField হলে → timezone.now() দিয়ে তুলনা (tz-aware করে)।"""
    if not sub or not sub.end_date:
        return False

    field = UserSubscription._meta.get_field("end_date")

    # Pure DateField (NOT DateTimeField)
    if isinstance(field, models.DateField) and not isinstance(field, models.DateTimeField):
        # end_date inclusive (দিনের শেষ পর্যন্ত বৈধ ধরা হলে এটিই যথেষ্ট)
        return timezone.localdate() <= sub.end_date

    # DateTimeField
    end = sub.end_date
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    return timezone.now() <= end

# subscription_app/views.py  (বা যেখানে এই ভিউটা রেখেছেন)
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.shortcuts import render

from .models import UserSubscription
# যদি আপনার নিজের helper থাকে, সেটাই ব্যবহার করুন
from .utils import is_subscription_active  # না থাকলে নিচে @NOTE দেখুন

@login_required
def subscription_expired(request):
    last_sub = (
        UserSubscription.objects
        .filter(user=request.user)
        .order_by("-end_date")
        .first()
    )

    today = timezone.localdate()

    # Active/Expired নির্ধারণ
    active = is_subscription_active(last_sub) if last_sub else False

    # টেমপ্লেটে ব্যবহৃত days_left গণনা (active হলে পজিটিভ/০, expired হলে ০)
    days_left = None
    if last_sub:
        # end_date যদি DateTimeField হয়, date বানিয়ে নিন: last_sub.end_date.date()
        delta = (getattr(last_sub.end_date, "date", lambda: last_sub.end_date)() - today) \
                if hasattr(last_sub.end_date, "date") else (last_sub.end_date - today)
        days_left = max(delta.days, 0)

    return render(
        request,
        "subscription_app/expired.html",  # ✅ ফাইলটা সত্যিই এই পাথে আছে কিনা নিশ্চিত করুন
        {
            "has_plan": bool(last_sub),
            "is_active": active,
            "last_end_date": last_sub.end_date if last_sub else None,
            "days_left": days_left,          # ✅ টেমপ্লেটে ইউজ করছেন, তাই context-এ যোগ করলাম
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