# attendance_app/excel_views.py — হাজিরা ও ছুটির রিপোর্টের Excel এক্সপোর্ট
# প্রতিটা এক্সপোর্ট HTML/PDF রিপোর্টের একই হেল্পার ব্যবহার করে, তাই সংখ্যা সব জায়গায় এক।
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.utils.timezone import localdate

from . import scoping
from .models import Department, Employee, LeaveRequest
from .utils import attendance_helpers as att_rules
from .utils.excel import Col, Sheet, excel_response
from .utils.params import date_param, int_param

_CODES = {'Present': 'P', 'Absent': 'A', 'Leave': 'L', 'Public Holiday': 'H', 'Weekly Off': 'W', 'Other Branch': '·'}


def _company(request):
    return getattr(getattr(request.user, 'profile', None), 'company', None)


def _name_of(qs, pk):
    return (qs.filter(pk=pk).values_list('name', flat=True).first() or '') if pk else ''


def _filters_meta(request, scope, start, end, department_id=None, employee_id=None):
    meta = [('Company', scope.company.name if scope.company else ''), ('Period', f"{start:%d %b %Y} – {end:%d %b %Y}")]
    if scope.branch:
        meta.append(('Branch', scope.branch.name))
    if department_id:
        meta.append(('Department', _name_of(scoping.departments(Department.objects.all(), scope), department_id)))
    if employee_id:
        meta.append(('Employee', _name_of(scoping.employees(Employee.objects.all(), scope), employee_id)))
    return meta


@login_required
def attendance_list_excel(request):
    """Attendance list → Excel: Daily (প্রতি দিন প্রতি জন) + Register (কর্মচারী × তারিখ) + Summary"""
    if not _company(request):
        return HttpResponseForbidden("Company not set.")
    scope = scoping.get_scope(request)
    start = date_param(request.GET, 'start_date', localdate())
    end = date_param(request.GET, 'end_date', localdate())
    employee_id, department_id = int_param(request.GET, 'employee'), int_param(request.GET, 'department')
    employees = scoping.employees_for_period(
        Employee.objects.select_related('department', 'department__branch'), scope, start, end)
    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department_id=department_id)

    per_emp = [(e, s, days) for e, s, days in att_rules.build_attendance(employees, start, end, branch_id=scope.branch_id)
               if not all(d['is_elsewhere'] for d in days)]
    meta = _filters_meta(request, scope, start, end, department_id, employee_id)

    daily = []
    for i, day in enumerate(att_rules.date_range(start, end)):
        for emp, _s, days in per_emp:
            d = days[i]
            if d['is_elsewhere']:
                continue
            daily.append({'date': day, 'weekday': day.strftime('%a'), 'name': emp.name, 'uid': emp.device_user_id,
                          'dept': emp.department.name if emp.department_id else '',
                          'shift': d.get('shift').name if d.get('shift') and d['shift'].name else '',
                          'in': d['in_time'], 'out': d['out_time'], 'late': d['late'], 'work': d['work'],
                          'over': d['over'], 'status': d['label']})
    sheets = [Sheet('Daily', [
        Col('Date', 'date', 'date'), Col('Day', 'weekday', width=7), Col('Employee', 'name', width=24),
        Col('ID', 'uid', 'int', width=7), Col('Department', 'dept'), Col('Shift', 'shift'),
        Col('In', 'in', 'datetime'), Col('Out', 'out', 'datetime'),
        Col('Late', 'late', 'duration'), Col('Worked', 'work', 'duration'), Col('Overtime', 'over', 'duration'),
        Col('Status', 'status', width=18),
    ], daily, title='Attendance — daily', meta=meta)]

    dates = list(att_rules.date_range(start, end))
    if len(dates) > 1:
        cols = [Col('Employee', 'name', width=24), Col('ID', 'uid', 'int', width=7)]
        cols += [Col(f"{d:%d %a}", (lambda r, i=i: r['cells'][i]), width=6) for i, d in enumerate(dates)]
        cols += [Col('Present', 'present', 'int'), Col('Late', 'late', 'int'), Col('Absent', 'absent', 'int'),
                 Col('Leave', 'leave', 'int'), Col('Off/Hol', 'off', 'int'), Col('Worked', 'worked', 'duration')]
        reg = []
        for emp, shift, days in per_emp:
            sm = att_rules.summarize(days, shift)
            reg.append({'name': emp.name, 'uid': emp.device_user_id,
                        'cells': [('P*' if d['late'] else 'P') if d['status'] == 'Present' else _CODES.get(d['status'], '?')
                                  for d in days],
                        'present': sm['present'], 'late': sm['late_days'], 'absent': sm['absent'], 'leave': sm['leave'],
                        'off': sm['holiday'] + sm['weekly_off'], 'worked': sm['total_work']})
        sheets.append(Sheet('Register', cols, reg, title='Attendance register', meta=meta,
                            notes=['P = Present, P* = Present (late), A = Absent, L = Leave, W = Weekly off, H = Holiday, · = other branch']))
    return excel_response(f"Attendance_{start}_to_{end}", sheets)


@login_required
def monthly_report_excel(request):
    from .views import get_monthly_report_context
    ctx = get_monthly_report_context(request)
    scope = scoping.get_scope(request)
    rows = [{**r, 'name': r['employee'].name, 'uid': r['employee'].device_user_id,
             'dept': r['employee'].department.name if r['employee'].department_id else ''} for r in ctx['report_data']]
    cols = [Col('Employee', 'name', width=24), Col('ID', 'uid', 'int', width=7), Col('Department', 'dept'),
            Col('Present', 'present_days', 'int'), Col('Absent', 'absent_days', 'int'), Col('Leave', 'leave_days', 'int'),
            Col('Weekly off', 'weekly_off_days', 'int'), Col('Holiday', 'holiday_days', 'int'),
            Col('Worked', 'total_work_hours', 'duration'), Col('Expected', 'expected_work_time_excl_off', 'duration'),
            Col('Late', 'late_time', 'duration'), Col('Overtime', 'over_time', 'duration'), Col('Less', 'less_time', 'duration'),
            Col('Short (+) / extra (−) hours', lambda r: round(r['work_time_difference'].total_seconds() / 3600, 2), 'decimal')]
    totals = {k: sum(r[k] for r in rows) for k in ('present_days', 'absent_days', 'leave_days', 'weekly_off_days', 'holiday_days')}
    meta = _filters_meta(request, scope, ctx['start_date'], ctx['end_date'], ctx['selected_department'], ctx['selected_employee'])
    return excel_response(f"Monthly_Report_{ctx['start_date']}_to_{ctx['end_date']}",
                          [Sheet('Monthly report', cols, rows, title='Monthly work time report', meta=meta, totals=totals)])


@login_required
def employee_attendance_excel(request, employee_id):
    from .views import _employee_report
    emp, _company_obj, start, end, days, s = _employee_report(request, employee_id)
    rows = [{'date': d['date'], 'weekday': d['weekday'], 'shift': d['shift'].name if d.get('shift') and d['shift'].name else '',
             'in': d['in_time'], 'out': d['out_time'], 'late': d['late'], 'work': d['work'], 'over': d['over'],
             'less': d['less'], 'status': d['label']} for d in days]
    cols = [Col('Date', 'date', 'date'), Col('Day', 'weekday'), Col('Shift', 'shift'), Col('In', 'in', 'time'),
            Col('Out', 'out', 'time'), Col('Late', 'late', 'duration'), Col('Worked', 'work', 'duration'),
            Col('Overtime', 'over', 'duration'), Col('Less', 'less', 'duration'), Col('Status', 'status', width=18)]
    totals = {'late': s['total_late'], 'work': s['total_work'], 'over': s['total_over'], 'less': s['total_less']}
    meta = [('Employee', f"{emp.name} (ID {emp.device_user_id})"),
            ('Department', emp.department.name if emp.department_id else '—'),
            ('Period', f"{start:%d %b %Y} – {end:%d %b %Y}"),
            ('Present / Absent / Leave', f"{s['present']} / {s['absent']} / {s['leave']}")]
    return excel_response(f"Attendance_{emp.name}_{start}_to_{end}",
                          [Sheet('Attendance', cols, rows, title=f"Attendance — {emp.name}", meta=meta, totals=totals)])


@login_required
def leave_summary_excel(request):
    from .views import get_leave_summary_data
    ctx = get_leave_summary_data(request.user, request.GET, scoping.get_scope(request))
    if 'error' in ctx:
        return HttpResponseForbidden(ctx['error'])
    cols = [Col('Employee', 'employee', width=26), Col('Requests', 'requests', 'int'),
            Col('Total days', 'total_days', 'int'), Col('By type', 'type_breakdown', width=40)]
    meta = [('Company', ctx['company_name'])]
    if ctx['date_from'] or ctx['date_to']:
        meta.append(('Period', f"{ctx['date_from'] or '…'} – {ctx['date_to'] or '…'}"))
    if ctx['status']:
        meta.append(('Status', ctx['status']))
    totals = {'requests': sum(r['requests'] for r in ctx['summary']), 'total_days': sum(r['total_days'] for r in ctx['summary'])}

    # দ্বিতীয় শিট: প্রতিটা আবেদন
    scope = scoping.get_scope(request)
    qs = scoping.leaves(LeaveRequest.objects.select_related('employee', 'employee__department', 'approved_by'), scope)
    if ctx['status']:
        qs = qs.filter(status=ctx['status'])
    if ctx['date_from']:
        qs = qs.filter(end_date__gte=ctx['date_from'])
    if ctx['date_to']:
        qs = qs.filter(start_date__lte=ctx['date_to'])
    if ctx['department']:
        qs = qs.filter(employee__department_id=ctx['department'])
    if ctx['q']:
        qs = qs.filter(employee__name__icontains=ctx['q'])
    return excel_response(f"Leave_Report_{date.today()}", [
        Sheet('Summary', cols, ctx['summary'], title='Leave summary', meta=meta, totals=totals),
        _leave_detail_sheet(qs.order_by('-start_date', '-id'), meta),
    ])


def _leave_detail_sheet(qs, meta):
    rows = [{'name': lv.employee.name, 'dept': lv.employee.department.name if lv.employee.department_id else '',
             'type': lv.get_leave_type_display(), 'start': lv.start_date, 'end': lv.end_date,
             'days': (lv.end_date - lv.start_date).days + 1, 'status': lv.status, 'reason': lv.reason or '',
             'by': lv.approved_by.username if lv.approved_by else '', 'applied': lv.applied_on} for lv in qs]
    cols = [Col('Employee', 'name', width=24), Col('Department', 'dept'), Col('Leave type', 'type'),
            Col('From', 'start', 'date'), Col('To', 'end', 'date'), Col('Days', 'days', 'int'), Col('Status', 'status'),
            Col('Reason', 'reason', width=36), Col('Approved by', 'by'), Col('Applied on', 'applied', 'datetime')]
    return Sheet('Requests', cols, rows, title='Leave requests', meta=meta,
                 totals={'days': sum(r['days'] for r in rows)})


@login_required
def leave_list_excel(request):
    scope = scoping.get_scope(request)
    qs = scoping.leaves(LeaveRequest.objects.select_related('employee', 'employee__department', 'approved_by'), scope)
    q = request.GET.get('q', '')
    if q:
        qs = qs.filter(employee__name__icontains=q)
    meta = [('Company', scope.company.name if scope.company else '')] + ([('Search', q)] if q else [])
    return excel_response(f"Leave_Requests_{date.today()}", [_leave_detail_sheet(qs.order_by('-start_date', '-id'), meta)])
