# payroll/excel_views.py — Salary sheet → Excel (বেতনের পেজ/PDF এর একই হিসাব)
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.utils import timezone

from attendance_app.utils.excel import Col, Sheet, excel_response
from attendance_app.utils.params import int_param, month_param

from .views import get_salary_summary_data


@login_required
def salary_summary_excel(request):
    month_str = month_param(request.GET, 'month', timezone.localdate().strftime('%Y-%m'))
    department_id = int_param(request.GET, 'department')
    employee_id = int_param(request.GET, 'employee')
    try:
        ctx = get_salary_summary_data(request, month_str, department_id, employee_id)
    except PermissionError:
        return HttpResponseForbidden("আপনার কোম্পানি সেট করা নেই।")

    rows = [{**s, 'name': s['employee'].name, 'uid': s['employee'].device_user_id,
             'dept': s['employee'].department.name if s['employee'].department_id else ''} for s in ctx['summaries']]
    cols = [
        Col('Employee', 'name', width=24), Col('ID', 'uid', 'int', width=7), Col('Department', 'dept'),
        Col('Base salary', 'base_salary', 'money'), Col('Bank', 'bank_transfer', 'money'), Col('Cash (base)', 'cash_amount', 'money'),
        Col('Present', 'present_days', 'int'), Col('Leave', 'leave_days', 'int'), Col('Absent', 'absent_days', 'int'),
        Col('Weekly off', 'weekly_off_days', 'int'), Col('Holiday', 'holiday_days', 'int'),
        Col('Late (hh:mm)', 'late_time', 'duration'), Col('Late days', 'late_days', 'int'),
        Col('OT (hh:mm)', 'over_time', 'duration'), Col('Expected (hh:mm)', 'expected_hours', 'duration'),
        Col('Worked (hh:mm)', 'total_work_hours', 'duration'),
        Col('Earned', 'earned_salary', 'money'), Col('OT pay', 'overtime_pay', 'money'), Col('Bonus', 'bonus_amount', 'money'),
    ]
    if ctx['late_deduction_enabled']:
        cols.append(Col('Late deduction', 'late_deduction', 'money'))
    if ctx['loan_deduction_any']:
        cols.append(Col('Loan deduction', 'loan_deduction', 'money'))
    cols += [Col('Final salary', 'final_salary', 'money'), Col('Payable cash', 'payable_cash', 'money'),
             Col('± Base', 'salary_difference', 'money')]
    if ctx['rounding_enabled']:
        cols.insert(-2, Col('Rounding', 'rounding_adjustment', 'money'))

    money_keys = ('base_salary', 'bank_transfer', 'cash_amount', 'earned_salary', 'overtime_pay', 'bonus_amount',
                  'late_deduction', 'loan_deduction', 'final_salary', 'payable_cash', 'salary_difference', 'rounding_adjustment')
    totals = {k: sum(r[k] for r in rows) for k in money_keys}
    totals.update({k: sum(r[k] for r in rows) for k in ('present_days', 'leave_days', 'absent_days', 'late_days')})

    meta = [('Company', ctx['company'].name), ('Month', datetime.strptime(month_str, '%Y-%m').strftime('%B %Y'))]
    if ctx['branch_name']:
        meta.append(('Branch', ctx['branch_name']))
    notes = []
    if ctx['late_deduction_enabled']:
        notes.append(f"Late deduction: every {ctx['lates_per_day']} late working days = 1 day's basic salary.")
    if ctx['rounding_enabled']:
        notes.append("Payable cash rounded up to the nearest 50 / 100 ৳.")
    return excel_response(f"Salary_Sheet_{month_str}",
                          [Sheet('Salary sheet', cols, rows, title='Salary sheet', meta=meta, totals=totals, notes=notes)])
