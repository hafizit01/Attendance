# attendance_app/branch_forms.py — মাল্টি-ব্রাঞ্চের ফর্ম
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Branch, Department, Employee


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ['name', 'address', 'phone', 'is_active']
        labels = {'is_active': 'Active branch'}
        help_texts = {'is_active': 'বন্ধ করলে ব্রাঞ্চের তথ্য থাকবে, শুধু নতুন কাজে দেখাবে না।'}
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = Branch.objects.filter(company=self.company, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("এই নামে একটা ব্রাঞ্চ আগে থেকেই আছে।")
        return name


class ManagerForm(forms.Form):
    """ব্রাঞ্চ ম্যানেজারের লগইন: শুধু নিজের ব্রাঞ্চের ডাটা দেখবে"""
    full_name = forms.CharField(max_length=150, label="Full name")
    username = forms.CharField(max_length=150, help_text="লগইনের সময় এটা লিখবে।")
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput, help_text="কমপক্ষে ৮ অক্ষর; শুধু সংখ্যা নয়।")

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("এই username আগে থেকেই আছে।")
        return username

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if password:
            user = User(username=cleaned.get('username') or '', email=cleaned.get('email') or '',
                        first_name=cleaned.get('full_name') or '')
            try:
                validate_password(password, user)
            except forms.ValidationError as e:
                self.add_error('password', e)
        return cleaned


class TransferForm(forms.Form):
    to_department = forms.ModelChoiceField(queryset=Department.objects.none(), label="New branch & department")
    effective_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}),
                                     help_text="এই তারিখ থেকে নতুন ব্রাঞ্চে কাজ শুরু। আগের দিনগুলোর রিপোর্ট পুরনো ব্রাঞ্চেই থাকবে।")
    new_device_user_id = forms.IntegerField(
        required=False, min_value=0, label="ID on the new branch's machine",
        help_text="নতুন ব্রাঞ্চের মেশিনে এই কর্মচারীর ID আলাদা হলে দিন। ফাঁকা রাখলে আগের ID থাকবে।")
    note = forms.CharField(max_length=255, required=False, label="Note (optional)")

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        qs = (Department.objects.filter(company=employee.company)
              .select_related('branch').order_by('branch__name', 'name'))
        if employee.department_id:
            qs = qs.exclude(pk=employee.department_id)
        self.fields['to_department'].queryset = qs
        self.fields['to_department'].label_from_instance = (
            lambda d: f"{d.branch.name} · {d.name}" if d.branch_id else d.name)

    def clean_effective_date(self):
        d = self.cleaned_data['effective_date']
        last = self.employee.transfers.order_by('-effective_date').first()
        if last and d < last.effective_date:
            raise forms.ValidationError(
                f"আগের বদলি {last.effective_date:%d %b %Y} তারিখে কার্যকর — এর আগের তারিখ দেওয়া যাবে না।")
        return d

    def clean(self):
        cleaned = super().clean()
        dept = cleaned.get('to_department')
        if dept:
            uid = cleaned.get('new_device_user_id')
            if uid is None:
                uid = self.employee.device_user_id
            # নতুন ব্রাঞ্চের মেশিনে এই ID অন্য কারো হলে পাঞ্চ ভুল মানুষের নামে যাবে
            dup = Employee.id_scope(dept).exclude(pk=self.employee.pk).filter(device_user_id=uid).first()
            if dup:
                where = dept.branch.name if dept.branch_id else dept.name
                self.add_error('new_device_user_id',
                               f"ID {uid} {where} এ {dup.name} এর — নতুন মেশিনে এই কর্মচারীর ID টা দিন।")
        return cleaned
