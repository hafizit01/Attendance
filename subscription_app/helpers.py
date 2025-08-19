from django.apps import apps

def resolve_company(request):
    """
    Company resolve strategy:
    1) request.current_company
    2) session['company_id']
    3) request.user.company (FK/OneToOne)
    4) request.user.companies.first() (M2M)
    5) user.memberships.first().organization (যদি থাকে)
    """
    Company = apps.get_model("<COMPANY_APP>", "Company")  # <-- app label বদলান

    # 1) subdomain middleware set
    if getattr(request, "current_company", None):
        return request.current_company

    # 2) session
    cid = request.session.get("company_id")
    if cid:
        try:
            return Company.objects.get(pk=cid)
        except Company.DoesNotExist:
            pass

    # 3) FK/OneToOne
    if request.user.is_authenticated and hasattr(request.user, "company"):
        c = getattr(request.user, "company", None)
        if c:
            return c

    # 4) M2M
    if request.user.is_authenticated and hasattr(request.user, "companies"):
        c = request.user.companies.first()
        if c:
            return c

    # 5) Membership style
    if request.user.is_authenticated and hasattr(request.user, "memberships"):
        m = request.user.memberships.select_related("organization").first()
        if m and hasattr(m, "organization"):
            return getattr(m, "organization", None)

    return None

def parse_plan_slug(invoice: str) -> str | None:
    """
    invoice format: '<plan-slug>-c<company_id>-u<user_id>-<timestamp>'
    """
    if not invoice:
        return None
    return invoice.split("-")[0].strip()

def parse_company_id(invoice: str):
    """
    extract company id from invoice.
    e.g., 'pro-c12-u7-172...' -> 12
    """
    if not invoice:
        return None
    for part in invoice.split("-"):
        if part.startswith("c") and part[1:].isdigit():
            return int(part[1:])
    return None
