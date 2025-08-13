from django.contrib import admin
from .models import Company, Department, Employee, Attendance, LeaveRequest, Holiday, UserProfile

admin.site.register(Company)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'company']
    search_fields = ['user__username', 'company__name']

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'weekly_off_day']
    list_filter = ['company']

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'device_user_id', 'department']
    list_filter = ['company', 'department']
    search_fields = ['name']

admin.site.register(Attendance)

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'status', 'approved_by']
    list_filter = ['status', 'leave_type']
    search_fields = ['employee__name']

admin.site.register(Holiday)
