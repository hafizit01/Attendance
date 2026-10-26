# attendance_app/branch_views.py — ব্রাঞ্চ, ব্রাঞ্চ ম্যানেজার, বদলি ও ব্রাঞ্চ সুইচার
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import scoping
from .branch_forms import BranchForm, ManagerForm, TransferForm
from .models import Branch, Employee, EmployeeTransfer, UserProfile


def _require_company_admin(request):
    """মালিক/কোম্পানি-লেভেল ইউজার না হলে (ব্রাঞ্চ ম্যানেজার বা কোম্পানি নেই) 403"""
    scope = scoping.get_scope(request)
    if not scope.is_company_admin:
        return scope, HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন এটা করতে পারেন।")
    return scope, None


def _safe_next(request, fallback):
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


# ---------------------------------------------------------
# ব্রাঞ্চ
# ---------------------------------------------------------
@login_required
def branch_list(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')

    branches = (scoping.branches(scope)
                .annotate(dept_count=Count('departments', distinct=True),
                          emp_count=Count('departments__employees', distinct=True),
                          device_count=Count('departments', filter=Q(departments__device_ip__isnull=False), distinct=True))
                .prefetch_related('managers__user'))
    return render(request, 'branches/branch_list.html', {
        'branches': branches,
        'can_manage': scope.is_company_admin,
        'unassigned_departments': scope.company.departments.filter(branch__isnull=True).count() if scope.is_company_admin else 0,
    })


@login_required
def branch_form(request, pk=None):
    scope, denied = _require_company_admin(request)
    if denied:
        return denied
    branch = get_object_or_404(Branch, pk=pk, company=scope.company) if pk else None
    form = BranchForm(request.POST or None, instance=branch, company=scope.company)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.company = scope.company
        obj.save()
        messages.success(request, f"Branch “{obj.name}” {'updated' if pk else 'created'}.")
        return redirect('attendance_app:branch_list')
    return render(request, 'branches/branch_form.html', {
        'form': form, 'branch': branch, 'title': 'Edit branch' if pk else 'Add branch',
    })


@login_required
@require_POST
def branch_delete(request, pk):
    scope, denied = _require_company_admin(request)
    if denied:
        return denied
    branch = get_object_or_404(Branch, pk=pk, company=scope.company)
    try:
        name = branch.name
        branch.delete()
        if request.session.get(scoping.SESSION_KEY) == pk:
            request.session.pop(scoping.SESSION_KEY, None)
        messages.success(request, f"Branch “{name}” deleted.")
    except ProtectedError:
        messages.error(request, f"“{branch.name}” ব্রাঞ্চে এখনও ডিপার্টমেন্ট আছে — আগে সেগুলো অন্য ব্রাঞ্চে সরান বা মুছুন।")
    return redirect('attendance_app:branch_list')


@login_required
@require_POST
def branch_switch(request):
    """মালিকের জন্য উপরের সুইচার: সব ব্রাঞ্চ বা নির্দিষ্ট একটা (শুধু দেখার সুবিধা)"""
    scope = scoping.get_scope(request)
    fallback = request.META.get('HTTP_REFERER') or '/'
    if scope.locked or scope.company is None:
        return redirect(_safe_next(request, fallback))
    value = request.POST.get('branch') or 'all'
    if value == 'all':
        request.session.pop(scoping.SESSION_KEY, None)
    else:
        branch = Branch.objects.filter(pk=value if value.isdigit() else 0, company=scope.company).first()
        if branch:
            request.session[scoping.SESSION_KEY] = branch.pk
    return redirect(_safe_next(request, fallback))


# ---------------------------------------------------------
# ব্রাঞ্চ ম্যানেজার
# ---------------------------------------------------------
@login_required
def manager_add(request, pk):
    scope, denied = _require_company_admin(request)
    if denied:
        return denied
    branch = get_object_or_404(Branch, pk=pk, company=scope.company)
    form = ManagerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(
                username=form.cleaned_data['username'], email=form.cleaned_data['email'] or '',
                password=form.cleaned_data['password'], first_name=form.cleaned_data['full_name'],
            )
            UserProfile.objects.create(user=user, company=scope.company, branch=branch)
        messages.success(request, f"{user.first_name} এখন “{branch.name}” এর ম্যানেজার। লগইন: {user.username}")
        return redirect('attendance_app:branch_list')
    return render(request, 'branches/manager_form.html', {'form': form, 'branch': branch})


@login_required
@require_POST
def manager_remove(request, user_id):
    """ম্যানেজারের লগইন বন্ধ করা (ইউজার মুছি না, যাতে তার করা কাজের রেকর্ড থাকে)"""
    scope, denied = _require_company_admin(request)
    if denied:
        return denied
    profile = get_object_or_404(UserProfile, user_id=user_id, company=scope.company, branch__isnull=False)
    user = profile.user
    user.is_active = False
    user.save(update_fields=['is_active'])
    messages.success(request, f"{user.get_full_name() or user.username} এর লগইন বন্ধ করা হয়েছে।")
    return redirect('attendance_app:branch_list')


# ---------------------------------------------------------
# বদলি
# ---------------------------------------------------------
@login_required
def employee_transfer(request, pk):
    scope, denied = _require_company_admin(request)
    if denied:
        return denied
    employee = get_object_or_404(Employee.objects.select_related('department', 'department__branch'),
                                 pk=pk, company=scope.company)
    form = TransferForm(request.POST or None, employee=employee)
    if request.method == 'POST' and form.is_valid():
        new_dept = form.cleaned_data['to_department']
        old_dept = employee.department
        new_uid = form.cleaned_data['new_device_user_id']
        note = form.cleaned_data['note']
        if new_uid is not None and new_uid != employee.device_user_id:
            note = f"Device ID {employee.device_user_id} → {new_uid}" + (f" · {note}" if note else "")
        else:
            new_uid = None
        with transaction.atomic():
            EmployeeTransfer.objects.create(
                employee=employee,
                from_branch_id=old_dept.branch_id if old_dept else None, to_branch_id=new_dept.branch_id,
                from_department=old_dept, to_department=new_dept,
                effective_date=form.cleaned_data['effective_date'], note=note[:255],
                created_by=request.user,
            )
            employee.department = new_dept
            fields = ['department']
            if new_uid is not None:
                employee.device_user_id = new_uid
                fields.append('device_user_id')
            employee.save(update_fields=fields)
        messages.success(request, f"{employee.name} → {new_dept.branch.name if new_dept.branch_id else ''} · {new_dept.name} "
                                  f"({form.cleaned_data['effective_date']:%d %b %Y} থেকে কার্যকর)")
        return redirect('attendance_app:employee_list')
    history = employee.transfers.select_related('from_branch', 'to_branch', 'from_department', 'to_department', 'created_by')
    return render(request, 'branches/transfer_form.html', {'form': form, 'employee': employee, 'history': history})
