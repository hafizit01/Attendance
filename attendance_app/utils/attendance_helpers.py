# attendance_helpers.py

from datetime import timedelta, datetime
from django.db.models import Min, Max
from attendance_app.models import Attendance

def generate_attendance_table(employee_qs, start_date, end_date):
    days = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    delta = timedelta(days=1)

    while start <= end:
        current_date = start.date()
        for emp in employee_qs:
            is_holiday = current_date.strftime('%A') == emp.department.weekly_off_day

            attendance_qs = Attendance.objects.filter(
                employee=emp,
                timestamp__date=current_date
            )

            if attendance_qs.exists():
                in_time = attendance_qs.aggregate(Min('timestamp'))['timestamp__min']
                out_time = attendance_qs.aggregate(Max('timestamp'))['timestamp__max']
                status = 'Present'
            elif is_holiday:
                in_time = None
                out_time = None
                status = 'Holiday'
            else:
                in_time = None
                out_time = None
                status = 'Absent'

            days.append({
                'employee': emp,
                'date': current_date,
                'in_time': in_time,
                'out_time': out_time,
                'status': status,
            })
        start += delta
    return days


# attendance_app/utils/attendance_helpers.py

from datetime import timedelta, datetime
from collections import defaultdict
from attendance_app.models import Attendance, LeaveRequest
from django.utils.timezone import make_aware

def get_attendance_summary(employee, start_date, end_date):
    # Load attendance records
    records = Attendance.objects.filter(employee=employee, timestamp__date__range=(start_date, end_date)).order_by('timestamp')
    
    # Prepare summary dict
    summary = defaultdict(lambda: {
        'in_time': None,
        'out_time': None,
        'status': 'Absent',
        'records': []
    })

    for record in records:
        date = record.timestamp.date()
        summary[date]['records'].append(record.timestamp)
    
    for date, data in summary.items():
        if data['records']:
            data['in_time'] = min(data['records'])
            data['out_time'] = max(data['records'])
            data['status'] = 'Present'

    # Approved leaves
    approved_leaves = LeaveRequest.objects.filter(
        employee=employee,
        status='Approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    approved_leave_dates = set()
    for leave in approved_leaves:
        leave_start = max(leave.start_date, start_date)
        leave_end = min(leave.end_date, end_date)
        for i in range((leave_end - leave_start).days + 1):
            approved_leave_dates.add(leave_start + timedelta(days=i))

    # Add absent days or approved leaves
    current = start_date
    while current <= end_date:
        if current not in summary:
            if current in approved_leave_dates:
                summary[current]['status'] = 'Leave'
            else:
                summary[current]['status'] = 'Absent'
        current += timedelta(days=1)

    # Sort by date
    summary = dict(sorted(summary.items()))

    # Count leave & absent
    approved_leave_count = sum(1 for date, data in summary.items() if data['status'] == 'Leave')
    absent_days = sum(1 for date, data in summary.items() if data['status'] == 'Absent')

    # Total work duration
    total_work_duration = timedelta()
    for date, data in summary.items():
        if data['in_time'] and data['out_time']:
            total_work_duration += (data['out_time'] - data['in_time'])

    return summary, approved_leave_count, absent_days, approved_leaves.count(), total_work_duration


def format_timedelta_custom(td):
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"
