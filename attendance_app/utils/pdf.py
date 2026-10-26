"""
সব PDF রিপোর্ট এখান দিয়ে বানানো হয় (WeasyPrint)।

- ফন্ট প্রজেক্টের ভেতরেই (attendance_app/pdf_fonts) — আগে প্রতিটা PDF এ Google Fonts ডাউনলোড হতো, তাতে কয়েক সেকেন্ড যেত
- ইন্টারনেট/নিজের সার্ভারে কোনো রিকোয়েস্ট যায় না: শুধু ফন্ট ফোল্ডারের ফাইল পড়া যায়
- কোম্পানি, ব্রাঞ্চ, কে/কখন বানাল — সব রিপোর্টের হেডার/ফুটারের কমন তথ্য এখানেই বসে
"""
from pathlib import Path

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from attendance_app import scoping

try:
    from weasyprint import HTML
    from weasyprint.urls import default_url_fetcher
    try:
        from weasyprint.text.fonts import FontConfiguration   # WeasyPrint 53+
    except ImportError:
        from weasyprint.fonts import FontConfiguration        # পুরনো ভার্সন
except (ImportError, OSError):  # সার্ভারে WeasyPrint/GTK না থাকলে
    HTML = None

FONT_DIR = Path(__file__).resolve().parent.parent / 'pdf_fonts'
FONT_BASE_URL = FONT_DIR.as_uri() + '/'


def _local_only_fetcher(url, *args, **kwargs):
    """শুধু ফন্ট ফোল্ডারের ফাইল; বাকি সব (http, অন্য ফাইল) সাথে সাথে বাতিল — অপেক্ষা নেই"""
    if url.startswith(FONT_BASE_URL):
        return default_url_fetcher(url, *args, **kwargs)
    raise ValueError(f"PDF: external resource blocked: {url}")


def report_meta(request):
    """হেডার/ফুটারের কমন তথ্য"""
    user = request.user
    company = getattr(getattr(user, 'profile', None), 'company', None)
    scope = scoping.get_scope(request)
    return {
        'company_name': company.name if company else 'SmartAttendance',
        'branch_name': scope.branch.name if scope.branch else 'All branches',
        'generated_at': timezone.localtime(),
        'generated_by': user.get_full_name() or user.username,
    }


def render_pdf(request, template, context, filename):
    """template + context → inline PDF response"""
    if HTML is None:
        return HttpResponse("PDF engine (WeasyPrint) is not installed on this server.", status=500)
    ctx = {**report_meta(request), **context}
    ctx['branch_name'] = ctx.get('branch_name') or 'All branches'   # কিছু ভিউ "সব ব্রাঞ্চ" এ ফাঁকা পাঠায়
    html = render_to_string(template, ctx, request=request)
    font_config = FontConfiguration()
    pdf = HTML(string=html, base_url=FONT_BASE_URL, url_fetcher=_local_only_fetcher).write_pdf(font_config=font_config)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
