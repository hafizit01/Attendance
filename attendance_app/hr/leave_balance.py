"""
ছুটির ব্যালান্স ও সীমা।
  • ছুটির ধরন: LeaveType (বিল্ট-ইন + কোম্পানির নিজের) — প্রতিটা Paid কিনা, বছরের মোট সীমায় গোনা হবে কিনা
  • বছরের মোট সীমা: LeaveAllocation(leave_type=TOTAL) — যেমন সব মিলিয়ে ১২ দিন (শুধু "গোনা হয়" এমন ধরন)
  • কোনো ধরনের আলাদা সীমা: LeaveAllocation(leave_type=<কোড>) — ঐচ্ছিক
  • কর্মচারীর নিজস্ব সীমা (employee সেট) কোম্পানির সীমার বদলে প্রযোজ্য
  • ব্যবহার: ওই ক্যালেন্ডার বছরের Approved ছুটি (দুই বছরে পড়লে ভাগ করে) — approve করলে কমে, reject/মুছলে ফেরে
  • সীমা পার হলে আটকায় না — সতর্ক করে; অপারেটর নিশ্চিত করলে সেভ হয়, আর "কত দিন বেশি" দেখায়
দিন গোনা: শুরু থেকে শেষ তারিখ পর্যন্ত সব দিন (Leave summary রিপোর্টের মতোই)।
"""
from collections import defaultdict
from datetime import date

from attendance_app.models import TOTAL_LEAVE, LeaveAllocation, LeaveRequest, LeaveType, leave_type_label

STATUS_APPROVED = 'Approved'
STATUS_PENDING = 'Pending'
TOTAL = TOTAL_LEAVE


def days_in_year(start, end, year):
    s = max(start, date(year, 1, 1))
    e = min(end, date(year, 12, 31))
    return (e - s).days + 1 if e >= s else 0


def types_for(company, active_only=False):
    """[LeaveType] — কোম্পানির ধরন (প্রথমবার বিল্ট-ইন তৈরি হয়)"""
    return LeaveType.for_company(company, active_only=active_only)


def type_rules(company):
    """{code: LeaveType}"""
    return {t.code: t for t in types_for(company)}


def counted_codes(company):
    return {t.code for t in types_for(company) if t.counts_in_limit}


def unpaid_codes(company_id):
    """বেতন কাটা হবে এমন ছুটির কোড। কোম্পানির ধরন এখনো তৈরি না হলে ডিফল্ট (শুধু Unpaid)"""
    rows = list(LeaveType.objects.filter(company_id=company_id).values_list('code', 'is_paid', 'is_builtin'))
    if not any(b for _c, _p, b in rows):
        rows += [(c, p, True) for c, p, _n in LeaveType.DEFAULTS if c not in {r[0] for r in rows}]
    return {c for c, paid, _b in rows if not paid}


def allocations_for(employee):
    """{leave_type বা TOTAL: days_per_year} — কর্মচারীর নিজস্ব সীমা কোম্পানির সীমার উপরে"""
    out = dict(LeaveAllocation.objects.filter(company_id=employee.company_id, employee__isnull=True)
               .values_list('leave_type', 'days_per_year'))
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


def _cell(allocated, used, pending):
    return {'allocated': allocated, 'used': used, 'pending': pending,
            'remaining': (allocated - used) if allocated is not None else None,
            'over': max(used - allocated, 0) if allocated is not None else 0}


def total_balance(employee, year, exclude_id=None):
    """বছরের মোট সীমা: {allocated, used, pending, remaining, over} — সীমা না থাকলে allocated None"""
    alloc = allocations_for(employee).get(TOTAL)
    counted = counted_codes(employee.company)
    used = used_days(employee, year, exclude_id=exclude_id)
    pending = used_days(employee, year, statuses=(STATUS_PENDING,), exclude_id=exclude_id)
    return _cell(alloc, sum(v for k, v in used.items() if k in counted),
                 sum(v for k, v in pending.items() if k in counted))


def balance(employee, year, exclude_id=None):
    """
    [{type, label, allocated, used, pending, remaining, limited, paid, counts}] — সীমাওয়ালা ধরন,
    তারপর সীমাহীন যেগুলো ব্যবহার হয়েছে
    """
    alloc = allocations_for(employee)
    used = used_days(employee, year, exclude_id=exclude_id)
    pending = used_days(employee, year, statuses=(STATUS_PENDING,), exclude_id=exclude_id)
    rows = []
    for t in types_for(employee.company):
        lt = t.code
        base = {'type': lt, 'label': t.label, 'paid': t.is_paid, 'counts': t.counts_in_limit}
        if lt in alloc:
            rows.append({**base, **_cell(alloc[lt], used.get(lt, 0), pending.get(lt, 0)), 'limited': True})
        elif used.get(lt) or pending.get(lt):
            rows.append({**base, **_cell(None, used.get(lt, 0), pending.get(lt, 0)), 'limited': False})
    return rows


def company_balances(employees, year):
    """
    অনেক কর্মচারীর ব্যালান্স কয়েকটা কোয়েরিতে: ({employee_id: {code: cell}}, [code ...])
    কলাম: আগে বছরের মোট (সীমা থাকলে), তারপর যেসব ধরনের আলাদা সীমা আছে
    """
    employees = list(employees)
    if not employees:
        return {}, []
    company = employees[0].company
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

    counted = counted_codes(company)
    has = lambda code: any(code in company_alloc[c] for c in company_ids) or any(code in emp_alloc[e.id] for e in employees)
    types = ([TOTAL] if has(TOTAL) else []) + [t.code for t in types_for(company) if has(t.code)]
    out = {}
    for emp in employees:
        alloc = {**company_alloc[emp.company_id], **emp_alloc[emp.id]}
        out[emp.id] = {}
        for lt in types:
            if lt == TOTAL:
                u = sum(v for k, v in used[emp.id].items() if k in counted)
                p = sum(v for k, v in pending[emp.id].items() if k in counted)
            else:
                u, p = used[emp.id][lt], pending[emp.id][lt]
            out[emp.id][lt] = _cell(alloc.get(lt), u, p)
    return out, types


def labels_for(company):
    labels = {t.code: t.label for t in types_for(company)}
    labels[TOTAL] = leave_type_label(TOTAL)
    return labels


# ---------------------------------------------------------
# আবেদনের চেক (সতর্কবার্তা — আটকায় না)
# ---------------------------------------------------------
def check_request(employee, leave_type, start, end, exclude_id=None):
    """
    সীমা পার হচ্ছে কিনা। রিটার্ন: [{'message', 'over', 'year', 'scope'}] — খালি = সব ঠিক।
    Approved + Pending দুটোই ধরা হয় (Pending গুলো approve হলে যা দাঁড়াবে)।
    """
    if not (employee and leave_type and start and end) or end < start:
        return []
    rules = type_rules(employee.company)
    t = rules.get(leave_type)
    label = t.label if t else leave_type_label(leave_type)
    alloc = allocations_for(employee)
    warnings = []
    for year in range(start.year, end.year + 1):
        want = days_in_year(start, end, year)
        if not want:
            continue
        taken = used_days(employee, year, statuses=(STATUS_APPROVED, STATUS_PENDING), exclude_id=exclude_id)
        # এই ধরনের নিজস্ব সীমা
        if leave_type in alloc:
            w = _over(f"{label}", alloc[leave_type], taken.get(leave_type, 0), want, employee, year)
            if w:
                warnings.append({**w, 'scope': 'type'})
        # বছরের মোট সীমা (শুধু গোনা হয় এমন ধরন)
        if TOTAL in alloc and (t is None or t.counts_in_limit):
            counted = {c for c, r in rules.items() if r.counts_in_limit}
            w = _over("মোট ছুটি", alloc[TOTAL], sum(v for k, v in taken.items() if k in counted), want, employee, year)
            if w:
                warnings.append({**w, 'scope': 'total'})
    return warnings


def _over(what, limit, taken, want, employee, year):
    remaining = limit - taken
    if want <= remaining:
        return None
    over = want - max(remaining, 0)
    if remaining <= 0:
        msg = (f"{employee.name} এর {year} সালের {what}-এর সীমা শেষ (বছরে {limit} দিন, {taken} দিন নেওয়া হয়ে গেছে)। "
               f"এই আবেদনের {want} দিনই সীমার অতিরিক্ত।")
    else:
        msg = (f"{employee.name} এর {year} সালে {what} বাকি আছে মাত্র {remaining} দিন (বছরে {limit}), "
               f"কিন্তু {want} দিনের আবেদন — {over} দিন সীমার অতিরিক্ত।")
    return {'message': msg, 'over': over, 'year': year, 'limit': limit, 'taken': taken, 'want': want}


def over_days(warnings):
    """একাধিক সতর্কতা (ধরন + মোট) থাকলে যেটায় বেশি দিন অতিরিক্ত"""
    by_year = defaultdict(int)
    for w in warnings:
        by_year[w['year']] = max(by_year[w['year']], w['over'])
    return sum(by_year.values())


def validate_request(employee, leave_type, start, end, exclude_id=None):
    """পুরনো ইন্টারফেস: সীমা পার হলে প্রথম বার্তা, না হলে None"""
    w = check_request(employee, leave_type, start, end, exclude_id=exclude_id)
    return w[0]['message'] if w else None


# ---------------------------------------------------------
# তালিকায় "কত দিন সীমার বেশি"
# ---------------------------------------------------------
def over_limit_map(leaves):
    """
    {leave_id: অতিরিক্ত দিন} — প্রতিটা কর্মচারীর বছরের ছুটি তারিখ অনুযায়ী সাজিয়ে (আগে Approved, পরে Pending)
    জমতে জমতে যেখানে সীমা পার হয়েছে। সীমা না থাকলে বা Rejected হলে ০।
    """
    leaves = [lv for lv in leaves if lv.status in (STATUS_APPROVED, STATUS_PENDING)]
    if not leaves:
        return {}
    emp_ids = {lv.employee_id for lv in leaves}
    years = {y for lv in leaves for y in range(lv.start_date.year, lv.end_date.year + 1)}
    company_ids = {lv.employee.company_id for lv in leaves}

    company_alloc = defaultdict(dict)
    emp_alloc = defaultdict(dict)
    for a in LeaveAllocation.objects.filter(company_id__in=company_ids).values('company_id', 'employee_id', 'leave_type', 'days_per_year'):
        (emp_alloc[a['employee_id']] if a['employee_id'] else company_alloc[a['company_id']])[a['leave_type']] = a['days_per_year']
    if not company_alloc and not emp_alloc:
        return {}
    counted = {cid: set(LeaveType.objects.filter(company_id=cid, counts_in_limit=True).values_list('code', flat=True))
               for cid in company_ids}
    for cid in company_ids:
        if not LeaveType.objects.filter(company_id=cid, is_builtin=True).exists():
            counted[cid] |= {c for c, _p, n in LeaveType.DEFAULTS if n}

    all_leaves = list(LeaveRequest.objects.filter(
        employee_id__in=emp_ids, status__in=(STATUS_APPROVED, STATUS_PENDING),
        start_date__lte=date(max(years), 12, 31), end_date__gte=date(min(years), 1, 1))
        .values('id', 'employee_id', 'employee__company_id', 'leave_type', 'status', 'start_date', 'end_date'))
    all_leaves.sort(key=lambda r: (r['status'] != STATUS_APPROVED, r['start_date'], r['id']))

    out = defaultdict(int)
    for year in years:
        cum_type = defaultdict(int)
        cum_total = defaultdict(int)
        for r in all_leaves:
            d = days_in_year(r['start_date'], r['end_date'], year)
            if not d:
                continue
            emp, cid, lt = r['employee_id'], r['employee__company_id'], r['leave_type']
            alloc = {**company_alloc[cid], **emp_alloc[emp]}
            over = 0
            if lt in alloc:
                before = cum_type[(emp, lt)]
                cum_type[(emp, lt)] += d
                over = max(over, max(cum_type[(emp, lt)] - alloc[lt], 0) - max(before - alloc[lt], 0))
            if TOTAL in alloc and lt in counted[cid]:
                before = cum_total[emp]
                cum_total[emp] += d
                over = max(over, max(cum_total[emp] - alloc[TOTAL], 0) - max(before - alloc[TOTAL], 0))
            out[r['id']] += over
    return {k: v for k, v in out.items() if v}
