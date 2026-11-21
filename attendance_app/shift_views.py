# attendance_app/shift_views.py — শিফট, শিফট অ্যাসাইন ও সাপ্তাহিক রোস্টার
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .models import Department, Employee, Shift, ShiftAssignment
from .shift_forms import ShiftAssignForm, ShiftForm
from .utils import attendance_helpers as att_rules
from .utils.params import date_param, int_param


def _shifts(scope):
    qs = Shift.objects.filter(company=scope.company).select_related('branch')
    if scope.branch:
        qs = qs.filter(Q(branch__isnull=True) | Q(branch=scope.branch))
    return qs


def _can_edit_shift(scope, shift=None):
    """মালিক সব শিফট; ব্রাঞ্চ ম্যানেজার শুধু নিজের ব্রাঞ্চের শিফট"""
    if scope.is_company_admin:
        return True
    return scope.locked and (shift is None or shift.branch_id == scope.branch_id)


def assign_shift(employees, shift, start, end=None, user=None):
    """
    [start, end] সময়ে কর্মচারীদের এই শিফট দাও। আগের যে অ্যাসাইনমেন্ট এই সময়ের সাথে মেলে
    সেগুলো কেটে ছোট করা হয় (আগের/পরের অংশ থেকে যায়) — তাই পুরনো মাসের রিপোর্ট বদলায় না।
    """
    created = 0
    with transaction.atomic():
        for emp in employees:
            overlapping = (ShiftAssignment.objects.select_for_update()
                           .filter(employee=emp)
                           .filter(Q(end_date__isnull=True) | Q(end_date__gte=start)))
            if end is not None:
                overlapping = overlapping.filter(start_date__lte=end)
            for a in overlapping:
                tail_needed = end is not None and (a.end_date is None or a.end_date > end)
                tail = (end + timedelta(days=1), a.end_date) if tail_needed else None
                if a.start_date < start:
                    a.end_date = start - timedelta(days=1)
                    a.save(update_fields=['end_date'])
                else:
                    a.delete()
                if tail:
                    ShiftAssignment.objects.create(employee=emp, shift=a.shift, start_date=tail[0],
                                                   end_date=tail[1], created_by=a.created_by)
            ShiftAssignment.objects.create(employee=emp, shift=shift, start_date=start, end_date=end, created_by=user)
            created += 1
    return created


@login_required
def shift_list(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')
    today = timezone.localdate()
    current = Q(assignments__start_date__lte=today) & (Q(assignments__end_date__isnull=True) | Q(assignments__end_date__gte=today))
    if scope.branch:
        current &= Q(assignments__employee__department__branch=scope.branch)
    shifts = _shifts(scope).annotate(
        current_count=Count('assignments__employee', filter=current, distinct=True),
        dept_count=Count('default_for_departments', distinct=True),
    )
    departments = scoping.departments(Department.objects.select_related('branch', 'default_shift'), scope)
    return render(request, 'shifts/shift_list.html', {
        'shifts': shifts,
        'departments': departments,
        'can_edit': _can_edit_shift(scope),
        'today': today,
    })


@login_required
def shift_form(request, pk=None):
    scope = scoping.get_scope(request)
    shift = get_object_or_404(_shifts(scope), pk=pk) if pk else None
    if not _can_edit_shift(scope, shift):
        return HttpResponseForbidden("এই শিফট বদলানোর অনুমতি নেই।")
    form = ShiftForm(request.POST or None, instance=shift, company=scope.company,
                     branch=scope.branch if scope.locked else None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.company = scope.company
        if scope.locked:
            obj.branch = scope.branch
        obj.save()
        if pk:
            messages.success(request, f"Shift “{obj.name}” updated. এই শিফটের কর্মচারীদের হিসাব নতুন সময় অনুযায়ী হবে।")
        else:
            messages.success(request, f"Shift “{obj.name}” created. এখন কর্মচারীদের এই শিফট দিন।")
        return redirect('attendance_app:shift_list')
    return render(request, 'shifts/shift_form.html', {
        'form': form, 'shift': shift, 'title': 'Edit shift' if pk else 'New shift',
    })


@login_required
@require_POST
def shift_delete(request, pk):
    scope = scoping.get_scope(request)
    shift = get_object_or_404(_shifts(scope), pk=pk)
    if not _can_edit_shift(scope, shift):
        return HttpResponseForbidden("এই শিফট মোছার অনুমতি নেই।")
    if shift.assignments.exists():
        # মুছলে পুরনো মাসের হিসাব বদলে যেত — তাই শুধু বন্ধ করা যায়
        shift.is_active = False
        shift.save(update_fields=['is_active'])
        messages.warning(request, f"“{shift.name}” কর্মচারীদের দেওয়া আছে, তাই মোছা হয়নি — বন্ধ (inactive) করা হলো। "
                                  "পুরনো হাজিরার হিসাব আগের মতোই থাকবে।")
    else:
        Department.objects.filter(default_shift=shift).update(default_shift=None)
        name = shift.name
        shift.delete()
        messages.success(request, f"Shift “{name}” deleted.")
    return redirect('attendance_app:shift_list')


@login_required
def shift_assign(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    dept_id = int_param(request.GET, 'department') or int_param(request.POST, 'department')
    employees = (scoping.employees(Employee.objects.filter(is_active=True), scope)
                 .select_related('department', 'department__branch').order_by('department__name', 'name'))
    if dept_id:
        employees = employees.filter(department_id=dept_id)
    shifts = _shifts(scope).filter(is_active=True)

    initial = {'start_date': timezone.localdate(), 'shift': int_param(request.GET, 'shift')}
    form = ShiftAssignForm(request.POST or None, shifts=shifts, employees=employees, initial=initial)
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        n = assign_shift(cd['employees'], cd['shift'], cd['start_date'], cd['end_date'], user=request.user)
        until = f" থেকে {cd['end_date']:%d %b %Y} পর্যন্ত" if cd['end_date'] else " থেকে"
        messages.success(request, f"{n} জন কর্মচারীকে “{cd['shift'].name}” শিফট দেওয়া হলো ({cd['start_date']:%d %b %Y}{until})।")
        return redirect(f"{request.path}?department={dept_id or ''}")

    # প্রতিজনের আজকের শিফট দেখানোর জন্য
    today = timezone.localdate()
    emp_list = list(employees)
    resolver = att_rules.ShiftResolver(emp_list, today, today)
    rows = [(e, resolver.on(e, today)) for e in emp_list]
    return render(request, 'shifts/shift_assign.html', {
        'form': form,
        'rows': rows,
        'departments': scoping.departments(Department.objects.select_related('branch'), scope),
        'selected_department': dept_id,
        'has_shifts': shifts.exists(),
    })


@login_required
def shift_roster(request):
    """সাপ্তাহিক রোস্টার: কে কোন দিন কোন শিফটে"""
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    today = timezone.localdate()
    week_start = date_param(request.GET, 'week', today - timedelta(days=(today.weekday() + 2) % 7))  # শনিবার থেকে
    days = [week_start + timedelta(days=i) for i in range(7)]
    dept_id = int_param(request.GET, 'department')

    employees = (scoping.employees(Employee.objects.filter(is_active=True), scope)
                 .select_related('department', 'department__branch', 'department__default_shift')
                 .order_by('department__name', 'name'))
    if dept_id:
        employees = employees.filter(department_id=dept_id)
    emp_list = list(employees[:300])
    resolver = att_rules.ShiftResolver(emp_list, days[0], days[-1])
    holidays = att_rules.load_holidays({scope.company.id}, days[0], days[-1]).get(scope.company.id, {})

    grid = []
    for e in emp_list:
        branch_id = e.department.branch_id if e.department_id else None
        cells = []
        for d in days:
            sh = resolver.on(e, d)
            cells.append({
                'date': d, 'shift': sh,
                'off': bool(sh.off_day) and d.strftime('%A') == sh.off_day,
                'holiday': att_rules.is_holiday_for(holidays, d, branch_id),
            })
        grid.append((e, cells))

    legend = list(_shifts(scope).filter(is_active=True))
    return render(request, 'shifts/roster.html', {
        'days': days, 'grid': grid, 'legend': legend, 'today': today,
        'prev_week': days[0] - timedelta(days=7), 'next_week': days[0] + timedelta(days=7),
        'departments': scoping.departments(Department.objects.select_related('branch'), scope),
        'selected_department': dept_id,
        'truncated': employees.count() > len(emp_list),
    })


@login_required
def employee_shift_history(request, employee_id):
    scope = scoping.get_scope(request)
    emp = get_object_or_404(scoping.employees(Employee.objects.select_related('department'), scope.security),
                            pk=employee_id)
    assignments = emp.shift_assignments.select_related('shift', 'created_by').order_by('-start_date', '-id')
    return render(request, 'shifts/employee_shifts.html', {
        'employee': emp, 'assignments': assignments,
        'base_shift': att_rules.shift_for(emp),
    })


@login_required
@require_POST
def assignment_delete(request, pk):
    scope = scoping.get_scope(request)
    a = get_object_or_404(ShiftAssignment.objects.select_related('employee'), pk=pk,
                          employee__company=scope.company)
    if scope.locked and (not a.employee.department_id or a.employee.department.branch_id != scope.branch_id):
        return HttpResponseForbidden("অনুমতি নেই।")
    emp_id = a.employee_id
    a.delete()
    messages.success(request, "শিফট অ্যাসাইনমেন্ট মুছে ফেলা হলো — ওই সময়ের হিসাব ডিপার্টমেন্টের শিফট অনুযায়ী হবে।")
    return redirect('attendance_app:employee_shift_history', employee_id=emp_id)
