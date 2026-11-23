"""
ছুটির আবেদন ও অনুমোদনপত্র (A4) — অ্যাডমিন ও কর্মচারীর পোর্টাল একই তথ্য দেখায়।
কর্মদিবস / কাজে যোগদানের দিন হাজিরার নিয়ম থেকেই (সাপ্তাহিক বন্ধ ও ঘোষিত ছুটি বাদ)।
"""
from datetime import timedelta

from django.utils import timezone

from attendance_app.hr import leave_balance
from attendance_app.utils import attendance_helpers as att_rules


def letter_context(lv):
    emp = lv.employee
    profile = getattr(emp, 'profile', None) if _has_profile(emp) else None

    # ছুটির মধ্যে কতগুলো কর্মদিবস, আর কবে কাজে যোগ দেবেন (পরের কর্মদিবস)
    look_end = lv.end_date + timedelta(days=14)
    [(_e, _s, days)] = att_rules.build_attendance([emp], lv.start_date, look_end)
    in_leave = [d for d in days if d['date'] <= lv.end_date]
    off_days = sum(1 for d in in_leave if d['is_off_day'] or d['is_holiday'])
    working_days = len(in_leave) - off_days
    resume = next((d['date'] for d in days if d['date'] > lv.end_date and not (d['is_off_day'] or d['is_holiday'])), None)

    # ব্যালান্স: এই ছুটি বাদ দিয়ে আগের অবস্থা → এই ছুটি → পরের অবস্থা
    year = lv.start_date.year
    before = leave_balance.total_balance(emp, year, exclude_id=lv.pk)
    rules = leave_balance.type_rules(emp.company)
    t = rules.get(lv.leave_type)
    counts = t.counts_in_limit if t else True
    this_days = leave_balance.days_in_year(lv.start_date, lv.end_date, year)
    balance = None
    if before['allocated'] is not None:
        taken_before = before['used'] + before['pending']
        after = before['allocated'] - taken_before - (this_days if counts else 0)
        balance = {'allocated': before['allocated'], 'taken_before': taken_before, 'this': this_days if counts else 0,
                   'remaining_after': max(after, 0), 'over': max(-after, 0), 'counts': counts}
    type_row = next((r for r in leave_balance.balance(emp, year, exclude_id=lv.pk) if r['type'] == lv.leave_type and r['limited']), None)

    approver = lv.approved_by
    return {
        'lv': lv, 'employee': emp, 'profile': profile, 'company': emp.company,
        'branch': emp.department.branch if emp.department_id and emp.department.branch_id else None,
        'leave_label': t.label if t else lv.get_leave_type_display(),
        'is_paid': t.is_paid if t else True,
        'total_days': lv.days, 'working_days': working_days, 'off_days': off_days, 'resume_date': resume,
        'balance': balance, 'type_row': type_row,
        'approver_name': (approver.get_full_name() or approver.username) if approver else '',
        'decided_at': lv.decided_at,
        'generated_at': timezone.localtime(),
    }


def _has_profile(emp):
    try:
        emp.profile
        return True
    except Exception:
        return False
