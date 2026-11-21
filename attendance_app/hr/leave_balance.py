"""
ছুটির ব্যালান্স।
  • বছরের সীমা: LeaveAllocation (কোম্পানির সবার জন্য, বা নির্দিষ্ট কর্মচারীর জন্য আলাদা)
  • ব্যবহার: ওই ক্যালেন্ডার বছরের Approved ছুটির দিন (ছুটি দুই বছরে পড়লে ভাগ করে)
    — approve করলেই ব্যালান্স থেকে কমে; reject/মুছলে ফিরে আসে (আলাদা করে কিছু রাখতে হয় না)
  • যে ধরনের allocation নেই তার কোনো সীমা নেই (আগের মতো)
দিন গোনা: শুরু থেকে শেষ তারিখ পর্যন্ত সব দিন (Leave summary রিপোর্টের মতোই)।
"""
from collections import defaultdict
from datetime import date

from attendance_app.models import LeaveAllocation, LeaveRequest

STATUS_APPROVED = 'Approved'
STATUS_PENDING = 'Pending'


def days_in_year(start, end, year):
    s = max(start, date(year, 1, 1))
    e = min(end, date(year, 12, 31))
    return (e - s).days + 1 if e >= s else 0


def allocations_for(employee):
    """{leave_type: days_per_year} — কর্মচারীর নিজস্ব সীমা কোম্পানির সীমার উপরে"""
    out = {}
    rows = LeaveAllocation.objects.filter(company_id=employee.company_id).filter(
        employee__isnull=True).values_list('leave_type', 'days_per_year')
    out.update(dict(rows))
    out.update(dict(LeaveAllocation.objects.filter(employee=employee).values_list('leave_type', 'days_per_year')))
    return out


def used_days(employee, year, statuses=(STATUS_APPROVED,), exclude_id=None):
    """{leave_type: days} ওই বছরে"""
    qs = LeaveRequest.objects.filter(employee=employee, status__in=statuses,
                                     start_date__lte=date(year, 12, 31), end_date__gte=date(year, 1, 1))
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    out = defaultdict(int)
    for lt, s, e in qs.values_list('leave_type', 'start_date', 'end_date'):
        out[lt] += days_in_year(s, e, year)
    return out


def balance(employee, year):
    """
    [{type, label, allocated, used, pending, remaining}] — শুধু যেসব ধরনের সীমা আছে, তারপর সীমাহীন যেগুলো ব্যবহার হয়েছে
    """
    alloc = allocations_for(employee)
    used = used_days(employee, year)
    pending = used_days(employee, year, statuses=(STATUS_PENDING,))
    rows = []
    for lt, label in LeaveRequest.LEAVE_TYPES:
        if lt in alloc:
            rows.append({'type': lt, 'label': label, 'allocated': alloc[lt], 'used': used.get(lt, 0),
                         'pending': pending.get(lt, 0), 'remaining': alloc[lt] - used.get(lt, 0), 'limited': True})
        elif used.get(lt) or pending.get(lt):
            rows.append({'type': lt, 'label': label, 'allocated': None, 'used': used.get(lt, 0),
                         'pending': pending.get(lt, 0), 'remaining': None, 'limited': False})
    return rows


def company_balances(employees, year):
    """অনেক কর্মচারীর ব্যালান্স কয়েকটা কোয়েরিতে: {employee_id: {type: {...}}}, এবং সীমাওয়ালা ধরনগুলো"""
    employees = list(employees)
    if not employees:
        return {}, []
    company_ids = {e.company_id for e in employees}
    company_alloc = defaultdict(dict)
    emp_alloc = defaultdict(dict)
    for a in LeaveAllocation.objects.filter(company_id__in=company_ids).values('company_id', 'employee_id', 'leave_type', 'days_per_year'):
        if a['employee_id']:
            emp_alloc[a['employee_id']][a['leave_type']] = a['days_per_year']
        else:
            company_alloc[a['company_id']][a['leave_type']] = a['days_per_year']
    used = defaultdict(lambda: defaultdict(int))
    pending = defaultdict(lambda: defaultdict(int))
    qs = LeaveRequest.objects.filter(employee__in=employees, status__in=(STATUS_APPROVED, STATUS_PENDING),
                                     start_date__lte=date(year, 12, 31), end_date__gte=date(year, 1, 1))
    for emp_id, lt, st, s, e in qs.values_list('employee_id', 'leave_type', 'status', 'start_date', 'end_date'):
        (used if st == STATUS_APPROVED else pending)[emp_id][lt] += days_in_year(s, e, year)

    types = [lt for lt, _l in LeaveRequest.LEAVE_TYPES
             if any(lt in company_alloc[c] for c in company_ids) or any(lt in emp_alloc[e.id] for e in employees)]
    out = {}
    for emp in employees:
        alloc = {**company_alloc[emp.company_id], **emp_alloc[emp.id]}
        out[emp.id] = {}
        for lt in types:
            a = alloc.get(lt)
            u = used[emp.id][lt]
            out[emp.id][lt] = {'allocated': a, 'used': u, 'pending': pending[emp.id][lt],
                               'remaining': (a - u) if a is not None else None}
    return out, types


def validate_request(employee, leave_type, start, end, exclude_id=None):
    """
    আবেদন সম্ভব কিনা। সমস্যা হলে বাংলা মেসেজ, না হলে None।
    প্রতি বছরের জন্য আলাদা চেক (ডিসেম্বর-জানুয়ারি জুড়ে ছুটি হলে)।
    """
    if not (employee and leave_type and start and end) or end < start:
        return None
    alloc = allocations_for(employee).get(leave_type)
    if alloc is None:
        return None   # সীমা নেই
    label = dict(LeaveRequest.LEAVE_TYPES).get(leave_type, leave_type)
    for year in range(start.year, end.year + 1):
        want = days_in_year(start, end, year)
        if not want:
            continue
        remaining = alloc - used_days(employee, year, exclude_id=exclude_id).get(leave_type, 0)
        if remaining <= 0:
            return f"{employee.name} এর {year} সালের {label} ব্যালান্স শেষ (বছরে {alloc} দিন, সব ব্যবহার হয়েছে)।"
        if want > remaining:
            return (f"{employee.name} এর {year} সালে {label} বাকি আছে মাত্র {remaining} দিন, "
                    f"কিন্তু {want} দিনের আবেদন করা হয়েছে।")
    return None
