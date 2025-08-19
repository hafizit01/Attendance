from datetime import timedelta
import logging
import time
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import BkashPayment, BkashPaymentExecute
from subscription_app.models import *
# যদি payment_details এ Assignment লাগে
# from attendance_app.models import Assignment


logger = logging.getLogger(__name__)


# -------------------------------
# Config helpers
# -------------------------------
def _bkash_base_url() -> str:
    # উদাহরণ: "https://tokenized.pay.bka.sh/v1.2.0-beta"
    return getattr(settings, "BKASH_BASE_URL", "https://tokenized.pay.bka.sh/v1.2.0-beta")

def _bkash_app_key() -> str:
    return getattr(settings, "BKASH_APP_KEY", "")

def _bkash_app_secret() -> str:
    return getattr(settings, "BKASH_APP_SECRET", "")

def _bkash_username() -> str:
    return getattr(settings, "BKASH_USERNAME", "")

def _bkash_password() -> str:
    return getattr(settings, "BKASH_PASSWORD", "")

def _bkash_use_bearer() -> bool:
    # কিছু ইন্টিগ্রেশনে Authorization: Bearer <token> দরকার হয়
    return bool(getattr(settings, "BKASH_USE_BEARER", False))


# -------------------------------
# Subscription helper
# -------------------------------
def activate_or_extend_subscription(user, plan: SubscriptionPlan) -> UserSubscription:
    """
    active সাবস্ক্রিপশন থাকলে শেষে extend, না থাকলে আজ থেকে নতুন সাবস্ক্রিপশন।
    একটিমাত্র active রাখি।
    """
    today = timezone.localdate()

    current = (
        UserSubscription.objects
        .filter(user=user, active=True, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )

    if current:
        start = current.end_date + timedelta(days=1)
    else:
        start = today

    end = start + timedelta(days=plan.duration_days)

    # একটিমাত্র active রাখুন
    UserSubscription.objects.filter(user=user, active=True).update(active=False)

    sub = UserSubscription.objects.create(
        user=user,
        plan=plan,
        start_date=start,
        end_date=end,
        active=True,
    )
    return sub


# -------------------------------
# bKash token (cached 20 min)
# -------------------------------
def grant_token_function():
    """
    bKash token গ্রান্ট করে; ক্যাশে 20 মিনিট রাখে।
    """
    token = cache.get("bkash_token")
    if token:
        return token

    url = f"{_bkash_base_url()}/tokenized/checkout/token/grant"
    payload = {
        "app_key": _bkash_app_key(),
        "app_secret": _bkash_app_secret(),
    }
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "username": _bkash_username(),
        "password": _bkash_password(),
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        logger.info("bKash grant token status=%s", r.status_code)

        if r.status_code == 200:
            data = r.json()
            token = data.get("id_token")
            if token:
                cache.set("bkash_token", token, timeout=20 * 60)
                return token

        logger.error("Failed to get bKash token: %s", getattr(r, "text", ""))
        return None

    except requests.RequestException as e:
        logger.exception("bKash token grant failed: %s", e)
        return None


def _auth_header(id_token: str) -> str:
    return f"Bearer {id_token}" if _bkash_use_bearer() else f"{id_token}"


def _status_is_success(s: str) -> bool:
    return (s or "").strip() in {"Success", "Completed", "COMPLETED", "SUCCEEDED", "OK", "0000"}


# -------------------------------
# Create Payment
# -------------------------------
@csrf_exempt
@login_required(login_url="login")
def create_bkash_payment(request, slug):
    id_token = grant_token_function()
    if not id_token:
        return JsonResponse({"error": "Failed to obtain bKash token"}, status=500)

    plan = get_object_or_404(SubscriptionPlan, slug=slug)

    # ইনভয়েস: plan slug + user id + timestamp → ইউনিক + execute-এ প্ল্যান সনাক্ত সহজ
    invoice_number = f"{plan.slug}-{request.user.id}-{int(time.time())}"

    callback_url = request.build_absolute_uri(reverse("payment_app:get_payment_status"))

    payload = {
        "mode": "0011",
        "payerReference": str(request.user.id),
        "callbackURL": callback_url,
        "amount": str(plan.price),  # Decimal → str
        "currency": "BDT",
        "intent": "sale",
        "merchantInvoiceNumber": invoice_number,
        "title": plan.name,
    }
    headers = {
        "accept": "application/json",
        "Authorization": _auth_header(id_token),
        "X-APP-Key": _bkash_app_key(),
        "content-type": "application/json",
    }
    url = f"{_bkash_base_url()}/tokenized/checkout/create"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()
    except Exception:
        logger.exception("bKash create request failed")
        return JsonResponse({"error": "Payment creation failed"}, status=502)

    if "paymentID" in data:
        # DB-তে create রাখি (audit/log)
        BkashPayment.objects.create(
            user=request.user,
            paymentID=data["paymentID"],
            createTime=data.get("createTime", ""),
            orgLogo=data.get("orgLogo", ""),
            orgName=data.get("orgName", ""),
            transactionStatus=data.get("transactionStatus", ""),
            amount=data.get("amount", ""),
            currency=data.get("currency", ""),
            intent=data.get("intent", ""),
            merchantInvoiceNumber=data.get("merchantInvoiceNumber", ""),
            title=plan.name,
        )
        bkash_url = data.get("bkashURL")
        return redirect(bkash_url)
    else:
        logger.error("bKash create error: %s", data)
        return JsonResponse({"error": "Payment creation failed", "details": data}, status=400)


# -------------------------------
# Execute Payment (internal helper-style view)
# -------------------------------
@csrf_exempt
@login_required(login_url="login")
def execute_bkash_payment(request, payment_id=None):
    """
    get_payment_status() থেকে কল হয়।
    dict রিটার্ন করে যাতে কলার বুঝে রিডাইরেক্ট করতে পারে।
    """
    try:
        id_token = grant_token_function()
        if not id_token:
            return {"error": "Token fetch failed"}

        # সর্বশেষ initiate করা payment ধরা হলো (চাইলে payment_id দিয়ে নির্দিষ্ট করতে পারেন)
        latest_payment = BkashPayment.objects.filter(user=request.user).last()
        if not latest_payment:
            return {"error": "No payment found for the user"}

        payment_id = latest_payment.paymentID
        payload = {"paymentID": payment_id}
        headers = {
            "accept": "application/json",
            "Authorization": _auth_header(id_token),
            "X-APP-Key": _bkash_app_key(),
        }
        url = f"{_bkash_base_url()}/tokenized/checkout/execute"

        r = requests.post(url, json=payload, headers=headers, timeout=30)
        resp = r.json()

        # error branch
        code = str(resp.get("statusCode") or "")
        if code and code != "0000":
            if code == "2023":
                return {"status": "insufficient_balance"}
            elif code == "2029":
                return {"status": "duplicate_transaction"}
            else:
                return {"error": resp.get("statusMessage") or "Execution failed"}

        # success branch → Execute রেকর্ড idempotent ভাবে সেভ
        exec_defaults = {
            "user": request.user,
            "createTime": resp.get("paymentExecuteTime", ""),
            "trxID": resp.get("trxID", ""),
            "transactionStatus": resp.get("transactionStatus", ""),
            "amount": resp.get("amount", ""),
            "currency": resp.get("currency", ""),
            "intent": resp.get("intent", ""),
            "merchantInvoiceNumber": resp.get("merchantInvoiceNumber", ""),
            "customerMsisdn": resp.get("customerMsisdn", ""),
            "title": latest_payment.title,
        }
        exec_obj, created = BkashPaymentExecute.objects.get_or_create(
            paymentID=resp.get("paymentID"), defaults=exec_defaults
        )
        if not created:
            # already executed → idempotent success
            return {"status": "success"}

        # ট্রানজেকশন স্ট্যাটাস ফাইনাল সাকসেস কিনা
        if not _status_is_success(resp.get("transactionStatus")):
            return {"error": "Transaction not successful"}

        # প্ল্যান resolve: invoice থেকে slug, নাহলে title
        invoice = exec_obj.merchantInvoiceNumber or latest_payment.merchantInvoiceNumber or ""
        plan_slug = invoice.split("-")[0].lower() if invoice else ""
        plan = None
        if plan_slug:
            try:
                plan = SubscriptionPlan.objects.get(slug=plan_slug)
            except SubscriptionPlan.DoesNotExist:
                plan = None
        if not plan:
            try:
                plan = SubscriptionPlan.objects.get(name=exec_obj.title)
            except SubscriptionPlan.DoesNotExist:
                return {"error": "Plan not found to activate subscription"}

        # সাবস্ক্রিপশন সক্রিয়/Extend
        _ = activate_or_extend_subscription(request.user, plan)
        return {"status": "success"}

    except Exception:
        logger.exception("bKash execute error")
        return {"error": "Payment execution failed. Please try again."}


# -------------------------------
# Callback handler from bKash
# -------------------------------
@login_required(login_url="login")
def get_payment_status(request):
    """
    bKash callbackURL এই ভিউ-তে পিং করে।
    কুয়েরি-প্যারাম status: success/cancel/failure
    """
    status = request.GET.get("status")
    payment_id = request.GET.get("paymentID")

    if status == "success":
        result = execute_bkash_payment(request, payment_id)
        if "status" in result:
            if result["status"] in {"insufficient_balance", "duplicate_transaction"}:
                return redirect("cancel")
            return redirect("payment_app:success")
        elif "error" in result:
            return JsonResponse({"error": result["error"]}, status=400)
        else:
            return JsonResponse({"error": "Unknown response from payment execution"}, status=500)

    elif status == "cancel":
        return redirect("payment_app:cancel")

    elif status == "failure":
        msg = "লেনদেন ব্যর্থ হয়েছে"
        return render(request, "failure.html", {"message": msg})

    else:
        msg = "Unknown status"
        return render(
            request,
            "failure.html",
            {"message": msg, "status": status, "payment_id": payment_id},
        )


# -------------------------------
# Simple result pages
# -------------------------------
@login_required(login_url="login")
def success(request):
    return render(request, "success.html")

@login_required(login_url="login")
def cancel(request):
    return render(request, "cancel.html")


# -------------------------------
# Example detail page (optional)
# -------------------------------
# @login_required(login_url="login")
# def payment_details(request, slug):
#     payment = get_object_or_404(BkashPaymentExecute, slug=slug)
#     assignments = Assignment.objects.filter(course__titel=payment.title)
#     return render(request, "payment_details.html", {"payment": payment, "assignments": assignments})
