from django.contrib import admin
from .models import Company, Department, Employee, Attendance, LeaveRequest, Holiday, UserProfile

# employees/admin.py
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from .models import Employee
from .forms import EmployeeForm
from .services import create_employee_with_limit


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    search_fields = ['name'] 

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'company']
    search_fields = ['user__username', 'company__name']

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'weekly_off_day', 'last_synced_at']
    list_filter = ['company']
    search_fields = ['name']  # ← এটা যোগ করা জরুরি
    readonly_fields = ['push_token', 'last_synced_at']  # ডিভাইসে push_token বসাতে এখান থেকে কপি করুন



@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeForm
    list_display = ['name', 'company', 'device_user_id', 'department', 'is_active']
    list_filter = ['company', 'department', 'is_active']
    search_fields = ['name', 'device_user_id', 'user__username', 'user__email']
    list_select_related = ['company', 'department', 'user']
    autocomplete_fields = ['department', 'user']  # ← company বাদ
    ordering = ['company__name', 'name']

    def _owner_company(self, request):
        # যদি Company.owner ফিল্ড থাকে
        try:
            return Company.objects.get(owner=request.user)
        except Company.DoesNotExist:
            return None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        comp = self._owner_company(request)
        return qs.filter(company=comp) if comp else qs.none()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            comp = self._owner_company(request)
            if 'department' in form.base_fields and comp:
                form.base_fields['department'].queryset = Department.objects.filter(company=comp)
        return form

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)

        # company form.save() এ সেট হয়; তবু সেফটি:
        if not obj.company and obj.department and getattr(obj.department, "company", None):
            obj.company = obj.department.company

        company = obj.company or (obj.department.company if obj.department else None)
        if company is None:
            self.message_user(request, "Company is required (via Department).", level=messages.ERROR)
            raise ValidationError("Company is required.")

        try:
            with transaction.atomic():
                emp = create_employee_with_limit(
                    company=company,
                    name=obj.name,
                    device_user_id=obj.device_user_id,
                    department=obj.department,
                    user=getattr(obj, "user", None),
                )
                obj.pk = emp.pk
        except ValidationError as e:
            self.message_user(request, "; ".join(e.messages), level=messages.ERROR)
            raise
        except IntegrityError:
            self.message_user(request, "This device_user_id already exists in this branch.", level=messages.ERROR)
            raise



@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'status', 'approved_by']
    list_filter = ['status', 'leave_type']
    search_fields = ['employee__name']



@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'company', 'timestamp', 'status')
    list_filter = ('company', 'status')
    search_fields = ('employee__name', 'employee__device_user_id')
    ordering = ('-timestamp',)



admin.site.register(Holiday)


from .models import (Device, DeviceCommand, HRSettings, MissingPunch, Notification, PunchLog,  # noqa: E402
                     Shift, ShiftAssignment)


from .models import LeaveAllocation  # noqa: E402


from .models import MessageLog, UnknownPunch  # noqa: E402


@admin.register(UnknownPunch)
class UnknownPunchAdmin(admin.ModelAdmin):
    list_display = ['raw_id', 'reason', 'timestamp', 'device', 'status', 'company']
    list_filter = ['company', 'reason', 'status']


@admin.register(MessageLog)
class MessageLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'kind', 'channel', 'to', 'status', 'provider', 'company']
    list_filter = ['company', 'kind', 'status', 'channel']
    search_fields = ['to', 'text']


@admin.register(LeaveAllocation)
class LeaveAllocationAdmin(admin.ModelAdmin):
    list_display = ['company', 'employee', 'leave_type', 'days_per_year']
    list_filter = ['company', 'leave_type']
    raw_id_fields = ['employee']


@admin.register(HRSettings)
class HRSettingsAdmin(admin.ModelAdmin):
    list_display = ['company', 'enable_late_deduction', 'lates_equal_one_day_deduction',
                    'enable_missing_punch_alert', 'enable_gps_attendance', 'updated_at']


@admin.register(MissingPunch)
class MissingPunchAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'in_time', 'status', 'company']
    list_filter = ['company', 'status']
    date_hierarchy = 'date'
    raw_id_fields = ['employee']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'title', 'kind', 'is_read', 'created_at']
    list_filter = ['kind', 'is_read']


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'department', 'protocol', 'serial_number', 'ip', 'is_active', 'last_seen_at']
    list_filter = ['company', 'protocol', 'is_active']
    search_fields = ['name', 'serial_number', 'ip']
    readonly_fields = ['push_token', 'last_seen_at', 'last_synced_at', 'last_error', 'last_ip_seen', 'firmware',
                       'model_name', 'user_count', 'finger_count', 'face_count', 'record_count',
                       'adms_att_stamp', 'adms_op_stamp']


@admin.register(DeviceCommand)
class DeviceCommandAdmin(admin.ModelAdmin):
    list_display = ['device', 'label', 'status', 'return_code', 'created_at', 'done_at']
    list_filter = ['status']


@admin.register(PunchLog)
class PunchLogAdmin(admin.ModelAdmin):
    list_display = ['device_user_id', 'employee', 'timestamp', 'method', 'source', 'device', 'company']
    list_filter = ['company', 'method', 'source']
    search_fields = ['employee__name', 'device_user_id']
    date_hierarchy = 'timestamp'
    raw_id_fields = ['employee', 'device', 'department', 'branch']


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'branch', 'start_time', 'end_time', 'grace_minutes', 'break_minutes', 'is_active']
    list_filter = ['company', 'is_active']


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ['employee', 'shift', 'start_date', 'end_date']
    list_filter = ['shift']
    raw_id_fields = ['employee']

