# attendance_app/document_views.py — কর্মচারীর প্রোফাইল ও ডকুমেন্ট (NID, CV, সার্টিফিকেট ...)
# ফাইল গোপন স্টোরেজে; খোলা/নামানো/মোছা — শুধু ঐ কোম্পানির ইউজার (ব্রাঞ্চ ম্যানেজার হলে নিজের ব্রাঞ্চের কর্মচারী)।
import mimetypes
import os
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .models import Employee, EmployeeDocument

MAX_FILE_MB = 10
MAX_FILES_PER_UPLOAD = 10
ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'webp', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt'}
INLINE_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'webp', 'gif'}     # ব্রাউজারে খোলা যায় — বাকিগুলো সবসময় ডাউনলোড
EXPIRY_WARN_DAYS = 30


def _employee(request, employee_id):
    scope = scoping.get_scope(request).security
    if scope.company is None:
        raise Http404
    return get_object_or_404(scoping.employees(Employee.objects.select_related('department', 'department__branch'), scope),
                             pk=employee_id)


def _document(request, pk):
    scope = scoping.get_scope(request).security
    if scope.company is None:
        raise Http404
    qs = EmployeeDocument.objects.select_related('employee').filter(company=scope.company)
    if scope.branch:
        qs = qs.filter(employee__department__branch=scope.branch)
    return get_object_or_404(qs, pk=pk)


def _legacy_files(employee):
    """আগের প্রোফাইলের ফাইল (Resume / Offer / Joining letter / ছবি) — একই তালিকায় দেখাতে"""
    profile = getattr(employee, 'profile', None)
    out = []
    if profile is None:
        return out
    for field, label in (('image', 'Photo (profile)'), ('resume', 'CV / Resume (profile)'),
                         ('offer_letter', 'Offer letter (profile)'), ('joining_letter', 'Joining letter (profile)')):
        f = getattr(profile, field, None)
        if f:
            try:
                url = f.url
            except ValueError:
                continue
            out.append({'label': label, 'name': os.path.basename(f.name), 'url': url})
    return out


@login_required
def employee_documents(request, employee_id):
    employee = _employee(request, employee_id)

    if request.method == 'POST':
        files = request.FILES.getlist('files')
        doc_type = request.POST.get('doc_type') or 'other'
        if doc_type not in dict(EmployeeDocument.DOC_TYPES):
            doc_type = 'other'
        title = (request.POST.get('title') or '').strip()[:120]
        note = (request.POST.get('note') or '').strip()[:255]
        expiry = request.POST.get('expiry_date') or None
        errors = []
        if not files:
            errors.append("কোনো ফাইল বাছাই করা হয়নি।")
        if len(files) > MAX_FILES_PER_UPLOAD:
            errors.append(f"এক বারে সর্বোচ্চ {MAX_FILES_PER_UPLOAD}টা ফাইল।")
        if expiry:
            from django.utils.dateparse import parse_date
            expiry = parse_date(expiry)
            if expiry is None:
                errors.append("মেয়াদের তারিখ ঠিক নয়।")
        for f in files:
            ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
            if ext not in ALLOWED_EXT:
                errors.append(f"{f.name}: এই ধরনের ফাইল চলবে না (PDF, ছবি, Word, Excel দিন)।")
            elif f.size > MAX_FILE_MB * 1024 * 1024:
                errors.append(f"{f.name}: {MAX_FILE_MB} MB এর বেশি।")
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('attendance_app:employee_documents', employee_id=employee.id)

        for f in files:
            EmployeeDocument.objects.create(
                company=employee.company, employee=employee, doc_type=doc_type,
                title=title if len(files) == 1 else '', file=f, original_name=f.name[:255], size=f.size,
                expiry_date=expiry, note=note, uploaded_by=request.user)
        messages.success(request, f"{len(files)}টা ডকুমেন্ট যোগ হয়েছে।")
        return redirect('attendance_app:employee_documents', employee_id=employee.id)

    docs = list(employee.documents.select_related('uploaded_by'))
    today = timezone.localdate()
    expired = [d for d in docs if d.expiry_date and d.expiry_date < today]
    expiring = [d for d in docs if d.expiry_date and today <= d.expiry_date <= today + timedelta(days=EXPIRY_WARN_DAYS)]
    groups = {}
    for code, label in EmployeeDocument.DOC_TYPES:     # NID, Photo, CV ... ক্রমে
        items = [d for d in docs if d.doc_type == code]
        if items:
            groups[label] = items
    have = {d.doc_type for d in docs}
    checklist = [(code, label, code in have) for code, label in EmployeeDocument.DOC_TYPES
                 if code in ('nid', 'photo', 'cv', 'certificate', 'appointment')]
    return render(request, 'employees/documents.html', {
        'employee': employee,
        'profile': getattr(employee, 'profile', None) if hasattr(employee, 'profile') else None,
        'docs': docs, 'groups': groups, 'legacy': _legacy_files(employee),
        'expired': expired, 'expiring': expiring, 'checklist': checklist,
        'doc_types': EmployeeDocument.DOC_TYPES, 'max_mb': MAX_FILE_MB,
        'accept': ','.join('.' + e for e in sorted(ALLOWED_EXT)),
        'total_size': sum(d.size for d in docs),
    })


@login_required
def document_file(request, pk):
    """ডকুমেন্ট খোলা (PDF/ছবি ব্রাউজারে) বা ?download=1 দিলে নামানো"""
    doc = _document(request, pk)
    try:
        fh = doc.file.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404("ফাইল পাওয়া যায়নি।")
    name = doc.original_name or os.path.basename(doc.file.name)
    inline = doc.extension in INLINE_EXT and not request.GET.get('download')
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    resp = FileResponse(fh, content_type=ctype, as_attachment=not inline, filename=name)
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'private, no-store'
    return resp


@login_required
@require_POST
def document_delete(request, pk):
    doc = _document(request, pk)
    emp_id = doc.employee_id
    name = doc.display_name
    try:
        doc.file.delete(save=False)
    except OSError:
        # ফাইল এই মুহূর্তে খোলা (কেউ ডাউনলোড করছে) — রেকর্ড রেখে দিই, যাতে গোপন ফাইল হারিয়ে পড়ে না থাকে
        messages.error(request, f"“{name}” এখন মোছা গেল না (ফাইলটা খোলা আছে)। একটু পরে আবার চেষ্টা করুন।")
        return redirect('attendance_app:employee_documents', employee_id=emp_id)
    doc.delete()
    messages.success(request, f"“{name}” মুছে ফেলা হয়েছে।")
    return redirect('attendance_app:employee_documents', employee_id=emp_id)
