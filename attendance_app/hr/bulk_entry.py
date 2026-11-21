"""
Bulk / Official Duty হাজিরা আর Missing-punch quick-fix — কম্পিউটার অপারেটরের জন্য।

হিসাবের নিয়ম বদলায়নি: এখানে শুধু In/Out পাঞ্চ লেখা হয় (is_manual=True), তারপর বেতন, রিপোর্ট সবই
আগের মতো attendance_helpers.build_attendance দিয়ে ওই পাঞ্চ থেকে হিসাব হয়।
  • Present / Official Duty / WFH = শিফটের In → Out (বা অপারেটরের দেওয়া সময়)
  • Half Day = In থেকে শিফটের অর্ধেক সময় (Out না দিলে) — তাই বাকি অর্ধেক "Less" হিসেবে কাটা যায়
  • Out সময় In এর সমান বা আগে হলে পরের দিনের Out (রাতের শিফট)

একই (employee, timestamp, status) আবার এলে নতুন রেকর্ড নয়, পুরনোটাই আপডেট হয় — তাই একই তারিখ
বারবার দিলেও uniq_employee_timestamp_status ভাঙে না।
"""
import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from attendance_app.models import Attendance
from attendance_app.utils import attendance_helpers as att_rules

ENTRY_TYPES = dict(Attendance.ENTRY_TYPES)
MAX_DAYS = 62                 # এক বারে সর্বোচ্চ তারিখ-সীমা
MAX_CELLS = 20000             # কর্মচারী × দিন
QUEUE_LIMIT = 1500            # কুইউতে এক বারে সর্বোচ্চ সারি
_CHUNK = 300

_TIME_RE = re.compile(r'^\s*(\d{1,2})(?::?(\d{2}))?\s*([ap]\.?m?\.?)?\s*$', re.I)


def parse_time(value):
    """'9' '930' '09:30' '18:30' '6:30pm' → time; ফাঁকা → None; ভুল → ValueError"""
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, time):
        return value
    m = _TIME_RE.match(str(value))
    if not m:
        raise ValueError(f"ভুল সময়: {value}")
    h, mnt, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or '').lower()
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError(f"ভুল সময়: {value}")
        h = h % 12 + (12 if ampm.startswith('p') else 0)
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        raise ValueError(f"ভুল সময়: {value}")
    return time(h, mnt)


def hhmm(t):
    if t is None:
        return ''
    if isinstance(t, datetime):
        t = att_rules.to_local(t)
    return t.strftime('%H:%M')


def day_times(day, shift, entry_type, in_t=None, out_t=None):
    """ওই দিনের (in_dt, out_dt) — লোকাল aware। ফাঁকা সময় শিফট থেকে।"""
    in_t = in_t or shift.in_time
    in_dt = timezone.make_aware(datetime.combine(day, in_t))
    if out_t is None and entry_type == 'half_day':
        out_dt = in_dt + shift.duration / 2 + shift.brk
    else:
        out_dt = timezone.make_aware(datetime.combine(day, out_t or shift.out_time))
        if out_dt <= in_dt:
            out_dt += timedelta(days=1)
    return in_dt, out_dt


def shift_defaults(shift):
    """গ্রিড/ফর্মে দেখানোর জন্য শিফটের সময় (HH:MM)"""
    day = timezone.localdate()
    _in, half_out = day_times(day, shift, 'half_day')
    return {'in': hhmm(shift.in_time), 'out': hhmm(shift.out_time), 'half_out': hhmm(half_out),
            'name': shift.name or 'Office time', 'night': bool(shift.night)}


def _key(emp_id, ts, status):
    return emp_id, ts.astimezone(dt_timezone.utc).replace(microsecond=0), status


def upsert_punches(punches, user, delete_ids=()):
    """
    punches: [(employee, ts_aware, 'In'/'Out', entry_type, remarks)]
    delete_ids: আগে মুছতে হবে এমন পাঞ্চ (overwrite)। সব এক transaction এ।
    রিটার্ন: (created, updated)
    """
    now = timezone.now()
    targets = {}
    for emp, ts, status, entry_type, remarks in punches:
        targets[_key(emp.id, ts, status)] = (emp, ts, status, entry_type, remarks)

    with transaction.atomic():
        ids = list(delete_ids)
        for i in range(0, len(ids), _CHUNK):
            Attendance.objects.filter(pk__in=ids[i:i + _CHUNK]).delete()

        existing = {}
        emp_ids = sorted({k[0] for k in targets})
        stamps = sorted({k[1] for k in targets})
        for i in range(0, len(stamps), _CHUNK):
            for a in Attendance.objects.filter(employee_id__in=emp_ids, timestamp__in=stamps[i:i + _CHUNK]):
                existing[_key(a.employee_id, a.timestamp, a.status)] = a

        to_create, to_update = [], []
        for key, (emp, ts, status, entry_type, remarks) in targets.items():
            a = existing.get(key)
            if a is None:
                a = Attendance(employee=emp, timestamp=ts, status=status)
                to_create.append(a)
            else:
                to_update.append(a)
            a.company_id = emp.company_id
            a.is_manual = True
            a.entry_type = entry_type
            a.remarks = remarks[:255]
            a.updated_by = user
            a.updated_at = now
        if to_update:
            Attendance.objects.bulk_update(
                to_update, ['company', 'is_manual', 'entry_type', 'remarks', 'updated_by', 'updated_at'], batch_size=500)
        if to_create:
            # আগে existing দেখে নেওয়ায় এখানে ডুপ্লিকেট আসার কথা নয়; একই সময়ে দুজন অপারেটর একই জিনিস
            # সেভ করলেও ignore_conflicts থাকায় crash হবে না (অন্যজনের রেকর্ড থেকে যায়)
            Attendance.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)
    return len(to_create), len(to_update)


# ---------------------------------------------------------
# Feature A: তারিখ-সীমা ধরে অনেকজনের হাজিরা
# ---------------------------------------------------------
def plan_bulk(employees, start, end, entry_type, in_t=None, out_t=None,
              skip_off_days=True, overwrite=False):
    """
    কোন দিনে কী লেখা হবে (dry-run)। রিটার্ন: {'items': [(emp, day, in_dt, out_dt, old_ids)], counts...}
    """
    employees = list(employees)
    counts = Counter()
    items = []
    per_emp = Counter()
    for emp, _base, days in att_rules.build_attendance(employees, start, end):
        for d in days:
            if d.get('is_elsewhere'):
                continue
            if skip_off_days and (d['is_holiday'] or d['is_off_day']):
                counts['weekly_off' if d['is_off_day'] and not d['is_holiday'] else 'holiday'] += 1
                continue
            if d['status'] == 'Leave' and not overwrite:
                counts['leave'] += 1
                continue
            if d['punch_count'] and not overwrite:
                counts['existing'] += 1
                continue
            in_dt, out_dt = day_times(d['date'], d['shift'], entry_type, in_t, out_t)
            if out_dt <= in_dt:
                counts['invalid'] += 1
                continue
            items.append((emp, d['date'], in_dt, out_dt, d['punch_ids'] if overwrite else ()))
            if d['punch_count']:
                counts['replaced'] += 1
            per_emp[emp.id] += 1
    days_each = set(per_emp.values())
    return {
        'items': items,
        'records': len(items),
        'employees': len(per_emp),
        'selected': len(employees),
        'days_each': days_each.pop() if len(days_each) == 1 else None,
        'skipped_weekly_off': counts['weekly_off'],
        'skipped_holiday': counts['holiday'],
        'skipped_existing': counts['existing'],
        'skipped_leave': counts['leave'],
        'replaced': counts['replaced'],
        'invalid': counts['invalid'],
    }


def apply_bulk(plan, user, entry_type, remarks):
    punches, old_ids = [], []
    for emp, _day, in_dt, out_dt, ids in plan['items']:
        punches.append((emp, in_dt, 'In', entry_type, remarks))
        punches.append((emp, out_dt, 'Out', entry_type, remarks))
        old_ids.extend(ids)
    created, updated = upsert_punches(punches, user, delete_ids=old_ids)
    return {'punches_created': created, 'punches_updated': updated}


# ---------------------------------------------------------
# Feature B: Missing / Pending কুইউ
# ---------------------------------------------------------
def missing_rows(employees, start, end, issue=''):
    """
    যেসব দিনে No Punch (কর্মদিবসে অনুপস্থিত, ছুটি নয়) বা Missing Out (In আছে, Out নেই)।
    রিটার্ন: (rows, truncated)
    """
    rows = []
    for emp, _base, days in att_rules.build_attendance(employees, start, end):
        dept = emp.department if emp.department_id else None
        for d in days:
            if d.get('is_elsewhere'):
                continue
            if d['status'] == 'Absent':
                kind = 'no_punch'
            elif d['in_time'] and not d['out_time']:
                kind = 'missing_out'
            else:
                continue
            if issue and kind != issue:
                continue
            sh = shift_defaults_cached(d['shift'])
            rows.append({
                'key': f"{emp.id}:{d['date'].isoformat()}",
                'employee_id': emp.id,
                'name': emp.name,
                'device_id': emp.device_user_id,
                'department': dept.name if dept else '',
                'date': d['date'].isoformat(),
                'date_label': d['date'].strftime('%a, %d %b'),
                'issue': kind,
                'current_in': hhmm(d['in_time']) if d['in_time'] else '',
                'shift': sh,
            })
            if len(rows) >= QUEUE_LIMIT:
                return rows, True
    return rows, False


_shift_cache = {}


def shift_defaults_cached(shift):
    key = tuple(shift)
    if key not in _shift_cache:
        if len(_shift_cache) > 500:
            _shift_cache.clear()
        _shift_cache[key] = shift_defaults(shift)
    return _shift_cache[key]


def fix_rows(employees_by_id, rows, user):
    """
    কুইউ থেকে সেভ। rows: [{'employee_id', 'date', 'in', 'out', 'status', 'remark'}]
    রিটার্ন: counts + সফল হওয়া key গুলো + ভুল সারির বার্তা
    """
    from datetime import date as _date

    parsed, errors = [], {}
    for r in rows:
        key = f"{r.get('employee_id')}:{r.get('date')}"
        try:
            emp = employees_by_id[int(r.get('employee_id'))]
            day = _date.fromisoformat(str(r.get('date')))
            in_t, out_t = parse_time(r.get('in')), parse_time(r.get('out'))
            entry_type = r.get('status') or 'present'
            if entry_type not in ENTRY_TYPES:
                raise ValueError("ভুল স্ট্যাটাস")
            if in_t is None:
                raise ValueError("In সময় দিন")
            if out_t is None and entry_type != 'half_day':
                raise ValueError("Out সময় দিন")
        except (KeyError, TypeError, ValueError) as e:
            errors[key] = str(e) if isinstance(e, ValueError) else "এই কর্মচারী/তারিখ পাওয়া যায়নি"
            continue
        parsed.append((key, emp, day, in_t, out_t, entry_type, str(r.get('remark') or '').strip()))

    saved, already = [], []
    if parsed:
        start = min(p[2] for p in parsed)
        end = max(p[2] for p in parsed)
        emps = list({p[1].id: p[1] for p in parsed}.values())
        current = {}
        for emp, _base, days in att_rules.build_attendance(emps, start, end):
            for d in days:
                current[(emp.id, d['date'])] = d

        punches, delete_ids = [], []
        for key, emp, day, in_t, out_t, entry_type, remark in parsed:
            d = current.get((emp.id, day))
            if d is None or d.get('is_elsewhere'):
                errors[key] = "এই তারিখ এই কর্মচারীর হিসাবে নেই"
                continue
            if d['punch_count'] >= 2:
                already.append(key)          # অন্য কেউ/মেশিন এর মধ্যেই ঠিক করেছে — ওভাররাইট নয়
                continue
            in_dt, out_dt = day_times(day, d['shift'], entry_type, in_t, out_t)
            if out_dt <= in_dt:
                errors[key] = "Out সময় In এর পরে হতে হবে"
                continue
            keep_in = (d['punch_count'] == 1 and d['in_time'] is not None and hhmm(d['in_time']) == hhmm(in_t))
            if not keep_in:
                delete_ids.extend(d['punch_ids'])
                punches.append((emp, in_dt, 'In', entry_type, remark))
            punches.append((emp, out_dt, 'Out', entry_type, remark))
            saved.append(key)
        if punches:
            upsert_punches(punches, user, delete_ids=delete_ids)
    return {'saved': saved, 'already_fixed': already, 'errors': errors}
