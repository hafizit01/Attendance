# attendance_app/leave_views.py — ছুটির বার্ষিক সীমা (allocation) ও ব্যালান্স
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .hr import leave_balance
from .models import Department, Employee, LeaveAllocation, LeaveRequest, LeaveType
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
        self.fields['employee'].queryset = scoping.employees(Employee.objects.filter(is_active=True).select_related('company'), scope).order_by('name')
        choices = [(leave_balance.TOTAL, 'All leave (yearly total)')]
        if scope and scope.company:
            choices += [(t.code, t.label) for t in leave_balance.types_for(scope.company, active_only=True)]
        self.fields['leave_type'] = forms.ChoiceField(choices=choices, label="Leave type")


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
    labels = leave_balance.labels_for(scope.company)
    company_alloc = dict(LeaveAllocation.objects.filter(company=scope.company, employee__isnull=True)
                         .values_list('leave_type', 'days_per_year'))
    overrides = (LeaveAllocation.objects.filter(company=scope.company, employee__isnull=False)
                 .select_related('employee').order_by('employee__name', 'leave_type'))
    if scope.branch:
        overrides = overrides.filter(employee__department__branch=scope.branch)
    used_codes = set(LeaveRequest.objects.filter(company=scope.company).values_list('leave_type', flat=True).distinct())
    return render(request, 'leaves/leave_balance.html', {
        'year': year, 'years': list(range(timezone.localdate().year + 1, timezone.localdate().year - 5, -1)),
        'rows': rows, 'types': [(t, labels.get(t, t)) for t in types],
        'leave_types': [{'obj': t, 'limit': company_alloc.get(t.code), 'in_use': t.code in used_codes}
                        for t in leave_balance.types_for(scope.company)],
        'total_limit': company_alloc.get(leave_balance.TOTAL),
        'overrides': overrides,
        'override_form': EmployeeAllocationForm(scope=scope),
        'departments': scoping.departments(Department.objects.all(), scope),
        'selected_department': dept_id,
        'can_manage': _can_manage(request),
    })


class _BadDays(ValueError):
    pass


def _days_value(raw, what):
    raw = (raw or '').strip()
    if raw == '':
        return None
    try:
        days = int(raw)
    except ValueError:
        raise _BadDays(what)
    if not 0 <= days <= 366:
        raise _BadDays(what)
    return days


@login_required
@require_POST
def leave_allocation_save(request):
    """
    ছুটির ধরন ও বছরের সীমা: সব মিলিয়ে মোট সীমা, প্রতিটা ধরনের Paid / মোট সীমায় গোনা / নিজস্ব সীমা / চালু,
    আর নতুন ধরন যোগ। খালি সীমা = সীমা নেই।
    """
    if not _can_manage(request):
        return HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন ছুটির নিয়ম বদলাতে পারেন।")
    company = scoping.get_scope(request).company
    P = request.POST
    types = leave_balance.types_for(company)
    try:
        with transaction.atomic():
            limits = {leave_balance.TOTAL: _days_value(P.get('total_limit'), 'মোট সীমা')}
            for t in types:
                if f'remove_{t.pk}' in P and not t.is_builtin:
                    if LeaveRequest.objects.filter(company=company, leave_type=t.code).exists():
                        t.is_active = False   # ব্যবহার হয়েছে — মুছি না, শুধু বন্ধ
                        t.save(update_fields=['is_active'])
                    else:
                        LeaveAllocation.objects.filter(company=company, leave_type=t.code).delete()
                        t.delete()
                    continue
                if f'present_{t.pk}' not in P:
                    continue   # পুরনো ফর্ম/অন্য পেজ — এই ধরনটা ছুঁই না
                t.is_paid = f'paid_{t.pk}' in P
                t.counts_in_limit = f'counts_{t.pk}' in P
                t.is_active = f'active_{t.pk}' in P
                t.save(update_fields=['is_paid', 'counts_in_limit', 'is_active'])
                limits[t.code] = _days_value(P.get(f'alloc_{t.pk}'), t.label)

            new_name = ' '.join((P.get('new_name') or '').split())[:20]
            if new_name:
                if new_name.upper() == leave_balance.TOTAL or any(t.code.lower() == new_name.lower() or t.label.lower() == new_name.lower()
                                                                  for t in types):
                    raise ValueError(f'"{new_name}" নামে আগেই একটা ছুটির ধরন আছে।')
                LeaveType.objects.create(company=company, code=new_name, is_paid='new_paid' in P,
                                         counts_in_limit='new_counts' in P, order=100 + len(types))
                limits[new_name] = _days_value(P.get('new_alloc'), new_name)

            for code, days in limits.items():
                qs = LeaveAllocation.objects.filter(company=company, employee__isnull=True, leave_type=code)
                if days is None:
                    qs.delete()
                else:
                    LeaveAllocation.objects.update_or_create(company=company, employee=None, leave_type=code,
                                                             defaults={'days_per_year': days})
    except _BadDays as e:
        messages.error(request, f"{e}: ০ থেকে ৩৬৬ এর মধ্যে সংখ্যা দিন (খালি = সীমা নেই)।")
        return redirect('attendance_app:leave_balance')
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('attendance_app:leave_balance')
    messages.success(request, "ছুটির নিয়ম সেভ হয়েছে। খালি রাখা সীমা = কোনো সীমা নেই।")
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
    exclude = int_param(request.GET, 'exclude')   # এডিটের সময় এই ছুটিটা বাদ দিয়ে হিসাব
    return JsonResponse({
        'employee': emp.name, 'year': year,
        'balance': leave_balance.balance(emp, year, exclude_id=exclude),
        'total': leave_balance.total_balance(emp, year, exclude_id=exclude),
        'types': {t.code: {'label': t.label, 'paid': t.is_paid, 'counts': t.counts_in_limit}
                  for t in leave_balance.types_for(emp.company)},
    })


@login_required
def leave_balance_excel(request):
    year = _year(request)
    scope, _d, rows, types = _balance_rows(request, year)
    labels = leave_balance.labels_for(scope.company)
    cols = [Col('Employee', lambda r: r['employee'].name, width=24), Col('ID', lambda r: r['employee'].device_user_id, 'int'),
            Col('Department', lambda r: r['employee'].department.name if r['employee'].department_id else '')]
    for i, t in enumerate(types):
        cols += [Col(f"{labels[t]} — allowed", (lambda r, i=i: r['cells'][i]['allocated']), 'int'),
                 Col(f"{labels[t]} — used", (lambda r, i=i: r['cells'][i]['used']), 'int'),
                 Col(f"{labels[t]} — pending", (lambda r, i=i: r['cells'][i]['pending']), 'int'),
                 Col(f"{labels[t]} — left", (lambda r, i=i: r['cells'][i]['remaining']), 'int')]
    meta = [('Company', scope.company.name), ('Year', year)] + ([('Branch', scope.branch.name)] if scope.branch else [])
    return excel_response(f"Leave_Balance_{year}", [Sheet('Leave balance', cols, rows, title=f'Leave balance {year}', meta=meta)])


# ---------------------------------------------------------
# ছুটির আবেদন ও অনুমোদনপত্র (A4) — প্রিন্ট / PDF
# ---------------------------------------------------------
def render_leave_letter(request, lv):
    """HTML (প্রিন্ট বোতামসহ) অথবা ?format=pdf দিলে PDF — অ্যাডমিন ও পোর্টাল দুটোই এটা ব্যবহার করে"""
    from .hr.leave_letter import letter_context
    from .utils.pdf import render_pdf
    ctx = letter_context(lv)
    if request.GET.get('format') == 'pdf':
        return render_pdf(request, 'leaves/leave_letter.html', {**ctx, 'pdf': True},
                          f"Leave_{lv.reference}_{lv.employee.name}.pdf".replace(' ', '_'))
    return render(request, 'leaves/leave_letter.html', ctx)


@login_required
def leave_letter(request, pk):
    lv = get_object_or_404(
        scoping.leaves(LeaveRequest.objects.select_related('employee', 'employee__company', 'employee__department',
                                                           'employee__department__branch', 'approved_by'),
                       scoping.get_scope(request).security), pk=pk)
    return render_leave_letter(request, lv)


@login_required
@require_POST
def leave_decide(request, pk):
    """
    এক ক্লিকে Approve / Reject (নোটিফিকেশনের পপআপ ও ছুটির তালিকা থেকে)।
    এই ছুটির নোটিফিকেশনগুলো পড়া হয়ে যায়। XHR হলে JSON, না হলে আগের পেজে ফেরে।
    """
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme
    from .models import Notification
    lv = get_object_or_404(scoping.leaves(LeaveRequest.objects.select_related('employee'),
                                          scoping.get_scope(request).security), pk=pk)
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        return HttpResponseForbidden("ভুল অনুরোধ।")
    lv.status = 'Approved' if action == 'approve' else 'Rejected'
    lv.approved_by = request.user
    lv.decided_at = timezone.now()
    lv.save(update_fields=['status', 'approved_by', 'decided_at'])
    Notification.objects.filter(user=request.user, kind='leave',
                                link=reverse('attendance_app:leave_update', args=[lv.pk])).update(is_read=True)
    letter = reverse('attendance_app:leave_letter', args=[lv.pk])
    text = f"{lv.employee.name} এর ছুটি ({lv.start_date:%d %b} – {lv.end_date:%d %b}) " + (
        "Approved হয়েছে।" if lv.status == 'Approved' else "Rejected হয়েছে।")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'status': lv.status, 'message': text,
                             'letter': letter if lv.status == 'Approved' else ''})
    if lv.status == 'Approved':
        from django.utils.html import format_html
        messages.success(request, format_html('{} <a href="{}" target="_blank" class="font-semibold underline">অনুমোদনপত্র (A4) →</a>', text, letter))
    else:
        messages.warning(request, text)
    nxt = request.POST.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect('attendance_app:leave_list')
