from django import forms
from django.core.exceptions import ValidationError
from .models import *

# employees/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import Employee
from subscription_app.utils_limit import get_employee_limit_for


def _manager_branch(user):
    """ব্রাঞ্চ ম্যানেজার হলে তার ব্রাঞ্চ (ফর্মের ড্রপডাউন সীমিত করার জন্য), নইলে None"""
    from .scoping import manager_branch_of
    return manager_branch_of(user) if user is not None else None


def _dept_label(dept):
    return f"{dept.branch.name} · {dept.name}" if dept.branch_id else dept.name


def _user_company(user):
    """user -> profile -> company (না থাকলে None)"""
    return getattr(getattr(user, 'profile', None), 'company', None)


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['name', 'device_user_id', 'department']  # ← company বাদ
        widgets = {
            'name': forms.TextInput(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
            'device_user_id': forms.NumberInput(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
            'department': forms.Select(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)  # view থেকে user পাস করা হবে (admin এ পাস হয় না)
        super().__init__(*args, **kwargs)
        if user is not None:
            company = _user_company(user)
            # শুধু user-এর company এর ডিপার্টমেন্ট দেখাবে; অন্য কোম্পানির id POST করলে invalid হবে
            qs = Department.objects.filter(company=company).select_related('branch') if company else Department.objects.none()
            branch = _manager_branch(user)
            if branch is not None:
                qs = qs.filter(branch=branch)   # ম্যানেজার শুধু নিজের ব্রাঞ্চের ডিপার্টমেন্টে
            self.fields['department'].queryset = qs.order_by('branch__name', 'name')
            self.fields['department'].label_from_instance = _dept_label

    def _has_is_active(self):
        return any(f.name == "is_active" for f in Employee._meta.get_fields())

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get('department')
        if not department or not getattr(department, "company", None):
            raise ValidationError(_("Please select a department that belongs to a company."))

        company = department.company

        # limit check
        limit = get_employee_limit_for(company)
        qs = Employee.objects.filter(company=company)
        if self._has_is_active():
            qs = qs.filter(is_active=True)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        current = qs.count()
        if limit is not None and current >= limit:
            raise ValidationError(
            _("❌ Cannot add employee — limit reached %(current)d/%(limit)d.")
            % {"current": current, "limit": limit}
        )



        # device ID ব্রাঞ্চের ভেতরে ইউনিক (প্রতিটা ব্রাঞ্চের মেশিন আলাদা)
        device_user_id = cleaned.get('device_user_id')
        if device_user_id is not None:
            dup = Employee.id_scope(department).filter(device_user_id=device_user_id)
            if self.instance and self.instance.pk:
                dup = dup.exclude(pk=self.instance.pk)
            dup = dup.first()
            if dup:
                where = department.branch.name if department.branch_id else company.name
                self.add_error('device_user_id', _(f"Device User ID {device_user_id} already belongs to {dup.name} in {where}."))

        # save() এ ব্যবহার করার জন্য stash করে রাখলাম
        self._resolved_company = company
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        # company auto-set from department
        if hasattr(self, "_resolved_company"):
            obj.company = self._resolved_company
        else:
            # সেফটি নেট: department থেকে সেট করো
            if obj.department and getattr(obj.department, "company", None):
                obj.company = obj.department.company
        if commit:
            obj.save()
        return obj




from django import forms
from .models import Department

class DepartmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # ডিভাইসের IP/Port শুধু superuser বদলাতে পারে। disabled ফিল্ডে Django POST এর মান উপেক্ষা করে
        # আগের মান রাখে — HTML disabled এর মতো সেভ করলে খালি হয়ে যায় না
        if user is not None and not user.is_superuser:
            for name in ("device_ip", "device_port"):
                self.fields[name].disabled = True

        # ব্রাঞ্চ: মালিক কোম্পানির যেকোনো ব্রাঞ্চ বাছতে পারে; ম্যানেজারের জন্য নিজের ব্রাঞ্চে লক
        company = _user_company(user) if user is not None else None
        branch_field = self.fields['branch']
        branch_field.required = True
        branch_field.empty_label = "Select a branch"
        branch_field.queryset = Branch.objects.filter(company=company) if company else Branch.objects.none()
        manager_branch = _manager_branch(user)
        if manager_branch is not None:
            branch_field.queryset = Branch.objects.filter(pk=manager_branch.pk)
            branch_field.initial = manager_branch
            branch_field.disabled = True
        elif not self.instance.pk and branch_field.queryset.count() == 1:
            branch_field.initial = branch_field.queryset.first()

    class Meta:
        model = Department
        # company ভিউ থেকে user এর কোম্পানি বসায়; ফর্মে রাখলে সব কোম্পানির নাম ড্রপডাউনে দেখা যেত
        fields = ["branch", "name", "weekly_off_day", "in_time", "out_time", "device_ip", "device_port"]
        widgets = {
            "company": forms.Select(attrs={
                "class": "w-full border rounded px-3 py-2",
            }),
            "name": forms.TextInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Department name",
            }),
            "weekly_off_day": forms.Select(attrs={
                "class": "w-full border rounded px-3 py-2",
            }),
            "device_ip": forms.TextInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Device IP (optional)",
            }),
            "device_port": forms.NumberInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Device Port (optional)",
            }),
            "in_time": forms.TimeInput(attrs={
                "type": "time",
                "class": "w-full border rounded px-3 py-2",
            }, format="%H:%M"),
            "out_time": forms.TimeInput(attrs={
                "type": "time",
                "class": "w-full border rounded px-3 py-2",
            }, format="%H:%M"),
        }



class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ['employee', 'timestamp', 'status']
        widgets = {
            "employee": forms.Select(attrs={"class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
            "timestamp": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
            "status": forms.Select(attrs={"class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self._company = _user_company(user) if user is not None else None
        if user is not None:
            # GET ও POST দুই ক্ষেত্রেই শুধু user-এর company এর employees
            qs = Employee.objects.filter(company=self._company) if self._company else Employee.objects.none()
            branch = _manager_branch(user)
            if branch is not None:
                qs = qs.filter(department__branch=branch)
            self.fields['employee'].queryset = qs

    def clean_employee(self):
        employee = self.cleaned_data.get('employee')
        # queryset এর বাইরের (অন্য কোম্পানি/ব্রাঞ্চ) id হলে ModelChoiceField নিজেই invalid করে
        if employee and self._company and employee.company_id != self._company.id:
            raise ValidationError("এই কর্মচারী আপনার কোম্পানির নয়।")
        return employee

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get('employee')
        timestamp = cleaned_data.get('timestamp')
        status = cleaned_data.get('status')

        if employee and timestamp and status:
            date = timestamp.date()

            # আজকের দিনের সকল attendance status বের করি
            attendance_qs = Attendance.objects.filter(
                employee=employee,
                timestamp__date=date
            )

            # যদি edit mode হয়, তাহলে নিজের টা বাদ দিয়ে হিসাব করব
            if self.instance.pk:
                attendance_qs = attendance_qs.exclude(pk=self.instance.pk)

            statuses = attendance_qs.values_list('status', flat=True)

            # Check for duplicate In or Out
            if 'In' in statuses and status == 'In':
                raise ValidationError("আজকের জন্য In টাইম ইতিমধ্যেই যুক্ত হয়েছে।")

            if 'Out' in statuses and status == 'Out':
                raise ValidationError("আজকের জন্য Out টাইম ইতিমধ্যেই যুক্ত হয়েছে।")

            # Limit to max 2 entries
            if attendance_qs.count() >= 2:
                raise ValidationError("এই কর্মচারীর আজকের জন্য In এবং Out টাইম ইতিমধ্যেই আছে।")

        return cleaned_data



from django import forms
from django.contrib.auth.models import User
from .models import LeaveRequest, Employee, Company

class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = '__all__'
        widgets = {
            "leave_type": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "reason": forms.Textarea(attrs={"rows": 3, "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "company": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "employee": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "status": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "approved_by": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "applied_on": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),

        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)  # view থেকে user পাস করা হবে
        super().__init__(*args, **kwargs)
        if user is not None:
            company = _user_company(user)
            if company:
                # শুধু user-এর company এর employees / company / users দেখাবে
                employees = Employee.objects.filter(company=company)
                branch = _manager_branch(user)
                if branch is not None:
                    employees = employees.filter(department__branch=branch)
                self.fields['employee'].queryset = employees
                self.fields['company'].queryset = Company.objects.filter(pk=company.pk)
                self.fields['approved_by'].queryset = User.objects.filter(profile__company=company)
            else:
                self.fields['employee'].queryset = Employee.objects.none()
                self.fields['company'].queryset = Company.objects.none()
                self.fields['approved_by'].queryset = User.objects.none()




class HolidayForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # ব্রাঞ্চ খালি = সব ব্রাঞ্চের ছুটি (শুধু মালিক); ম্যানেজার শুধু নিজের ব্রাঞ্চের ছুটি দিতে পারে
        company = _user_company(user) if user is not None else None
        field = self.fields['branch']
        field.queryset = Branch.objects.filter(company=company) if company else Branch.objects.none()
        field.empty_label = "All branches"
        field.label = "Applies to"
        manager_branch = _manager_branch(user)
        if manager_branch is not None:
            field.queryset = Branch.objects.filter(pk=manager_branch.pk)
            field.initial = manager_branch
            field.required = True
            field.disabled = True

    class Meta:
        model = Holiday
        fields = ['title', 'branch', 'start_date', 'end_date', 'description']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500',
                'placeholder': 'Holiday title'
            }),
            'start_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500',
                'placeholder': 'Optional description'
            }),
        }



class DayAttendanceForm(forms.Form):
    in_time = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    out_time = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))

    def clean(self):
        data = super().clean()
        in_t = data.get('in_time')
        out_t = data.get('out_time')
        if in_t and out_t and out_t <= in_t:
            raise forms.ValidationError("Out time must be after In time.")
        return data