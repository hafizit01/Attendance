from django.shortcuts import render

# Create your views here.
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.timezone import now, localdate
from .models import SubscriptionPlan, UserSubscription

@login_required
def view_plans(request):
    plans = SubscriptionPlan.objects.all()
    return render(request, 'subscription_app/view_plans.html', {'plans': plans})

@login_required
def dashboard_subscription_check(request):
    subscription = UserSubscription.objects.filter(
        user=request.user,
        active=True,
        end_date__gt=localdate()
    ).first()

    if not subscription:
        last_sub = UserSubscription.objects.filter(user=request.user).order_by('-end_date').first()
        return render(request, 'subscription_app/expired.html', {
            'last_end_date': last_sub.end_date if last_sub else None
        })

    return render(request, 'subscription_app/dashboard.html', {
        'subscription': subscription
    })
