"""
কে পোর্টাল ইউজার আর তারা কোথায় যেতে পারে।

পোর্টাল ইউজার = যার লগইন কোনো কর্মচারীর সাথে যুক্ত (Employee.user) কিন্তু অ্যাডমিন প্রোফাইল নেই
(UserProfile.company খালি) এবং staff/superuser নয়। এরা শুধু /me/ এর পেজ দেখতে পারে —
অ্যাডমিনের কোনো পেজ (অন্যদের হাজিরা, বেতন ইত্যাদি) খুলতে গেলে পোর্টালে ফেরত পাঠানো হয়।
মালিক/ম্যানেজার নিজেও কর্মচারী হলে তারা অ্যাডমিন হিসেবেই থাকে।
"""
from django.http import JsonResponse
from django.shortcuts import redirect

PORTAL_PREFIX = '/me/'
# পোর্টাল ইউজারও যেগুলো খুলতে পারে
PORTAL_ALLOWED_PREFIXES = (PORTAL_PREFIX, '/login/', '/logout/', '/static/', '/favicon.ico', '/robots.txt',
                           '/iclock/', '/api/', '/p/')


def is_admin_user(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.company_id)


def portal_employee(user):
    """পোর্টাল ইউজার হলে তার Employee, নইলে None (অ্যাডমিন বা কর্মচারী-লগইন নেই)"""
    if not getattr(user, 'is_authenticated', False) or is_admin_user(user):
        return None
    return getattr(user, 'employee', None)


class PortalAccessMiddleware:
    """পোর্টাল ইউজারকে অ্যাডমিন এলাকার বাইরে রাখে (সাবস্ক্রিপশন middleware এর আগে চলে)"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None
        path = request.path or ''
        if path.startswith(PORTAL_ALLOWED_PREFIXES):
            return None
        if portal_employee(user) is None:
            return None
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or ''):
            return JsonResponse({'detail': 'Not allowed for employee accounts'}, status=403)
        return redirect('portal:home')
