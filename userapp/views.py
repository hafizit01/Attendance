# userapp/views.py  (আপনার login_view যেখানে আছে)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect,resolve_url,get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse


from django.http import HttpResponse
from django.template.loader import render_to_string
import weasyprint

from subscription_app.models import UserSubscription
from django.utils import timezone

from django.contrib.auth.decorators import login_required
from .forms import EmployeeProfileForm   # form আমরা নিচে বানাবো

# EmployeeProfile List
from django.core.paginator import Paginator
from django.db.models import Q
from .models import EmployeeProfile

def _is_expired(user):
    company = getattr(user, "current_company", None) or getattr(user, "company", None)
    sub = getattr(company, "subscription", None) or \
          UserSubscription.objects.filter(user=user).order_by("-end_date").first()
    if not sub:
        return True  # প্ল্যানই না থাকলে expired-এর মত আচরণ করতে চাইলে True রাখুন
    today = timezone.localdate()
    end_date = sub.end_date.date() if hasattr(sub.end_date, "date") else sub.end_date
    return end_date < today or getattr(sub, "is_expired", False)


def _safe_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        login_url = settings.LOGIN_URL if isinstance(settings.LOGIN_URL, str) else "/login/"
        if not str(nxt).startswith(str(login_url)):
            return nxt
    # Fallback সোজা fix
    return reverse("attendance_app:dashboard")


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username') or request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # ✅ লগইনের পরই expired চেক
            if _is_expired(user):
                messages.warning(request, "Your subscription has expired.")
                return redirect("subscription_app:my_plans")  # 🔁 সরাসরি My Plans/Expired পেজে

            messages.success(request, f'Welcome back, {user.get_username()}!')
            return redirect(_safe_next(request))  # next বা dashboard

        else:
            messages.error(request, '⚠️ Invalid username or password. Please try again.')

    return render(request, 'login.html')





def employee_profile_list(request):
    search_query = request.GET.get("q", "")
    profiles = EmployeeProfile.objects.select_related("employee").all()

    if search_query:
        profiles = profiles.filter(
            Q(employee__name__icontains=search_query) |
            Q(designation__icontains=search_query) |
            Q(mobile_number__icontains=search_query) |
            Q(bank_account__icontains=search_query) |
            Q(voter_id__icontains=search_query)
        )

    paginator = Paginator(profiles, 10)  # প্রতি পেজে 10টা প্রোফাইল
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "list.html", {
        "profiles": page_obj,
        "search_query": search_query
    })



from django.conf import settings
import os

@login_required
def employee_profile_list_pdf(request):
    search_query = request.GET.get("q", "")
    profiles = EmployeeProfile.objects.select_related("employee").all()

    if search_query:
        profiles = profiles.filter(
            Q(employee__name__icontains=search_query) |
            Q(designation__icontains=search_query) |
            Q(mobile_number__icontains=search_query) |
            Q(bank_account__icontains=search_query) |
            Q(voter_id__icontains=search_query)
        )

    # Template render
    html = render_to_string("employee_profile_list_pdf.html", {
        "profiles": profiles,
        "search_query": search_query
    })

    # Response as PDF
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'filename="employee_profiles.pdf"'

    # Just render PDF directly (no external CSS file)
    weasyprint.HTML(string=html, base_url=request.build_absolute_uri()).write_pdf(response)
    return response

    

# EmployeeProfile Detail
@login_required
def employee_profile_detail(request, pk):
    profile = get_object_or_404(EmployeeProfile, pk=pk)
    return render(request, "detail.html", {"profile": profile})

@login_required
def employee_profile_pdf(request, pk):
    profile = get_object_or_404(EmployeeProfile, pk=pk)

    # Template render
    html = render_to_string("pdf_template.html", {"profile": profile})

    # Create PDF
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'filename="employee_{profile.employee.id}.pdf"'
    weasyprint.HTML(string=html).write_pdf(
        response,
        stylesheets=[weasyprint.CSS(string="""
            body { font-family: sans-serif; }
            .header { text-align: center; border-bottom: 2px solid #333; margin-bottom: 20px; }
            .section { margin-bottom: 20px; }
            .section h3 { background: #f1f1f1; padding: 5px; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            table, th, td { border: 1px solid #ccc; padding: 6px; text-align: left; }
        """)]
    )
    return response



# Create EmployeeProfile
def employee_profile_create(request):
    if request.method == "POST":
        form = EmployeeProfileForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Employee Profile added successfully!")
            return redirect("userapp:employee_profile_list")
        else:
            messages.error(request, "❌ Something went wrong. Please check the form.")
    else:
        form = EmployeeProfileForm()
    return render(request, "form.html", {"form": form, "title": "Add Employee Profile"})


# Update EmployeeProfile
@login_required
def employee_profile_update(request, pk):
    profile = get_object_or_404(EmployeeProfile, pk=pk)
    if request.method == "POST":
        form = EmployeeProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Employee Profile updated successfully ✏️")
            return redirect("userapp:employee_profile_detail", pk=pk)
    else:
        form = EmployeeProfileForm(instance=profile)
    return render(request, "form.html", {"form": form, "title": "Edit Employee Profile"})


# Delete EmployeeProfile
@login_required
def employee_profile_delete(request, pk):
    profile = get_object_or_404(EmployeeProfile, pk=pk)
    if request.method == "POST":
        profile.delete()
        messages.success(request, "Employee Profile deleted ❌")
        return redirect("userapp:employee_profile_list")
    return render(request, "confirm_delete.html", {"profile": profile})





def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect(resolve_url(getattr(settings, "LOGIN_URL", "/login/")))



def post_login_router(request):
    if request.user.is_superuser:
        return redirect("admin:index")
    elif request.user.groups.filter(name="Teacher").exists():
        return redirect("teacher_app:dashboard")
    return redirect("attendance_app:dashboard")