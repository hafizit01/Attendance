# attendance_app/portal/admin_views.py — অ্যাডমিনের দিক: কর্মচারীকে পোর্টাল লগইন দেওয়া, রিমোট পাঞ্চ যাচাই
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from attendance_app import scoping
from attendance_app.models import Attendance, Employee
from attendance_app.utils.params import date_param, int_param
from attendance_app.utils.attendance_helpers import day_range


class PortalAccessForm(forms.Form):
    username = forms.CharField(max_length=150, help_text="কর্মচারী এটা দিয়ে লগইন করবে (যেমন মোবাইল নম্বর বা ID)।")
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False,
                               help_text="কমপক্ষে ৮ অক্ষর, শুধু সংখ্যা নয়। পুরনো লগইনে খালি রাখলে পাসওয়ার্ড বদলাবে না।")

    def __init__(self, *args, existing_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_user = existing_user
        if existing_user is None:
            self.fields['password'].required = True

    def clean_username(self):
        u = self.cleaned_data['username'].strip()
        qs = User.objects.filter(username__iexact=u)
        if self.existing_user:
            qs = qs.exclude(pk=self.existing_user.pk)
        if qs.exists():
            raise forms.ValidationError("এই username আগে থেকেই ব্যবহার হচ্ছে।")
        return u

    def clean_password(self):
        p = self.cleaned_data.get('password')
        if p:
            validate_password(p, self.existing_user)
        return p


def _employee(request, pk):
    scope = scoping.get_scope(request)
    return get_object_or_404(scoping.employees(Employee.objects.select_related('user', 'department'), scope.security), pk=pk)


@login_required
def portal_access(request, pk):
    emp = _employee(request, pk)
    user = emp.user
    if user is not None and (user.is_superuser or user.is_staff or getattr(getattr(user, 'profile', None), 'company_id', None)):
        messages.error(request, "এই কর্মচারীর সাথে একটা অ্যাডমিন লগইন যুক্ত — এখান থেকে বদলানো যাবে না।")
        return redirect('attendance_app:employee_list')
    form = PortalAccessForm(request.POST or None, existing_user=user,
                            initial={'username': user.username if user else str(emp.device_user_id)})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if user is None:
                user = User(username=form.cleaned_data['username'], first_name=emp.name[:150])
                user.set_password(form.cleaned_data['password'])
                user.save()
                emp.user = user
                emp.save(update_fields=['user'])
                messages.success(request, f"{emp.name} এর পোর্টাল লগইন তৈরি হয়েছে। লগইন পেজ থেকে ঢুকে /me/ পোর্টাল দেখবে।")
            else:
                user.username = form.cleaned_data['username']
                user.is_active = True
                if form.cleaned_data['password']:
                    user.set_password(form.cleaned_data['password'])
                user.save()
                messages.success(request, f"{emp.name} এর পোর্টাল লগইন আপডেট হয়েছে।")
        return redirect('attendance_app:employee_portal_access', pk=emp.pk)
    return render(request, 'portal/admin_access.html', {'employee': emp, 'form': form, 'portal_user': user})


@login_required
@require_POST
def portal_access_revoke(request, pk):
    emp = _employee(request, pk)
    user = emp.user
    if user is None or user.is_superuser or user.is_staff or getattr(getattr(user, 'profile', None), 'company_id', None):
        return HttpResponseForbidden("এই লগইন বন্ধ করা যাবে না।")
    user.is_active = False
    user.save(update_fields=['is_active'])
    messages.warning(request, f"{emp.name} এর পোর্টাল লগইন বন্ধ করা হয়েছে (আবার চালু করতে Save দিন)।")
    return redirect('attendance_app:employee_portal_access', pk=emp.pk)


def _remote_qs(request):
    scope = scoping.get_scope(request)
    return scoping.attendance(Attendance.objects.filter(is_remote_punch=True), scope) \
        .select_related('employee', 'employee__department')


@login_required
def remote_punch_list(request):
    qs = _remote_qs(request)
    d_from, d_to = date_param(request.GET, 'date_from'), date_param(request.GET, 'date_to')
    emp_id = int_param(request.GET, 'employee')
    if d_from:
        qs = qs.filter(timestamp__gte=day_range(d_from)['timestamp__gte'])
    if d_to:
        qs = qs.filter(timestamp__lt=day_range(d_to)['timestamp__lt'])
    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    scope = scoping.get_scope(request)
    return render(request, 'portal/admin_remote_punches.html', {
        'page_obj': Paginator(qs.order_by('-timestamp'), 40).get_page(request.GET.get('page')),
        'employees': scoping.employees(Employee.objects.all(), scope).order_by('name'),
        'date_from': d_from, 'date_to': d_to, 'selected_employee': emp_id,
    })


@login_required
def remote_punch_selfie(request, pk):
    """সেলফি — শুধু ঐ কোম্পানির অ্যাডমিন (ম্যানেজার হলে নিজের ব্রাঞ্চের)। ফাইল পাবলিক /media/ এ নেই।"""
    scope = scoping.get_scope(request).security
    if scope.company is None:
        raise Http404
    att = scoping.attendance(Attendance.objects.filter(pk=pk, is_remote_punch=True), scope).first()
    if att is None or not att.selfie:
        raise Http404
    try:
        f = att.selfie.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404
    resp = FileResponse(f, content_type='image/jpeg')
    resp['Cache-Control'] = 'private, max-age=3600'
    return resp
