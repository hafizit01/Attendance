# payroll/structure_views.py — Salary Base (বেতন কাঠামো), Salary Components, Payslips, Bonus & OT
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance_app import scoping
from attendance_app.models import Department, Employee
from attendance_app.utils.params import int_param, month_param

from .models import EmployeeSalary, EmployeeSalaryComponent, SalaryChange, SalaryComponent, salary_breakdown
from .payslip import payslip_lines
from .views import can_change_payroll_settings, get_salary_summary_data, is_not_attendance_group

PAY_TYPES = [('bank', 'Bank'), ('cash', 'Cash'), ('mixed', 'Bank + Cash')]


def _company(request):
    return scoping.get_scope(request).company


def _d(value, default=Decimal(0)):
    try:
        v = Decimal(str(value).strip().replace(',', '')) if str(value or '').strip() != '' else default
    except (InvalidOperation, ValueError):
        raise ValueError
    if v < 0:
        raise ValueError
    return v.quantize(Decimal('0.01'))


def _pay_type(sal, net):
    if sal is None:
        return ''
    bank = Decimal(sal.bank_transfer_amount or 0)
    if bank <= 0:
        return 'cash'
    return 'bank' if bank >= net else 'mixed'


def _row(emp, components):
    sal = getattr(emp, 'employeesalary', None)
    profile = getattr(emp, 'profile', None)
    data = {'id': emp.id, 'name': emp.name, 'uid': emp.device_user_id,
            'dept': emp.department.name if emp.department_id else '',
            'designation': getattr(profile, 'designation', '') or '', 'configured': bool(sal)}
    if sal:
        b = salary_breakdown(sal)
        values = {c.component_id: str(c.amount) for c in sal.components.all()}
        data.update(basic=b['basic'], allowances=b['gross'] - b['basic'], deductions=b['fixed_deductions'],
                    gross=b['gross'], net=b['net'], bank=Decimal(sal.bank_transfer_amount or 0),
                    pay_type=_pay_type(sal, b['net']), breakdown=b,
                    form={'basic': str(sal.base_salary), 'bank': str(sal.bank_transfer_amount),
                          'bonus_percent': str(sal.yearly_bonus_percent), 'bonus_fixed': str(sal.yearly_bonus_fixed),
                          'bonus_month': sal.bonus_payout_month,
                          'components': {str(c.id): values.get(c.id, '0') for c in components}})
    else:
        data.update(basic=None, allowances=None, deductions=None, gross=None, net=None, bank=None, pay_type='',
                    form={'basic': '', 'bank': '0', 'bonus_percent': '0', 'bonus_fixed': '0', 'bonus_month': 12,
                          'components': {str(c.id): None for c in components}})
    return data


# ---------------------------------------------------------
# Salary Base
# ---------------------------------------------------------
@login_required
@user_passes_test(is_not_attendance_group)
def salary_base(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    SalaryComponent.ensure_defaults(scope.company)
    components = list(SalaryComponent.objects.filter(company=scope.company, is_active=True))

    base = (scoping.employees(Employee.objects.filter(is_active=True), scope)
            .select_related('department', 'employeesalary', 'profile')
            .prefetch_related('employeesalary__components__component').order_by('name'))

    # KPI — ফিল্টারের আগে পুরো স্কোপের
    all_rows = [_row(e, components) for e in base]
    configured = [r for r in all_rows if r['configured']]
    total_commit = sum((r['gross'] for r in configured), Decimal(0))
    kpi = {'commitment': total_commit, 'configured': len(configured), 'staff': len(all_rows),
           'average': (total_commit / len(configured)) if configured else Decimal(0),
           'not_set': len(all_rows) - len(configured),
           'net_total': sum((r['net'] for r in configured), Decimal(0))}

    q = (request.GET.get('q') or '').strip().lower()
    dept_id = int_param(request.GET, 'department')
    pay_type = request.GET.get('type') or ''
    status = request.GET.get('status') or ''
    rows = all_rows
    if q:
        rows = [r for r in rows if q in r['name'].lower() or q == str(r['uid']) or q in r['designation'].lower()]
    if dept_id:
        ids = set(base.filter(department_id=dept_id).values_list('id', flat=True))
        rows = [r for r in rows if r['id'] in ids]
    if pay_type in dict(PAY_TYPES):
        rows = [r for r in rows if r['pay_type'] == pay_type]
    if status == 'configured':
        rows = [r for r in rows if r['configured']]
    elif status == 'not_set':
        rows = [r for r in rows if not r['configured']]

    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    drawer_rows = {r['id']: {k: r[k] for k in ('id', 'name', 'uid', 'dept', 'designation', 'configured')} | {'form': r['form']}
                   for r in page.object_list}
    return render(request, 'payroll/salary_base.html', {
        'tab': 'base', 'kpi': kpi, 'page_obj': page, 'rows': page.object_list,
        'components': components,
        'components_data': [{'id': c.id, 'name': c.name, 'kind': c.kind, 'calc': c.calc_type,
                             'value': str(c.default_value)} for c in components],
        'rows_data': json.loads(json.dumps(drawer_rows, default=str)),
        'departments': scoping.departments(Department.objects.all(), scope).order_by('name'),
        'q': request.GET.get('q', ''), 'selected_department': dept_id, 'pay_type': pay_type, 'status': status,
        'pay_types': PAY_TYPES,
        'can_manage_components': can_change_payroll_settings(request.user, scope.company) and not scope.locked,
    })


@login_required
@user_passes_test(is_not_attendance_group)
@require_POST
def salary_base_save(request):
    """ড্রয়ার থেকে এক কর্মচারীর পুরো বেতন কাঠামো সেভ (JSON রিটার্ন)"""
    scope = scoping.get_scope(request)
    emp = get_object_or_404(scoping.employees(Employee.objects.all(), scope.security), pk=request.POST.get('employee'))
    components = {c.id: c for c in SalaryComponent.objects.filter(company=scope.company, is_active=True)}
    try:
        basic = _d(request.POST.get('basic'))
        bank = _d(request.POST.get('bank'))
        bonus_percent = _d(request.POST.get('bonus_percent'))
        bonus_fixed = _d(request.POST.get('bonus_fixed'))
        amounts = {cid: _d(request.POST.get(f'component_{cid}')) for cid in components}
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'সব ঘরে ০ বা তার বেশি সংখ্যা দিন।'}, status=400)
    try:
        bonus_month = min(max(int(request.POST.get('bonus_month') or 12), 1), 12)
    except ValueError:
        bonus_month = 12
    if basic <= 0:
        return JsonResponse({'status': 'error', 'message': 'Basic salary দিন।'}, status=400)
    if bonus_percent > 100:
        return JsonResponse({'status': 'error', 'message': 'বোনাস ১০০% এর বেশি হতে পারে না।'}, status=400)
    earnings = sum((a for cid, a in amounts.items() if components[cid].kind == 'earning'), Decimal(0))
    deductions = sum((a for cid, a in amounts.items() if components[cid].kind == 'deduction'), Decimal(0))
    gross = basic + earnings
    if deductions > gross:
        return JsonResponse({'status': 'error', 'message': 'মোট কাটা Gross বেতনের বেশি হতে পারে না।'}, status=400)
    if bank > gross - deductions:
        return JsonResponse({'status': 'error', 'message': 'ব্যাংকে পাঠানো টাকা Net বেতনের বেশি হতে পারে না।'}, status=400)

    with transaction.atomic():
        sal, _created = EmployeeSalary.objects.update_or_create(employee=emp, defaults={
            'company': scope.company, 'base_salary': basic, 'bank_transfer_amount': bank,
            'yearly_bonus_percent': bonus_percent, 'yearly_bonus_fixed': bonus_fixed, 'bonus_payout_month': bonus_month})
        for cid, amount in amounts.items():
            EmployeeSalaryComponent.objects.update_or_create(salary=sal, component_id=cid, defaults={'amount': amount})
        SalaryChange.objects.create(salary=sal, basic=basic, gross=gross, deductions=deductions, bank_transfer=bank,
                                    changed_by=request.user,
                                    detail={components[cid].name: str(a) for cid, a in amounts.items() if a})
    msg = f"{emp.name}: বেতন কাঠামো সেভ হয়েছে — Net ৳{gross - deductions:,.0f}।"
    messages.success(request, msg)
    return JsonResponse({'status': 'success', 'message': msg})


@login_required
@user_passes_test(is_not_attendance_group)
def salary_history(request, employee_id):
    scope = scoping.get_scope(request)
    emp = get_object_or_404(scoping.employees(Employee.objects.all(), scope.security), pk=employee_id)
    rows = SalaryChange.objects.filter(salary__employee=emp).select_related('changed_by')[:30]
    return JsonResponse({'employee': emp.name, 'history': [{
        'at': timezone.localtime(r.changed_at).strftime('%d %b %Y, %I:%M %p'), 'by': r.changed_by.username if r.changed_by else '—',
        'basic': str(r.basic), 'gross': str(r.gross), 'deductions': str(r.deductions), 'net': str(r.gross - r.deductions),
        'bank': str(r.bank_transfer), 'detail': r.detail} for r in rows]})


# ---------------------------------------------------------
# Salary Components (ক্যাটাগরি)
# ---------------------------------------------------------
class ComponentForm(forms.ModelForm):
    class Meta:
        model = SalaryComponent
        fields = ['name', 'kind', 'calc_type', 'default_value', 'prorate', 'order', 'is_active']
        widgets = {'kind': forms.RadioSelect, 'calc_type': forms.RadioSelect}

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = SalaryComponent.objects.filter(company=self.company, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("এই নামে একটা অংশ আগে থেকেই আছে।")
        return name


@login_required
@user_passes_test(is_not_attendance_group)
def components_view(request, pk=None):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    SalaryComponent.ensure_defaults(scope.company)
    can_manage = can_change_payroll_settings(request.user, scope.company) and not scope.locked
    instance = get_object_or_404(SalaryComponent, pk=pk, company=scope.company) if pk else None
    form = ComponentForm(request.POST or None, instance=instance, company=scope.company)
    if request.method == 'POST':
        if not can_manage:
            return HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন বেতনের অংশ বদলাতে পারেন।")
        if form.is_valid():
            obj = form.save(commit=False)
            obj.company = scope.company
            obj.save()
            messages.success(request, f"“{obj.name}” সেভ হয়েছে।")
            return redirect('payroll:components')
    from django.db.models import Count, Sum
    comps = (SalaryComponent.objects.filter(company=scope.company)
             .annotate(staff=Count('employee_values', filter=Q(employee_values__amount__gt=0)),
                       total=Sum('employee_values__amount')))
    return render(request, 'payroll/components.html', {
        'tab': 'components', 'form': form, 'editing': instance, 'can_manage': can_manage,
        'earnings': [c for c in comps if c.kind == 'earning'], 'deductions': [c for c in comps if c.kind == 'deduction'],
    })


@login_required
@user_passes_test(is_not_attendance_group)
@require_POST
def component_delete(request, pk):
    scope = scoping.get_scope(request)
    if not can_change_payroll_settings(request.user, scope.company) or scope.locked:
        return HttpResponseForbidden("অনুমতি নেই।")
    comp = get_object_or_404(SalaryComponent, pk=pk, company=scope.company)
    if comp.employee_values.filter(amount__gt=0).exists():
        comp.is_active = False
        comp.save(update_fields=['is_active'])
        messages.warning(request, f"“{comp.name}” কর্মচারীদের বেতনে আছে, তাই বন্ধ (inactive) করা হলো — এখন থেকে বেতনে ধরা হবে না।")
    else:
        comp.delete()
        messages.success(request, "মুছে ফেলা হয়েছে।")
    return redirect('payroll:components')


# ---------------------------------------------------------
# Payslips (অ্যাডমিন)
# ---------------------------------------------------------
def _default_month():
    today = timezone.localdate()
    return (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')


@login_required
@user_passes_test(is_not_attendance_group)
def payslip_list(request):
    month = month_param(request.GET, 'month', _default_month())
    try:
        ctx = get_salary_summary_data(request, month, int_param(request.GET, 'department'))
    except PermissionError:
        return HttpResponseForbidden("Company not set.")
    rows = []
    for s in ctx['summaries']:
        lines = payslip_lines(s)
        rows.append({'s': s, 'earnings': lines['total_earnings'], 'deductions': lines['total_deductions']})
    today = timezone.localdate().replace(day=1)
    months = []
    cur = today
    for _ in range(13):
        months.append(cur)
        cur = (cur - timedelta(days=1)).replace(day=1)
    page_obj = Paginator(rows, 100).get_page(request.GET.get('page'))   # মোট (totals) সবার, পেজে ১০০ জন
    return render(request, 'payroll/payslip_list.html', {
        'tab': 'payslips', 'rows': page_obj.object_list, 'page_obj': page_obj, 'row_count': len(rows),
        'month': month, 'months': months,
        'month_label': datetime.strptime(month, '%Y-%m').strftime('%B %Y'),
        'departments': ctx['departments'], 'selected_department': ctx['selected_department'],
        'totals': {'net': sum((r['s']['final_salary'] for r in rows), Decimal(0)),
                   'earnings': sum((r['earnings'] for r in rows), Decimal(0)),
                   'deductions': sum((r['deductions'] for r in rows), Decimal(0))},
        'is_current': month == timezone.localdate().strftime('%Y-%m'),
    })


@login_required
@user_passes_test(is_not_attendance_group)
def payslip_view(request, employee_id, month):
    """প্রিন্ট করা যায় এমন পে-স্লিপ (HTML); ?format=pdf দিলে PDF"""
    try:
        m = datetime.strptime(month, '%Y-%m')
    except ValueError:
        raise Http404
    try:
        ctx = get_salary_summary_data(request, month, employee_id=employee_id)
    except PermissionError:
        return HttpResponseForbidden("Company not set.")
    rows = [s for s in ctx['summaries'] if s['employee'].id == employee_id]
    if not rows:
        raise Http404("এই কর্মচারীর এই মাসের বেতন পাওয়া যায়নি।")
    s = rows[0]
    emp = s['employee']
    data = {'slip': s, 'lines': payslip_lines(s), 'employee': emp, 'month_label': m.strftime('%B %Y'),
            'company': ctx['company'], 'month': month,
            'designation': getattr(getattr(emp, 'profile', None), 'designation', '') or '',
            'generated_at': timezone.localtime()}
    if request.GET.get('format') == 'pdf':
        from attendance_app.utils.pdf import render_pdf
        data.update(company_name=ctx['company'].name,
                    branch_name=emp.department.branch.name if emp.department_id and emp.department.branch_id else '')
        return render_pdf(request, 'portal/payslip_pdf.html', data, f"Payslip_{emp.name}_{month}.pdf")
    return render(request, 'payroll/payslip.html', data)


# ---------------------------------------------------------
# Bonus & OT
# ---------------------------------------------------------
@login_required
@user_passes_test(is_not_attendance_group)
def bonus_ot(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    sals = (EmployeeSalary.objects.filter(employee__in=scoping.employees(Employee.objects.filter(is_active=True), scope))
            .filter(Q(yearly_bonus_percent__gt=0) | Q(yearly_bonus_fixed__gt=0))
            .select_related('employee', 'employee__department').order_by('bonus_payout_month', 'employee__name'))
    import calendar
    rows = [{'emp': s.employee, 'percent': s.yearly_bonus_percent, 'fixed': s.yearly_bonus_fixed,
             'amount': s.yearly_bonus_amount(), 'month': calendar.month_name[s.bonus_payout_month]} for s in sals]
    by_month = {}
    for r in rows:
        by_month[r['month']] = by_month.get(r['month'], Decimal(0)) + r['amount']
    return render(request, 'payroll/bonus_ot.html', {
        'tab': 'bonus', 'rows': rows, 'by_month': sorted(by_month.items(), key=lambda x: list(calendar.month_name).index(x[0])),
        'total_bonus': sum((r['amount'] for r in rows), Decimal(0)), 'company': scope.company,
        'can_change': can_change_payroll_settings(request.user, scope.company) and not scope.locked,
    })
