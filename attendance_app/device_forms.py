# attendance_app/device_forms.py — হাজিরা মেশিনের ফর্ম
from django import forms

from . import scoping
from .devices.fileimport import MAX_BYTES
from .models import Department, Device


def _dept_label(dept):
    return f"{dept.branch.name} · {dept.name}" if dept.branch_id else dept.name


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ['name', 'department', 'protocol', 'serial_number', 'ip', 'port', 'comm_key',
                  'username', 'password', 'is_active']
        labels = {'is_active': 'Active', 'protocol': 'Machine type / connection'}
        help_texts = {'is_active': 'বন্ধ করলে মেশিনের ডাটা নেওয়া হবে না।'}
        widgets = {
            'password': forms.PasswordInput(render_value=True, attrs={'autocomplete': 'new-password'}),
            'protocol': forms.RadioSelect,
        }

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = scoping.departments(Department.objects.select_related('branch'), scope) if scope else Department.objects.none()
        self.fields['department'].queryset = qs.order_by('branch__name', 'name')
        self.fields['department'].label_from_instance = _dept_label
        self.fields['department'].empty_label = "Select department"
        self.fields['serial_number'].required = False
        if not self.instance.pk and qs.count() == 1:
            self.fields['department'].initial = qs.first()

    def clean_serial_number(self):
        sn = (self.cleaned_data.get('serial_number') or '').strip()
        return sn or None

    def clean(self):
        cleaned = super().clean()
        p = cleaned.get('protocol')
        if p in (Device.PROTOCOL_ZK_PULL, Device.PROTOCOL_HIK_ISAPI) and not cleaned.get('ip'):
            self.add_error('ip', "LAN কানেকশনের জন্য মেশিনের IP দিন।")
        if p == Device.PROTOCOL_ZK_ADMS and not cleaned.get('serial_number'):
            self.add_error('serial_number', "Cloud push (ADMS) মেশিন সিরিয়াল নম্বর দিয়ে চেনা হয় — SN দিন।")
        if p in (Device.PROTOCOL_ZK_PULL,) and not cleaned.get('port'):
            cleaned['port'] = 4370
        if p in (Device.PROTOCOL_HIK_ISAPI, Device.PROTOCOL_HIK_PUSH) and cleaned.get('ip') and not cleaned.get('port'):
            cleaned['port'] = 80
        if p == Device.PROTOCOL_HIK_ISAPI and not cleaned.get('password'):
            self.add_error('password', "Hikvision মেশিনের লগইন পাসওয়ার্ড দিন।")
        return cleaned


class PunchImportForm(forms.Form):
    file = forms.FileField(
        label="Attendance file",
        help_text="USB থেকে নেওয়া attlog.dat / .txt, অথবা CSV (ID, তারিখ-সময়)। সর্বোচ্চ 20 MB।",
        widget=forms.ClearableFileInput(attrs={'accept': '.dat,.txt,.csv,.tsv'}),
    )
    create_employees = forms.BooleanField(
        required=False, initial=False, label="অচেনা ID হলে নতুন কর্মচারী বানাও",
        help_text="বন্ধ থাকলে অচেনা ID এর পাঞ্চ শুধু লগে থাকবে (পরে কর্মচারী যোগ করলে হাজিরায় যোগ হবে না, আবার ইমপোর্ট করতে হবে)।",
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > MAX_BYTES:
            raise forms.ValidationError("ফাইল 20 MB এর বেশি।")
        return f
