# attendance_app/leave_views.py — ছুটির বার্ষিক সীমা (allocation) ও ব্যালান্স
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .hr import leave_balance
from .models import Department, Employee, LeaveAllocation, LeaveRequest
from .utils.excel import Col, Sheet, excel_response
from .utils.params import int_param


def _year(request):
    y = int_param(request.GET, 'year') or timezone.localdate().year
    return min(max(y, 2000), 2100)


def _can_manage(request):
    scope = scoping.get_scope(request)
    return request.user.is_superuser or scope.is_company_admin


class EmployeeAllocationForm(forms.ModelForm):
    class Meta:
        model = LeaveAllocation
        fields = ['employee', 'leave_type', 'days_per_year']

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].required = True
        self.fields['employee'].queryset = scoping.employees(Employee.objects.filter(is_active=True), scope).order_by('name')


def _balance_rows(request, year):
    scope = scoping.get_scope(request)
    dept_id = int_param(request.GET, 'department')
    employees = scoping.employees(Employee.objects.filter(is_active=True).select_related('department'), scope)
    if dept_id:
        employees = employees.filter(department_id=dept_id)
    employees = list(employees.order_by('department__name', 'name'))
    balances, types = leave_balance.company_balances(employees, year)
    rows = [{'employee': e, 'cells': [balances[e.id][t] for t in types]} for e in employees]
    return scope, dept_id, rows, types


@login_required
def leave_balance_view(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    year = _year(request)
    _s, dept_id, rows, types = _balance_rows(request, year)
    labels = dict(LeaveRequest.LEAVE_TYPES)
    company_alloc = dict(LeaveAllocation.objects.filter(company=scope.company, employee__isnull=True)
                         .values_list('leave_type', 'days_per_year'))
    overrides = (LeaveAllocation.objects.filter(company=scope.company, employee__isnull=False)
                 .select_related('employee').order_by('employee__name', 'leave_type'))
    if scope.branch:
        overrides = overrides.filter(employee__department__branch=scope.branch)
    return render(request, 'leaves/leave_balance.html', {
        'year': year, 'years': list(range(timezone.localdate().year + 1, timezone.localdate().year - 5, -1)),
        'rows': rows, 'types': [(t, labels[t]) for t in types],
        'leave_types': [(t, l, company_alloc.get(t)) for t, l in LeaveRequest.LEAVE_TYPES],
        'overrides': overrides,
        'override_form': EmployeeAllocationForm(scope=scope),
        'departments': scoping.departments(Department.objects.all(), scope),
        'selected_department': dept_id,
        'can_manage': _can_manage(request),
    })


@login_required
@require_POST
def leave_allocation_save(request):
    """কোম্পানির সবার জন্য বছরের সীমা (প্রতি ধরনে একটা সংখ্যা; খালি = সীমা নেই)"""
    if not _can_manage(request):
        return HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন ছুটির সীমা বদলাতে পারেন।")
    company = scoping.get_scope(request).company
    changed = 0
    for lt, _label in LeaveRequest.LEAVE_TYPES:
        raw = (request.POST.get(f'alloc_{lt}') or '').strip()
        qs = LeaveAllocation.objects.filter(company=company, employee__isnull=True, leave_type=lt)
        if raw == '':
            changed += qs.delete()[0]
            continue
        try:
            days = int(raw)
            if not 0 <= days <= 366:
                raise ValueError
        except ValueError:
            messages.error(request, f"{lt}: ০ থেকে ৩৬৬ এর মধ্যে সংখ্যা দিন।")
            return redirect('attendance_app:leave_balance')
        obj, created = LeaveAllocation.objects.update_or_create(company=company, employee=None, leave_type=lt,
                                                                defaults={'days_per_year': days})
        changed += 1
    messages.success(request, "বছরের ছুটির সীমা সেভ হয়েছে। খালি রাখা ধরনগুলোর কোনো সীমা নেই।")
    return redirect('attendance_app:leave_balance')


@login_required
@require_POST
def leave_allocation_override(request):
    if not _can_manage(request):
        return HttpResponseForbidden("অনুমতি নেই।")
    scope = scoping.get_scope(request)
    form = EmployeeAllocationForm(request.POST, scope=scope)
    if form.is_valid():
        emp = form.cleaned_data['employee']
        LeaveAllocation.objects.update_or_create(
            company=scope.company, employee=emp, leave_type=form.cleaned_data['leave_type'],
            defaults={'days_per_year': form.cleaned_data['days_per_year']})
        messages.success(request, f"{emp.name} এর জন্য আলাদা সীমা সেভ হয়েছে।")
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect('attendance_app:leave_balance')


@login_required
@require_POST
def leave_allocation_delete(request, pk):
    if not _can_manage(request):
        return HttpResponseForbidden("অনুমতি নেই।")
    obj = get_object_or_404(LeaveAllocation, pk=pk, company=scoping.get_scope(request).company, employee__isnull=False)
    obj.delete()
    messages.success(request, "আলাদা সীমা মুছে ফেলা হয়েছে — এখন কোম্পানির সাধারণ সীমা প্রযোজ্য।")
    return redirect('attendance_app:leave_balance')


@login_required
def employee_leave_balance_json(request, employee_id):
    """ছুটির ফর্মে কর্মচারী বাছাই করলে তার ব্যালান্স দেখাতে"""
    scope = scoping.get_scope(request)
    emp = get_object_or_404(scoping.employees(Employee.objects.all(), scope.security), pk=employee_id)
    year = _year(request)
    return JsonResponse({'employee': emp.name, 'year': year, 'balance': leave_balance.balance(emp, year)})


@login_required
def leave_balance_excel(request):
    year = _year(request)
    scope, _d, rows, types = _balance_rows(request, year)
    labels = dict(LeaveRequest.LEAVE_TYPES)
    cols = [Col('Employee', lambda r: r['employee'].name, width=24), Col('ID', lambda r: r['employee'].device_user_id, 'int'),
            Col('Department', lambda r: r['employee'].department.name if r['employee'].department_id else '')]
    for i, t in enumerate(types):
        cols += [Col(f"{labels[t]} — allowed", (lambda r, i=i: r['cells'][i]['allocated']), 'int'),
                 Col(f"{labels[t]} — used", (lambda r, i=i: r['cells'][i]['used']), 'int'),
                 Col(f"{labels[t]} — pending", (lambda r, i=i: r['cells'][i]['pending']), 'int'),
                 Col(f"{labels[t]} — left", (lambda r, i=i: r['cells'][i]['remaining']), 'int')]
    meta = [('Company', scope.company.name), ('Year', year)] + ([('Branch', scope.branch.name)] if scope.branch else [])
    return excel_response(f"Leave_Balance_{year}", [Sheet('Leave balance', cols, rows, title=f'Leave balance {year}', meta=meta)])
