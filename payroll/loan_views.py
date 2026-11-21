# payroll/loan_views.py — লোন / অগ্রিম বেতন ও কিস্তি
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance_app import scoping
from attendance_app.models import Employee
from attendance_app.utils.excel import Col, Sheet, excel_response

from .models import EmployeeLoan, LoanRepayment


class LoanForm(forms.ModelForm):
    start_month = forms.DateField(input_formats=['%Y-%m', '%Y-%m-%d'],
                                  widget=forms.DateInput(attrs={'type': 'month'}, format='%Y-%m'),
                                  help_text="যে মাসের বেতন থেকে প্রথম কিস্তি কাটা হবে")

    class Meta:
        model = EmployeeLoan
        fields = ['employee', 'loan_type', 'amount', 'emi_count', 'monthly_deduction', 'start_month', 'status', 'note']
        widgets = {'loan_type': forms.RadioSelect}

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = scoping.employees(Employee.objects.all(), scope).order_by('name')
        self.fields['monthly_deduction'].required = False

    def clean(self):
        c = super().clean()
        amount, emi, monthly = c.get('amount'), c.get('emi_count'), c.get('monthly_deduction')
        if amount is not None and amount <= 0:
            self.add_error('amount', "টাকার পরিমাণ ০ এর বেশি হতে হবে।")
        if emi is not None and emi < 1:
            self.add_error('emi_count', "কমপক্ষে ১ কিস্তি।")
        if amount and emi and not monthly:
            c['monthly_deduction'] = (amount / emi).quantize(Decimal('0.01'))
        if monthly is not None and monthly <= 0:
            self.add_error('monthly_deduction', "মাসিক কিস্তি ০ এর বেশি হতে হবে।")
        return c


class RepaymentForm(forms.ModelForm):
    class Meta:
        model = LoanRepayment
        fields = ['date', 'amount', 'note']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


def _loans(request):
    scope = scoping.get_scope(request)
    qs = EmployeeLoan.objects.filter(company=scope.company).select_related('employee', 'employee__department')
    if scope.branch:
        qs = qs.filter(employee__department__branch=scope.branch)
    return scope, qs.prefetch_related('repayments')


def _can_manage(request):
    scope = scoping.get_scope(request)
    return request.user.is_superuser or scope.is_company_admin or request.user.has_perm('payroll.add_employeeloan')


@login_required
def loan_list(request):
    scope, qs = _loans(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    status = request.GET.get('status', 'active')
    if status in ('active', 'closed'):
        qs = qs.filter(status=status)
    today = timezone.localdate()
    loans = list(qs)
    for ln in loans:
        ln.this_month = ln.deduction_for(today.year, today.month)
        ln.remaining = ln.remaining_now
        ln.recovered = Decimal(ln.amount) - ln.remaining
        ln.progress = int(ln.recovered * 100 / ln.amount) if ln.amount else 0
    return render(request, 'payroll/loans/loan_list.html', {
        'loans': loans, 'status': status,
        'total_outstanding': sum((ln.remaining for ln in loans), Decimal(0)),
        'total_this_month': sum((ln.this_month for ln in loans), Decimal(0)),
        'can_manage': _can_manage(request),
    })


@login_required
def loan_form(request, pk=None):
    if not _can_manage(request):
        return HttpResponseForbidden("লোন যোগ/এডিট করার অনুমতি নেই।")
    scope, qs = _loans(request)
    loan = get_object_or_404(qs, pk=pk) if pk else None
    initial = {'start_month': timezone.localdate().replace(day=1)} if not loan else None
    form = LoanForm(request.POST or None, instance=loan, scope=scope, initial=initial)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.company = scope.company
        if not obj.pk:
            obj.created_by = request.user
        obj.save()
        messages.success(request, f"{obj.employee.name}: {obj.get_loan_type_display()} ৳{obj.amount:,.0f} সেভ হয়েছে — "
                                  f"{obj.start_month:%b %Y} থেকে মাসে ৳{obj.monthly_deduction:,.0f} কাটা হবে।")
        return redirect('payroll:loan_detail', pk=obj.pk)
    return render(request, 'payroll/loans/loan_form.html', {'form': form, 'loan': loan,
                                                            'title': 'Edit loan' if loan else 'New loan / advance'})


@login_required
def loan_detail(request, pk):
    _scope, qs = _loans(request)
    loan = get_object_or_404(qs, pk=pk)
    today = timezone.localdate().replace(day=1)
    return render(request, 'payroll/loans/loan_detail.html', {
        'loan': loan, 'schedule': loan.schedule(), 'today_month': today,
        'repayments': loan.repayments.all(), 'repay_form': RepaymentForm(initial={'date': timezone.localdate()}),
        'remaining': loan.remaining_now, 'can_manage': _can_manage(request),
    })


@login_required
@require_POST
def loan_repay(request, pk):
    if not _can_manage(request):
        return HttpResponseForbidden("অনুমতি নেই।")
    _scope, qs = _loans(request)
    loan = get_object_or_404(qs, pk=pk)
    form = RepaymentForm(request.POST)
    if form.is_valid() and form.cleaned_data['amount'] > 0:
        r = form.save(commit=False)
        r.loan, r.created_by = loan, request.user
        r.save()
        messages.success(request, f"হাতে ফেরত ৳{r.amount:,.0f} যোগ হয়েছে — বাকি কিস্তি সেই অনুযায়ী কমবে।")
    else:
        messages.error(request, "সঠিক তারিখ ও টাকার পরিমাণ দিন।")
    return redirect('payroll:loan_detail', pk=loan.pk)


@login_required
@require_POST
def loan_delete(request, pk):
    if not _can_manage(request):
        return HttpResponseForbidden("অনুমতি নেই।")
    _scope, qs = _loans(request)
    loan = get_object_or_404(qs, pk=pk)
    loan.delete()
    messages.warning(request, "লোন মুছে ফেলা হয়েছে — আগের মাসগুলোর বেতনেও আর কিস্তি দেখাবে না। "
                              "শুধু ভবিষ্যতের কাটা বন্ধ করতে চাইলে Closed করুন।")
    return redirect('payroll:loan_list')


@login_required
def loan_excel(request):
    scope, qs = _loans(request)
    today = timezone.localdate()
    rows = []
    for ln in qs:
        rem = ln.remaining_now
        rows.append({'name': ln.employee.name, 'dept': ln.employee.department.name if ln.employee.department_id else '',
                     'type': ln.get_loan_type_display(), 'amount': ln.amount, 'emi': ln.emi_count,
                     'monthly': ln.monthly_deduction, 'start': ln.start_month, 'end': ln.end_month,
                     'recovered': Decimal(ln.amount) - rem, 'remaining': rem,
                     'this_month': ln.deduction_for(today.year, today.month), 'status': ln.get_status_display(),
                     'note': ln.note})
    cols = [Col('Employee', 'name', width=24), Col('Department', 'dept'), Col('Type', 'type'),
            Col('Amount', 'amount', 'money'), Col('EMIs', 'emi', 'int'), Col('Monthly EMI', 'monthly', 'money'),
            Col('Start month', 'start', 'date'), Col('Last EMI', 'end', 'date'), Col('Recovered', 'recovered', 'money'),
            Col('Outstanding', 'remaining', 'money'), Col(f"Deduction {today:%b %Y}", 'this_month', 'money'),
            Col('Status', 'status'), Col('Note', 'note', width=30)]
    totals = {k: sum((r[k] for r in rows), Decimal(0)) for k in ('amount', 'recovered', 'remaining', 'this_month')}
    return excel_response(f"Loans_{today}", [Sheet('Loans', cols, rows, title='Loans & advances',
                                                    meta=[('Company', scope.company.name)], totals=totals)])
