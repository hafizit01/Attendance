from datetime import timedelta, datetime, date
from collections import defaultdict
from django.db.models import Min, Max, Prefetch
from attendance_app.models import Attendance, LeaveRequest, Holiday, Employee

# ---------------------------------------------------------
# 1. Optimized Attendance Table Generator (Fast)
# ---------------------------------------------------------
def generate_attendance_table(employee_qs, start_date_str, end_date_str):
    """
    N+1 Query সমস্যা সমাধান করে দ্রুত অ্যাটেন্ডেন্স টেবিল তৈরি করে।
    """
    # Date conversion
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    # ১. একবারে সব ডাটা নিয়ে আসা (Database Optimization)
    # -----------------------------------------------------
    
    # সব অ্যাটেন্ডেন্স
    all_attendance = Attendance.objects.filter(
        employee__in=employee_qs,
        timestamp__date__range=(start_date, end_date)
    ).values('employee_id', 'timestamp', 'status')

    # সব অ্যাপ্রুভড লিভ
    all_leaves = LeaveRequest.objects.filter(
        employee__in=employee_qs,
        status='Approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    ).values('employee_id', 'start_date', 'end_date')

    # সব পাবলিক হলিডে (কোম্পানি অনুযায়ী ফিল্টার করা উচিত, এখানে সাধারণ রাখা হলো)
    # ধরে নিচ্ছি employee_qs এর সবার কোম্পানি সেম
    company = employee_qs.first().company if employee_qs.exists() else None
    all_holidays = Holiday.objects.filter(
        company=company,
        start_date__lte=end_date,
        end_date__gte=start_date
    ).values('start_date', 'end_date')

    # ২. ডাটা প্রসেসিং (Python Dictionary তে সাজানো)
    # -----------------------------------------------------
    
    # Attendance Map: { (emp_id, date): [timestamps...] }
    att_map = defaultdict(list)
    for att in all_attendance:
        d = att['timestamp'].date()
        att_map[(att['employee_id'], d)].append(att['timestamp'])

    # Leave Map: { (emp_id, date): True }
    leave_map = set()
    for lv in all_leaves:
        s, e = lv['start_date'], lv['end_date']
        # Date range expand করা
        curr = max(s, start_date)
        end_limit = min(e, end_date)
        while curr <= end_limit:
            leave_map.add((lv['employee_id'], curr))
            curr += timedelta(days=1)

    # Holiday Set: { date }
    holiday_map = set()
    for h in all_holidays:
        s, e = h['start_date'], h['end_date']
        curr = max(s, start_date)
        end_limit = min(e, end_date)
        while curr <= end_limit:
            holiday_map.add(curr)
            curr += timedelta(days=1)

    # ৩. ফাইনাল লিস্ট তৈরি করা
    # -----------------------------------------------------
    days = []
    current_date = start_date
    
    while current_date <= end_date:
        weekday = current_date.strftime('%A')
        
        for emp in employee_qs:
            emp_id = emp.id
            is_weekly_off = (weekday == emp.department.weekly_off_day) if emp.department else False
            
            # ডাটা বের করা
            timestamps = att_map.get((emp_id, current_date), [])
            in_time = min(timestamps) if timestamps else None
            out_time = max(timestamps) if timestamps else None
            
            # Status Logic Hierarchy
            if timestamps:
                status = 'Present'
                # যদি মাত্র একটা পাঞ্চ থাকে
                if len(timestamps) == 1:
                    out_time = None # অথবা same as in_time রাখতে পারেন
            elif (emp_id, current_date) in leave_map:
                status = 'Leave'
            elif current_date in holiday_map:
                status = 'Public Holiday'
            elif is_weekly_off:
                status = 'Weekly Off'
            else:
                status = 'Absent'

            days.append({
                'employee': emp,
                'date': current_date,
                'in_time': in_time,
                'out_time': out_time,
                'status': status,
            })
            
        current_date += timedelta(days=1)

    return days


# ---------------------------------------------------------
# 2. Optimized Attendance Summary (Corrected Logic)
# ---------------------------------------------------------
def get_attendance_summary(employee, start_date, end_date):
    """
    Weekly Off এবং Holiday সহ পূর্ণাঙ্গ সামারি।
    """
    # ১. অ্যাটেন্ডেন্স রেকর্ডস
    records = Attendance.objects.filter(
        employee=employee, 
        timestamp__date__range=(start_date, end_date)
    ).order_by('timestamp')
    
    summary = defaultdict(lambda: {
        'in_time': None, 'out_time': None, 'status': 'Absent', 'records': []
    })

    for record in records:
        d = record.timestamp.date()
        summary[d]['records'].append(record.timestamp)
    
    # ২. Present সেট করা
    for d, data in summary.items():
        if data['records']:
            data['in_time'] = min(data['records'])
            if len(data['records']) > 1:
                data['out_time'] = max(data['records'])
            data['status'] = 'Present'

    # ৩. লিভ, হলিডে এবং উইকলি অফ লোড করা
    # -----------------------------------
    
    # Approved Leaves
    approved_leaves = LeaveRequest.objects.filter(
        employee=employee, status='Approved',
        start_date__lte=end_date, end_date__gte=start_date
    )
    leave_dates = set()
    for leave in approved_leaves:
        curr = max(leave.start_date, start_date)
        end_limit = min(leave.end_date, end_date)
        while curr <= end_limit:
            leave_dates.add(curr)
            curr += timedelta(days=1)

    # Public Holidays
    holidays = Holiday.objects.filter(
        company=employee.company,
        start_date__lte=end_date, end_date__gte=start_date
    )
    holiday_dates = set()
    for h in holidays:
        curr = max(h.start_date, start_date)
        end_limit = min(h.end_date, end_date)
        while curr <= end_limit:
            holiday_dates.add(curr)
            curr += timedelta(days=1)

    # Weekly Off Day
    weekly_off_day = employee.department.weekly_off_day if employee.department else 'Friday'

    # ৪. লুপ চালিয়ে গ্যাপ পূরণ করা
    # -----------------------------------
    current = start_date
    while current <= end_date:
        if current not in summary: # যদি প্রেজেন্ট না থাকে
            status = 'Absent'
            
            if current in leave_dates:
                status = 'Leave'
            elif current in holiday_dates:
                status = 'Holiday'
            elif current.strftime('%A') == weekly_off_day:
                status = 'Weekly Off'
            
            summary[current]['status'] = status
            
        current += timedelta(days=1)

    # ৫. স্ট্যাটাস কাউন্ট এবং ডিউরেশন
    # -----------------------------------
    summary = dict(sorted(summary.items())) # তারিখ অনুযায়ী সর্ট

    leave_count = sum(1 for d in summary.values() if d['status'] == 'Leave')
    absent_count = sum(1 for d in summary.values() if d['status'] == 'Absent')
    # চাইলে holiday_count ও বের করতে পারেন

    total_work_duration = timedelta()
    for data in summary.values():
        if data['in_time'] and data['out_time']:
            total_work_duration += (data['out_time'] - data['in_time'])

    return summary, leave_count, absent_count, approved_leaves.count(), total_work_duration


def format_timedelta_custom(td):
    if not td: return "00:00:00"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"