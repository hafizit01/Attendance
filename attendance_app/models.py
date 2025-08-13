from django.db import models
from django.contrib.auth.models import User

# সপ্তাহের দিনের জন্য অপশন টুপল
WEEKDAYS = [
    ('Saturday', 'Saturday'),
    ('Sunday', 'Sunday'),
    ('Monday', 'Monday'),
    ('Tuesday', 'Tuesday'),
    ('Wednesday', 'Wednesday'),
    ('Thursday', 'Thursday'),
    ('Friday', 'Friday'),
]

class Company(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    def __str__(self):
        return f"{self.user.username} Profile"

    @property
    def company_name(self):
        return self.company.name if self.company else ''



class Department(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='departments',
        null=True,
        blank=True
    )
    name = models.CharField(max_length=100)
    weekly_off_day = models.CharField(
        max_length=10,
        choices=WEEKDAYS,
        default='Friday'
    )

    device_ip = models.GenericIPAddressField(blank=True, null=True)
    device_port = models.IntegerField(blank=True, null=True)

    in_time = models.TimeField(default='10:30', help_text="Office start time")
    out_time = models.TimeField(default='20:30', help_text="Office end time")


    def __str__(self):
        return f"{self.name} ({self.company.name if self.company else 'No Company'})"


class Employee(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='employees',
        null=True,
        blank=True
    )
    name = models.CharField(max_length=100)
    device_user_id = models.IntegerField()
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='employees'
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        unique_together = ('company', 'device_user_id')  # একই কোম্পানিতে device_user_id ইউনিক হবে

    def __str__(self):
        return f"{self.name} ({self.company.name if self.company else 'No Company'})"


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('In', 'In'),
        ('Out', 'Out')
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='attendances',
        null=True,
        blank=True
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='attendances'
    )
    timestamp = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)

    def __str__(self):
        return f"{self.employee.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.status}"


class LeaveRequest(models.Model):
    LEAVE_TYPES = [
        ('Casual', 'Casual Leave'),
        ('Sick', 'Sick Leave'),
        ('Earned', 'Earned Leave'),
        ('Unpaid', 'Unpaid Leave'),
        ('Other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='leave_requests',
        null=True,
        blank=True
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='leave_requests'
    )
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Pending')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    applied_on = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type} ({self.start_date} to {self.end_date})"


class Holiday(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='holidays',
        null=True,
        blank=True
    )
    title = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} ({self.start_date} to {self.end_date})"

    class Meta:
        ordering = ['start_date']
