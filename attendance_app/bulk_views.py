# attendance_app/bulk_views.py — Bulk & Official Duty হাজিরা, Missing / Pending quick-fix কুইউ
# সব কোয়েরি কোম্পানি (request.user.profile.company) + ব্রাঞ্চ ম্যানেজারের ব্রাঞ্চ দিয়ে সীমিত।
import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .hr import bulk_entry, missing_punch
from .models import Attendance, Department, Employee

STATUS_CHOICES = Attendance.ENTRY_TYPES


def _employees(scope):
    return (scoping.employees(Employee.objects.filter(is_active=True), scope)
            .select_related('department', 'department__branch', 'department__default_shift')
            .order_by('department__name', 'name'))


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


def _err(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


def _parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------
# Feature A
# ---------------------------------------------------------
@login_required
def bulk_attendance(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    emps = list(_employees(scope))
    today = timezone.localdate()
    people = []
    for e in emps:
        sh = bulk_entry.shift_defaults_cached(bulk_entry.att_rules.shift_for(e))
        people.append({'id': e.id, 'name': e.name, 'device_id': e.device_user_id,
                       'dept_id': e.department_id, 'dept': e.department.name if e.department_id else '—',
                       'shift': f"{sh['name']} {sh['in']}–{sh['out']}"})
    depts = scoping.departments(Department.objects.all(), scope).order_by('name')
    return render(request, 'attendance_ops/bulk.html', {
        'people': people,
        'departments': [{'id': d.id, 'name': d.name} for d in depts],
        'status_choices': STATUS_CHOICES,
        'today': today.isoformat(),
        'active_tab': 'bulk',
    })


def _bulk_inputs(request, data):
    """POST JSON → (employees, start, end, entry_type, in_t, out_t, skip, overwrite, remarks) অথবা error"""
    scope = scoping.get_scope(request).security
    if scope.company is None:
        return None, "আপনার কোম্পানি সেট করা নেই।"
    try:
        ids = {int(i) for i in data.get('employee_ids') or []}
    except (TypeError, ValueError):
        return None, "ভুল কর্মচারী।"
    if not ids:
        return None, "অন্তত একজন কর্মচারী বাছাই করুন।"
    start, end = _parse_date(data.get('from_date')), _parse_date(data.get('to_date'))
    if not start or not end:
        return None, "From ও To তারিখ দিন।"
    if end < start:
        return None, "To তারিখ From এর আগে হতে পারে না।"
    if (end - start).days + 1 > bulk_entry.MAX_DAYS:
        return None, f"এক বারে সর্বোচ্চ {bulk_entry.MAX_DAYS} দিন।"
    entry_type = data.get('status') or 'present'
    if entry_type not in bulk_entry.ENTRY_TYPES:
        return None, "ভুল স্ট্যাটাস।"
    try:
        in_t, out_t = bulk_entry.parse_time(data.get('in_time')), bulk_entry.parse_time(data.get('out_time'))
    except ValueError as e:
        return None, str(e)
    emps = list(_employees(scope).filter(id__in=ids))
    if len(emps) != len(ids):
        return None, "কিছু কর্মচারী আপনার কোম্পানি/ব্রাঞ্চের নয়।"
    if len(emps) * ((end - start).days + 1) > bulk_entry.MAX_CELLS:
        return None, "একসাথে খুব বেশি — কর্মচারী বা তারিখ কমিয়ে দিন।"
    return {
        'employees': emps, 'start': start, 'end': end, 'entry_type': entry_type,
        'in_t': in_t, 'out_t': out_t,
        'skip_off_days': bool(data.get('skip_off_days', True)),
        'overwrite': bool(data.get('overwrite', False)),
        'remarks': str(data.get('remarks') or '').strip()[:255],
    }, None


def _summary(plan):
    return {k: plan[k] for k in ('records', 'employees', 'selected', 'days_each', 'skipped_weekly_off',
                                 'skipped_holiday', 'skipped_existing', 'skipped_leave', 'replaced', 'invalid')}


@login_required
@require_POST
def bulk_attendance_preview(request):
    data = _json_body(request)
    if data is None:
        return _err("ভুল অনুরোধ।")
    inp, err = _bulk_inputs(request, data)
    if err:
        return _err(err)
    plan = bulk_entry.plan_bulk(inp['employees'], inp['start'], inp['end'], inp['entry_type'],
                                inp['in_t'], inp['out_t'], inp['skip_off_days'], inp['overwrite'])
    return JsonResponse({'ok': True, **_summary(plan)})


@login_required
@require_POST
def bulk_attendance_apply(request):
    data = _json_body(request)
    if data is None:
        return _err("ভুল অনুরোধ।")
    inp, err = _bulk_inputs(request, data)
    if err:
        return _err(err)
    plan = bulk_entry.plan_bulk(inp['employees'], inp['start'], inp['end'], inp['entry_type'],
                                inp['in_t'], inp['out_t'], inp['skip_off_days'], inp['overwrite'])
    if not plan['records']:
        return JsonResponse({'ok': True, 'saved': 0, **_summary(plan)})
    res = bulk_entry.apply_bulk(plan, request.user, inp['entry_type'], inp['remarks'])
    company = inp['employees'][0].company
    missing_punch.resolve_closed(company, inp['start'])
    return JsonResponse({'ok': True, 'saved': plan['records'], **res, **_summary(plan)})


# ---------------------------------------------------------
# Feature B
# ---------------------------------------------------------
@login_required
def missing_queue(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    today = timezone.localdate()
    g = request.GET
    month = g.get('month', '')
    start, end = _parse_date(g.get('from')), _parse_date(g.get('to'))
    if month and not (start or end):
        try:
            y, m = (int(x) for x in month.split('-'))
            start = date(y, m, 1)
            end = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1))
        except ValueError:
            start = end = None
    if not start:
        start = today.replace(day=1) if today.day > 1 else (today - timedelta(days=1)).replace(day=1)
    if not end:
        end = today - timedelta(days=1)
    end = min(end, today)
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > bulk_entry.MAX_DAYS:
        start = end - timedelta(days=bulk_entry.MAX_DAYS - 1)

    emps = _employees(scope)
    dept_id = g.get('department') or ''
    if dept_id.isdigit():
        emps = emps.filter(department_id=int(dept_id))
    issue = g.get('issue') if g.get('issue') in ('no_punch', 'missing_out') else ''
    q = (g.get('q') or '').strip()
    if q:
        emps = emps.filter(name__icontains=q) if not q.isdigit() else emps.filter(device_user_id=int(q))

    rows, truncated = bulk_entry.missing_rows(list(emps), start, end, issue)
    counts = {'no_punch': sum(r['issue'] == 'no_punch' for r in rows),
              'missing_out': sum(r['issue'] == 'missing_out' for r in rows)}
    depts = scoping.departments(Department.objects.all(), scope).order_by('name')
    return render(request, 'attendance_ops/missing_queue.html', {
        'rows': rows, 'truncated': truncated, 'counts': counts,
        'departments': depts, 'status_choices': STATUS_CHOICES,
        'f': {'from': start.isoformat(), 'to': end.isoformat(), 'department': dept_id, 'issue': issue, 'q': q},
        'today': today.isoformat(), 'limit': bulk_entry.QUEUE_LIMIT,
        'active_tab': 'queue',
    })


@login_required
@require_POST
def missing_queue_save(request):
    scope = scoping.get_scope(request).security
    if scope.company is None:
        return _err("আপনার কোম্পানি সেট করা নেই।")
    data = _json_body(request)
    rows = (data or {}).get('rows')
    if not isinstance(rows, list) or not rows:
        return _err("কোনো সারি বাছাই করা হয়নি।")
    if len(rows) > bulk_entry.QUEUE_LIMIT:
        return _err("এক বারে খুব বেশি সারি।")
    ids = set()
    for r in rows:
        try:
            ids.add(int(r.get('employee_id')))
        except (AttributeError, TypeError, ValueError):
            pass
    emps = {e.id: e for e in _employees(scope).filter(id__in=ids)}
    res = bulk_entry.fix_rows(emps, [r for r in rows if isinstance(r, dict)], request.user)
    if res['saved']:
        first = min(_parse_date(k.split(':', 1)[1]) for k in res['saved'])
        missing_punch.resolve_closed(scope.company, first)
    return JsonResponse({'ok': True, **res})
