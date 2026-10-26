# attendance_app/scoping.py
# মাল্টি-ব্রাঞ্চ: কোন ইউজার কোন ডাটা দেখবে — সব ভিউ এখান থেকেই ঠিক করে।
#
#   • ব্রাঞ্চ ম্যানেজার (UserProfile.branch সেট): শুধু নিজের ব্রাঞ্চ — বদলাতে পারে না (locked)
#   • মালিক / কোম্পানি-লেভেল ইউজার (branch খালি): সব ব্রাঞ্চ, অথবা উপরের সুইচার থেকে বাছাই করা একটা
#     (session['branch_id']) — এটা শুধু দেখার সুবিধা, নিরাপত্তার সীমা নয়
from dataclasses import dataclass

from django.db.models import Q

from .models import Branch, EmployeeTransfer

SESSION_KEY = 'branch_id'


@dataclass
class Scope:
    company: object = None
    branch: object = None      # None = সব ব্রাঞ্চ
    locked: bool = False       # True = ব্রাঞ্চ ম্যানেজার (নিজের ব্রাঞ্চের বাইরে যেতে পারবে না)

    @property
    def branch_id(self):
        return self.branch.id if self.branch else None

    @property
    def security(self):
        """
        নির্দিষ্ট রেকর্ড খোলার (edit/delete/detail) সীমা: ম্যানেজার = নিজের ব্রাঞ্চ, মালিক = পুরো কোম্পানি।
        মালিকের সুইচারের বাছাই এখানে প্রযোজ্য নয় — নইলে ব্রাঞ্চ বদলালে খোলা পেজ 404 দিত।
        """
        return self if self.locked else Scope(company=self.company)

    @property
    def is_company_admin(self):
        """পুরো কোম্পানি দেখার/চালানোর অধিকার (ব্রাঞ্চ যোগ, বদলি, ম্যানেজার তৈরি)"""
        return self.company is not None and not self.locked


def get_scope(request):
    cached = getattr(request, '_branch_scope', None)
    if cached is not None:
        return cached

    profile = getattr(request.user, 'profile', None)
    company = getattr(profile, 'company', None)
    scope = Scope(company=company)
    if company is not None:
        manager_branch = getattr(profile, 'branch', None)
        if manager_branch is not None and manager_branch.company_id == company.id:
            scope.branch, scope.locked = manager_branch, True
        else:
            branch_id = request.session.get(SESSION_KEY)
            if branch_id:
                scope.branch = Branch.objects.filter(pk=branch_id, company=company).first()
                if scope.branch is None:
                    request.session.pop(SESSION_KEY, None)
    request._branch_scope = scope
    return scope


def manager_branch_of(user):
    """ফর্মের জন্য: ইউজার ব্রাঞ্চ ম্যানেজার হলে তার ব্রাঞ্চ, নইলে None"""
    profile = getattr(user, 'profile', None)
    branch = getattr(profile, 'branch', None)
    company = getattr(profile, 'company', None)
    if branch is not None and company is not None and branch.company_id == company.id:
        return branch
    return None


# ---------------------------------------------------------
# QuerySet ফিল্টার (বর্তমান ব্রাঞ্চ অনুযায়ী)
# ---------------------------------------------------------
def employees(qs, scope):
    qs = qs.filter(company=scope.company)
    return qs.filter(department__branch=scope.branch) if scope.branch else qs


def departments(qs, scope):
    qs = qs.filter(company=scope.company)
    return qs.filter(branch=scope.branch) if scope.branch else qs


def leaves(qs, scope):
    qs = qs.filter(company=scope.company)
    return qs.filter(employee__department__branch=scope.branch) if scope.branch else qs


def holidays(qs, scope):
    """ব্রাঞ্চ বাছাই থাকলে: সব-ব্রাঞ্চের ছুটি + ওই ব্রাঞ্চের ছুটি"""
    qs = qs.filter(company=scope.company)
    return qs.filter(Q(branch__isnull=True) | Q(branch=scope.branch)) if scope.branch else qs


def attendance(qs, scope):
    qs = qs.filter(employee__company=scope.company)
    return qs.filter(employee__department__branch=scope.branch) if scope.branch else qs


def branches(scope):
    if scope.company is None:
        return Branch.objects.none()
    qs = Branch.objects.filter(company=scope.company)
    return qs.filter(pk=scope.branch_id) if scope.locked else qs


# ---------------------------------------------------------
# ইতিহাস-সহ রিপোর্ট: কোনো সময়ে ব্রাঞ্চে ছিল এমন সবাই
# ---------------------------------------------------------
def employees_for_period(qs, scope, start_date, end_date):
    """
    ব্রাঞ্চ বাছাই থাকলে: এখন ওই ব্রাঞ্চে আছে, অথবা [start, end] সময়ে বদলি হয়ে এসেছে/চলে গেছে —
    এমন সবাই। কোন দিনটা কার ব্রাঞ্চে পড়বে তা attendance_helpers.build_attendance(branch_id=...) ঠিক করে।
    """
    qs = qs.filter(company=scope.company)
    if not scope.branch:
        return qs
    moved_ids = (EmployeeTransfer.objects
                 .filter(employee__company=scope.company)
                 .filter(Q(from_branch=scope.branch) | Q(to_branch=scope.branch))
                 .filter(effective_date__gt=start_date)
                 .values_list('employee_id', flat=True))
    return qs.filter(Q(department__branch=scope.branch) | Q(id__in=list(moved_ids))).distinct()
