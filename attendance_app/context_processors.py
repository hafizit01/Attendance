# attendance_app/context_processors.py
# সব টেমপ্লেটে ব্রাঞ্চ সুইচার / ব্রাঞ্চ ব্যাজের তথ্য
from . import scoping


def branch_context(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    scope = scoping.get_scope(request)
    if scope.company is None:
        return {'branch_scope': scope}
    from .models import Notification
    unread = Notification.objects.filter(user=user, is_read=False)
    return {
        'branch_scope': scope,
        # হেডারের ঘণ্টা: না-পড়া সংখ্যা ও সর্বশেষ কয়েকটা (দুইটা ছোট কোয়েরি)
        'notif_unread_count': unread.count(),
        'notif_latest': list(Notification.objects.filter(user=user).order_by('-created_at')[:6]),
        # মালিকের জন্য সুইচারের তালিকা (শুধু চালু ব্রাঞ্চ; বাছাই করা থাকলে সেটা বন্ধ হলেও দেখাই)
        'switchable_branches': (
            list(scoping.branches(scope).filter(is_active=True) | scoping.branches(scope).filter(pk=scope.branch_id))
            if scope.is_company_admin else []
        ),
    }
