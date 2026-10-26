# attendance_app/utils/attendance_helpers.py
# হাজিরা হিসাবের একমাত্র জায়গা (single source of truth)।
# Dashboard, Monthly Report, Attendance List/PDF, Employee Detail/PDF — সবাই এখান থেকেই হিসাব নেয়,
# তাই সব পেজে একই নিয়ম (payroll এর নিয়মের সাথে মিল রেখে):
#
#   • সব timestamp আগে লোকাল টাইমজোনে (Asia/Dhaka) রূপান্তর, তারপর লোকাল তারিখ ধরে দিন ভাগ
#   • In  = দিনের প্রথম পাঞ্চ;  Out = দিনের শেষ পাঞ্চ (কমপক্ষে ২টা পাঞ্চ থাকলে)
#   • শিফট = ডিপার্টমেন্টের in_time/out_time (না থাকলে 10:30–20:30); রাতের শিফট হলে out পরের দিন
#   • Late = শিফট শুরুর পরে In (শুধু কর্মদিবসে)
#   • Work = Out − max(In, শিফট শুরু)  (আগে আসলে বাড়তি সময় ধরা হয় না)
#   • কর্মদিবসে: Over = Work − শিফট, Less = শিফট − Work;  ছুটির দিনে পুরো Work = Over
#   • Leave দিনে শিফটের সমান Work ক্রেডিট;  Absent দিনে পুরো শিফট Less
#   • Status priority: Present > Public Holiday > Weekly Off > Leave > Absent
from collections import defaultdict, namedtuple
from datetime import datetime, time, timedelta

from django.utils import timezone

from attendance_app.models import Attendance, EmployeeTransfer, Holiday, LeaveRequest

DEFAULT_IN_TIME = time(10, 30)
DEFAULT_OUT_TIME = time(20, 30)
DEFAULT_OFF_DAY = 'Friday'

Punch = namedtuple('Punch', 'ts id')
Shift = namedtuple('Shift', 'in_time out_time off_day duration')


def to_local(dt):
    """DB থেকে আসা (UTC) datetime কে লোকাল টাইমজোনে আনে"""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return timezone.localtime(dt)


def local_date(dt):
    return to_local(dt).date()


def shift_for(employee):
    dept = getattr(employee, 'department', None)
    in_t = (dept.in_time if dept and dept.in_time else None) or DEFAULT_IN_TIME
    out_t = (dept.out_time if dept and dept.out_time else None) or DEFAULT_OUT_TIME
    off_day = dept.weekly_off_day if dept else DEFAULT_OFF_DAY
    start = datetime.combine(datetime.min.date(), in_t)
    end = datetime.combine(datetime.min.date(), out_t)
    if end <= start:  # রাতের শিফট
        end += timedelta(days=1)
    return Shift(in_t, out_t, off_day, end - start)


def date_range(start_date, end_date):
    d = start_date
    while d <= end_date:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------
# Bulk loaders (প্রতি রিপোর্টে কয়েকটা মাত্র কোয়েরি)
# ---------------------------------------------------------
def load_punches(employee_ids, start_date, end_date):
    """{(employee_id, local_date): [Punch(ts_local, id), ...] (সময় অনুযায়ী সাজানো)}"""
    rows = (Attendance.objects
            .filter(employee_id__in=list(employee_ids),
                    # USE_TZ=True তে __date লোকাল টাইমজোনে হিসাব হয়
                    timestamp__date__range=(start_date, end_date))
            .order_by('timestamp')
            .values_list('employee_id', 'timestamp', 'id'))
    punches = defaultdict(list)
    for emp_id, ts, pk in rows:
        ts = to_local(ts)
        punches[(emp_id, ts.date())].append(Punch(ts, pk))
    return punches


def load_holidays(company_ids, start_date, end_date):
    """
    {company_id: {date: {branch_id বা None}}} — None মানে সব ব্রাঞ্চের ছুটি।
    কারো জন্য ছুটি কিনা: is_holiday_for(...) দেখুন।
    """
    holidays = defaultdict(lambda: defaultdict(set))
    qs = Holiday.objects.filter(company_id__in=list(company_ids),
                                start_date__lte=end_date, end_date__gte=start_date)
    for h in qs.values('company_id', 'branch_id', 'start_date', 'end_date'):
        for d in date_range(max(h['start_date'], start_date), min(h['end_date'], end_date)):
            holidays[h['company_id']][d].add(h['branch_id'])
    return holidays


def is_holiday_for(company_holidays, day, branch_id):
    """company_holidays = load_holidays(...)[company_id]"""
    branches = company_holidays.get(day)
    return bool(branches) and (None in branches or branch_id in branches)


def load_transfers(employee_ids):
    """{employee_id: [EmployeeTransfer, ...] (effective_date অনুযায়ী সাজানো)}"""
    out = defaultdict(list)
    qs = (EmployeeTransfer.objects.filter(employee_id__in=list(employee_ids))
          .only('employee_id', 'effective_date', 'from_branch_id', 'to_branch_id')
          .order_by('effective_date', 'id'))
    for t in qs:
        out[t.employee_id].append(t)
    return out


def branch_on(current_branch_id, transfers, day):
    """
    ওই দিনে কর্মচারী কোন ব্রাঞ্চে ছিল।
    প্রথম বদলির আগে = প্রথম বদলির from_branch; তারপর = শেষ কার্যকর বদলির to_branch; বদলি না থাকলে বর্তমান।
    """
    if not transfers:
        return current_branch_id
    if day < transfers[0].effective_date:
        return transfers[0].from_branch_id
    branch_id = transfers[0].to_branch_id
    for t in transfers:
        if t.effective_date <= day:
            branch_id = t.to_branch_id
        else:
            break
    return branch_id


def load_leaves(employee_ids, start_date, end_date):
    """{(employee_id, date)} — শুধু Approved leave"""
    leaves = set()
    qs = LeaveRequest.objects.filter(employee_id__in=list(employee_ids), status='Approved',
                                     start_date__lte=end_date, end_date__gte=start_date)
    for lv in qs.values('employee_id', 'start_date', 'end_date'):
        for d in date_range(max(lv['start_date'], start_date), min(lv['end_date'], end_date)):
            leaves.add((lv['employee_id'], d))
    return leaves


# ---------------------------------------------------------
# একদিনের হিসাব
# ---------------------------------------------------------
def evaluate_day(day, punches, shift, is_holiday=False, is_leave=False, elsewhere=False):
    """
    punches: ওই দিনের Punch লিস্ট (সাজানো, লোকাল)।
    elsewhere: ব্রাঞ্চ রিপোর্টে এই দিন কর্মচারী অন্য ব্রাঞ্চে ছিল — কোনো হিসাবে ধরা হয় না।
    রিটার্ন: dict — status, label, in_time, out_time, late, work, over, less, attendance_id, ...
    """
    if elsewhere:
        zero = timedelta()
        return {'date': day, 'weekday': day.strftime('%A'), 'in_time': None, 'out_time': None,
                'late': zero, 'work': zero, 'over': zero, 'less': zero, 'is_holiday': False,
                'is_off_day': False, 'is_elsewhere': True, 'punch_count': 0, 'attendance_id': None,
                'status': 'Other Branch', 'label': 'Other branch'}

    is_off_day = bool(shift.off_day) and day.strftime('%A') == shift.off_day
    is_working_day = not (is_holiday or is_off_day)
    zero = timedelta()

    result = {
        'date': day,
        'weekday': day.strftime('%A'),
        'in_time': None,
        'out_time': None,
        'late': zero,
        'work': zero,
        'over': zero,
        'less': zero,
        'is_holiday': is_holiday,
        'is_off_day': is_off_day,
        'is_elsewhere': False,
        'punch_count': len(punches),
        'attendance_id': punches[0].id if punches else None,
    }

    if punches:
        in_t = punches[0].ts
        out_t = punches[-1].ts if len(punches) > 1 else None
        shift_start = timezone.make_aware(datetime.combine(day, shift.in_time))

        result['in_time'] = in_t
        result['out_time'] = out_t
        if is_working_day and in_t > shift_start:
            result['late'] = in_t - shift_start

        actual_start = max(in_t, shift_start)
        if out_t and out_t > actual_start:
            result['work'] = out_t - actual_start

        if is_working_day:
            result['over'] = max(result['work'] - shift.duration, zero)
            result['less'] = max(shift.duration - result['work'], zero)
        else:
            result['over'] = result['work']

        result['status'] = 'Present'
        result['label'] = ('Present (Holiday)' if is_holiday
                           else 'Present (Weekly Off)' if is_off_day else 'Present')
    elif is_holiday:
        result['status'] = result['label'] = 'Public Holiday'
    elif is_off_day:
        result['status'] = result['label'] = 'Weekly Off'
    elif is_leave:
        result['status'] = result['label'] = 'Leave'
        result['work'] = shift.duration  # পুরো ক্রেডিট
    else:
        result['status'] = result['label'] = 'Absent'
        result['less'] = shift.duration  # পুরো পেনাল্টি

    return result


# ---------------------------------------------------------
# অনেক employee x অনেক দিন
# ---------------------------------------------------------
def build_attendance(employees, start_date, end_date, branch_id=None):
    """
    রিটার্ন: [(employee, shift, [day_result, ...]), ...]  (employees এর ক্রম অনুযায়ী)
    branch_id: ব্রাঞ্চ রিপোর্ট — যেদিন কর্মচারী অন্য ব্রাঞ্চে ছিল (বদলির ইতিহাস অনুযায়ী) সেদিন 'Other Branch'।
    ব্রাঞ্চ-ভিত্তিক ছুটি সবসময় ওই দিনের ব্রাঞ্চ অনুযায়ী ধরা হয়।
    """
    employees = list(employees)
    emp_ids = [e.id for e in employees]
    punches = load_punches(emp_ids, start_date, end_date)
    holidays = load_holidays({e.company_id for e in employees}, start_date, end_date)
    leaves = load_leaves(emp_ids, start_date, end_date)
    transfers = load_transfers(emp_ids)

    out = []
    for emp in employees:
        shift = shift_for(emp)
        company_holidays = holidays.get(emp.company_id, {})
        current_branch = emp.department.branch_id if emp.department_id else None
        emp_transfers = transfers.get(emp.id, [])
        days = []
        for d in date_range(start_date, end_date):
            day_branch = branch_on(current_branch, emp_transfers, d)
            days.append(evaluate_day(
                d, punches.get((emp.id, d), []), shift,
                is_holiday=is_holiday_for(company_holidays, d, day_branch),
                is_leave=(emp.id, d) in leaves,
                elsewhere=branch_id is not None and day_branch != branch_id,
            ))
        out.append((emp, shift, days))
    return out


def summarize(days, shift):
    """একজন employee এর অনেক দিনের যোগফল"""
    zero = timedelta()
    counts = defaultdict(int)
    for d in days:
        counts[d['status']] += 1
    working_days = sum(1 for d in days if not (d['is_holiday'] or d['is_off_day'] or d['is_elsewhere']))
    expected = working_days * shift.duration
    total_work = sum((d['work'] for d in days), zero)
    return {
        'present': counts['Present'],
        'absent': counts['Absent'],
        'leave': counts['Leave'],
        'holiday': counts['Public Holiday'],
        'weekly_off': counts['Weekly Off'],
        'late_days': sum(1 for d in days if d['late'] > zero),
        'total_work': total_work,
        'total_late': sum((d['late'] for d in days), zero),
        'total_over': sum((d['over'] for d in days), zero),
        'total_less': sum((d['less'] for d in days), zero),
        'expected': expected,
        'difference': expected - total_work,  # ধনাত্মক = কম কাজ, ঋণাত্মক = বেশি কাজ
    }


def format_timedelta(td):
    """timedelta -> 'HH:MM:SS' (ঋণাত্মক হলে '-' সহ)"""
    if not isinstance(td, timedelta):
        return "00:00:00"
    total = int(td.total_seconds())
    sign = "-" if total < 0 else ""
    h, r = divmod(abs(total), 3600)
    m, s = divmod(r, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"


def generate_attendance_table(employee_qs, start_date, end_date, branch_id=None):
    """
    Attendance List/PDF এর জন্য: দিন অনুযায়ী (তারপর employee) সারি।
    start_date/end_date: 'YYYY-MM-DD' স্ট্রিং বা date।
    branch_id: ব্রাঞ্চ রিপোর্টে কর্মচারী যেদিন অন্য ব্রাঞ্চে ছিল সেই সারি বাদ।
    """
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

    per_emp = build_attendance(employee_qs, start_date, end_date, branch_id=branch_id)
    rows = []
    for i, day in enumerate(date_range(start_date, end_date)):
        for emp, _shift, days in per_emp:
            d = days[i]
            if d['is_elsewhere']:
                continue
            rows.append({
                'employee': emp,
                'date': day,
                'in_time': d['in_time'],
                'out_time': d['out_time'],
                'status': d['status'],
                'work': d['work'],
                'attendance_id': d['attendance_id'],
                'editable': bool(d['attendance_id']),
            })
    return rows
