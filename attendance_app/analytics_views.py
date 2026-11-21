# attendance_app/analytics_views.py — হাজিরার ট্রেন্ড বিশ্লেষণ (Django ORM + pandas, কোনো বাইরের AI API নয়)
import calendar
import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe

from . import scoping
from .models import Department, Employee, MessageLog
from .utils import attendance_helpers as att_rules
from .utils.params import int_param, month_param

WEEKDAYS = ['Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def attendance_frame(employees, start, end, branch_id=None):
    """build_attendance → pandas DataFrame (প্রতি কর্মচারী প্রতি দিন এক সারি) — রিপোর্টের একই নিয়ম"""
    import pandas as pd
    rows = []
    for emp, _shift, days in att_rules.build_attendance(employees, start, end, branch_id=branch_id):
        dept = emp.department.name if emp.department_id else '—'
        for d in days:
            if d['is_elsewhere']:
                continue
            working = not (d['is_holiday'] or d['is_off_day'])
            rows.append({
                'emp_id': emp.id, 'name': emp.name, 'dept': dept, 'date': d['date'], 'weekday': d['weekday'],
                'status': d['status'], 'working': working,
                'scheduled': working and d['status'] in ('Present', 'Absent'),   # ছুটি নেওয়া দিন বাদ
                'absent': d['status'] == 'Absent',
                'late': working and d['late'].total_seconds() > 0,
                'late_min': d['late'].total_seconds() / 60 if working else 0.0,
            })
    return pd.DataFrame(rows)


def compute_insights(df):
    """DataFrame → চার্ট ও টেবিলের ডাটা + লেখায় অন্তর্দৃষ্টি"""
    if df.empty:
        return None
    # ডাটা না থাকা দিন: কোনো ডিপার্টমেন্টে (৩+ জন) সেদিন কেউই পাঞ্চ করেনি — সাধারণত মেশিনের ডাটা সিঙ্ক হয়নি
    # বা অফিস বন্ধ ছিল। এগুলো হিসাবে ধরলে "সবাই অনুপস্থিত" দেখায়, তাই আলাদা করে দেখাই ও বাদ দিই।
    dd = df[df['scheduled']].groupby(['dept', 'date']).agg(n=('scheduled', 'sum'), absent=('absent', 'sum')).reset_index()
    gaps = dd[(dd['n'] >= 3) & (dd['absent'] == dd['n'])]
    gap_keys = set(zip(gaps['dept'], gaps['date']))
    if gap_keys:
        df = df[[(d, t) not in gap_keys for d, t in zip(df['dept'], df['date'])]]
    data_gaps = [{'dept': r.dept, 'date': r.date.strftime('%d %b'), 'people': int(r.n)}
                 for r in gaps.sort_values('date').itertuples()]
    sched = df[df['scheduled']]
    total_sched = int(sched.shape[0])
    total_absent = int(sched['absent'].sum())
    present = df[df['status'] == 'Present']
    absence_rate = (100 * total_absent / total_sched) if total_sched else 0.0

    # ১) সবচেয়ে বেশি দেরি করা ৫ জন
    late = (df[df['late']].groupby(['emp_id', 'name', 'dept'])
            .agg(late_days=('late', 'sum'), late_min=('late_min', 'sum'), avg_min=('late_min', 'mean'))
            .reset_index().sort_values(['late_days', 'late_min'], ascending=False))
    top_late = [{'name': r.name, 'dept': r.dept, 'late_days': int(r.late_days), 'late_min': int(round(r.late_min)),
                 'avg_min': int(round(r.avg_min))} for r in late.head(5).itertuples()]
    team_avg_late = float(late['late_days'].sum() / df['emp_id'].nunique()) if df['emp_id'].nunique() else 0.0

    # ২) ডিপার্টমেন্ট অনুযায়ী অনুপস্থিতির হার
    dep = (sched.groupby('dept').agg(scheduled=('scheduled', 'sum'), absent=('absent', 'sum'),
                                     people=('emp_id', 'nunique')).reset_index())
    dep['rate'] = (100 * dep['absent'] / dep['scheduled']).round(1)
    dep = dep.sort_values('rate', ascending=False)
    dept_rates = [{'dept': r.dept, 'rate': float(r.rate), 'absent': int(r.absent), 'scheduled': int(r.scheduled),
                   'people': int(r.people)} for r in dep.itertuples()]

    # ৩) সবচেয়ে বেশি অনুপস্থিতির দিন + প্রতিদিনের হার
    day = (sched.groupby(['date', 'weekday']).agg(scheduled=('scheduled', 'sum'), absent=('absent', 'sum'))
           .reset_index().sort_values('date'))
    day['rate'] = (100 * day['absent'] / day['scheduled']).round(1)
    daily = [{'date': r.date.strftime('%d %b'), 'iso': r.date.isoformat(), 'weekday': r.weekday, 'rate': float(r.rate),
              'absent': int(r.absent), 'scheduled': int(r.scheduled)} for r in day.itertuples()]
    worst_days = sorted(daily, key=lambda x: (x['absent'], x['rate']), reverse=True)[:5]

    # ৪) সপ্তাহের কোন দিনে অনুপস্থিতি বেশি
    wk = sched.groupby('weekday').agg(scheduled=('scheduled', 'sum'), absent=('absent', 'sum'))
    weekday = []
    for w in WEEKDAYS:
        if w in wk.index and wk.loc[w, 'scheduled']:
            weekday.append({'weekday': w[:3], 'rate': round(float(100 * wk.loc[w, 'absent'] / wk.loc[w, 'scheduled']), 1)})

    # লেখায় অন্তর্দৃষ্টি (নিয়মভিত্তিক)
    notes = []
    if top_late:
        t = top_late[0]
        mult = (t['late_days'] / team_avg_late) if team_avg_late else 0
        notes.append(f"{t['name']} এই মাসে {t['late_days']} দিন দেরি করেছেন (গড়ে {t['avg_min']} মিনিট)"
                     + (f" — দলের গড়ের {mult:.1f} গুণ।" if mult >= 1.5 else "।"))
    if len(dept_rates) > 1 and dept_rates[0]['rate'] > 0:
        d0 = dept_rates[0]
        notes.append(f"{d0['dept']} এ অনুপস্থিতি সবচেয়ে বেশি: {d0['rate']}% (কোম্পানির গড় {absence_rate:.1f}%)।")
    if weekday:
        wmax = max(weekday, key=lambda x: x['rate'])
        if wmax['rate'] > 0 and absence_rate and wmax['rate'] >= absence_rate * 1.3:
            notes.append(f"{wmax['weekday']} বারে অনুপস্থিতি বেশি ({wmax['rate']}%) — সাপ্তাহিক ছুটির আগে/পরে কিনা দেখুন।")
    if worst_days and worst_days[0]['absent']:
        w = worst_days[0]
        notes.append(f"{w['date']} ({w['weekday']}) সবচেয়ে বেশি অনুপস্থিত: {w['absent']} জন ({w['rate']}%)।")
    if data_gaps:
        sample = ", ".join(f"{g['date']} ({g['dept']})" for g in data_gaps[:4])
        notes.append(f"{len(data_gaps)} টা দিনে পুরো ডিপার্টমেন্টের কোনো পাঞ্চ নেই — {sample}{' …' if len(data_gaps) > 4 else ''}। "
                     "মেশিনের ডাটা সিঙ্ক হয়নি বা অফিস বন্ধ ছিল; এই দিনগুলো হিসাব থেকে বাদ রাখা হয়েছে।")
    if not notes:
        notes.append("এই সময়ে উল্লেখযোগ্য কোনো সমস্যা দেখা যায়নি।")

    late_share = (100 * float(df['late'].sum()) / present.shape[0]) if present.shape[0] else 0.0
    return {
        'kpi': {'employees': int(df['emp_id'].nunique()), 'absence_rate': round(absence_rate, 1),
                'attendance_rate': round(100 - absence_rate, 1) if total_sched else 0,
                'late_share': round(late_share, 1),
                'avg_late': int(round(float(df.loc[df['late'], 'late_min'].mean()))) if df['late'].any() else 0,
                'absent_days': total_absent},
        'top_late': top_late, 'dept_rates': dept_rates, 'worst_days': worst_days, 'daily': daily,
        'weekday': weekday, 'notes': notes, 'data_gaps': data_gaps,
    }


@login_required
def analytics_dashboard(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    today = timezone.localdate()
    month = month_param(request.GET, 'month', today.strftime('%Y-%m'))
    y, m = map(int, month.split('-'))
    start = date(y, m, 1)
    end = min(date(y, m, calendar.monthrange(y, m)[1]), today)
    dept_id = int_param(request.GET, 'department')
    data = None
    if start <= today:
        employees = scoping.employees_for_period(
            Employee.objects.filter(is_active=True).select_related('department', 'department__branch', 'department__default_shift'),
            scope, start, end)
        if dept_id:
            employees = employees.filter(department_id=dept_id)
        data = compute_insights(attendance_frame(employees, start, end, branch_id=scope.branch_id))
    charts = {}
    if data:
        charts = {
            'topLate': {'labels': [t['name'] for t in data['top_late']], 'values': [t['late_days'] for t in data['top_late']],
                        'extra': [f"{t['late_min']} min total" for t in data['top_late']]},
            'dept': {'labels': [d['dept'] for d in data['dept_rates']], 'values': [d['rate'] for d in data['dept_rates']],
                     'extra': [f"{d['absent']} of {d['scheduled']} days" for d in data['dept_rates']]},
            'daily': {'labels': [d['date'] for d in data['daily']], 'values': [d['absent'] for d in data['daily']],
                      'extra': [f"{d['rate']}% · {d['weekday']}" for d in data['daily']]},
            'weekday': {'labels': [w['weekday'] for w in data['weekday']], 'values': [w['rate'] for w in data['weekday']]},
        }
    months = []
    cur = today.replace(day=1)
    for _ in range(12):
        months.append(cur)
        cur = (cur.replace(day=1) - timedelta(days=1)).replace(day=1)
    return render(request, 'analytics/dashboard.html', {
        'data': data, 'charts_json': mark_safe(json.dumps(charts)), 'month': month, 'months': months,
        'period': f"{start:%d %b} – {end:%d %b %Y}", 'selected_department': dept_id,
        'departments': scoping.departments(Department.objects.all(), scope),
    })


@login_required
def message_log(request):
    scope = scoping.get_scope(request)
    qs = MessageLog.objects.filter(company=scope.company)
    return render(request, 'analytics/message_log.html', {'page_obj': Paginator(qs, 50).get_page(request.GET.get('page'))})
