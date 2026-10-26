from datetime import timedelta
import logging
import time
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
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
    active সাবস্ক্রিপশন থাকলে তার শেষের পরদিন থেকে নতুনটা শুরু (early renewal এ দিন হারায় না),
    না থাকলে আজ থেকে নতুন সাবস্ক্রিপশন।
    আগেরটা active রাখা হয়, যাতে তার মেয়াদ শেষ না হওয়া পর্যন্ত ইউজার ব্লক না হয়।
    """
    today = timezone.localdate()

    latest = (
        UserSubscription.objects
        .filter(user=user, active=True, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )

    start = latest.end_date + timedelta(days=1) if latest else today
    end = start + timedelta(days=plan.duration_days)

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
@login_required  # settings.LOGIN_URL (/login/)
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
# Plan resolve (invoice থেকে)
# -------------------------------
def _plan_from_invoice(invoice: str):
    """
    invoice format: '<plan-slug>-<user_id>-<timestamp>'
    slug এ '-' থাকতে পারে (যেমন 'basic-plan'), তাই ডান দিক থেকে শেষ দুটো অংশ কেটে নিই।
    """
    parts = (invoice or "").rsplit("-", 2)
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return SubscriptionPlan.objects.filter(slug=parts[0].lower()).first()


def _amount_matches(paid, expected: Decimal) -> bool:
    try:
        return Decimal(str(paid)).quantize(Decimal("0.01")) == Decimal(expected).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False


# -------------------------------
# Execute Payment (internal helper)
# -------------------------------
def _execute_payment(user, payment_id):
    """
    bKash যে paymentID ফেরত দিয়েছে সেটাই execute করে।
    dict রিটার্ন করে যাতে কলার বুঝে রিডাইরেক্ট করতে পারে।
    """
    if not payment_id:
        return {"error": "Missing paymentID"}

    # শুধু এই ইউজারের তৈরি করা নির্দিষ্ট payment
    payment = BkashPayment.objects.filter(user=user, paymentID=payment_id).first()
    if not payment:
        return {"error": "Payment not found for this user"}

    # আগেই প্রসেস হয়ে থাকলে আবার bKash এ execute করি না (idempotent)
    done = BkashPaymentExecute.objects.filter(paymentID=payment_id).first()
    if done:
        if done.subscription_id:
            return {"status": "success"}
        return {"error": "This payment was already processed but no subscription was activated. Please contact support."}

    try:
        id_token = grant_token_function()
        if not id_token:
            return {"error": "Token fetch failed"}

        headers = {
            "accept": "application/json",
            "Authorization": _auth_header(id_token),
            "X-APP-Key": _bkash_app_key(),
        }
        url = f"{_bkash_base_url()}/tokenized/checkout/execute"

        r = requests.post(url, json={"paymentID": payment_id}, headers=headers, timeout=30)
        resp = r.json()
    except Exception:
        logger.exception("bKash execute error")
        return {"error": "Payment execution failed. Please try again."}

    # error branch
    code = str(resp.get("statusCode") or "")
    if code and code != "0000":
        if code == "2023":
            return {"status": "insufficient_balance"}
        elif code == "2029":
            return {"status": "duplicate_transaction"}
        else:
            return {"error": resp.get("statusMessage") or "Execution failed"}

    if resp.get("paymentID") != payment_id:
        logger.error("bKash execute paymentID mismatch: expected=%s got=%s", payment_id, resp.get("paymentID"))
        return {"error": "Payment ID mismatch"}

    # ট্রানজেকশন স্ট্যাটাস ফাইনাল সাকসেস কিনা
    if not _status_is_success(resp.get("transactionStatus")):
        return {"error": "Transaction not successful"}

    # প্ল্যান resolve: নিজের DB তে রাখা invoice থেকে slug, নাহলে title
    plan = _plan_from_invoice(payment.merchantInvoiceNumber)
    if not plan and payment.title:
        plan = SubscriptionPlan.objects.filter(name=payment.title).first()

    exec_fields = {
        "user": user,
        "paymentID": payment_id,
        "createTime": resp.get("paymentExecuteTime", ""),
        "trxID": resp.get("trxID", ""),
        "transactionStatus": resp.get("transactionStatus", ""),
        "amount": resp.get("amount", ""),
        "currency": resp.get("currency", ""),
        "intent": resp.get("intent", ""),
        "merchantInvoiceNumber": resp.get("merchantInvoiceNumber", ""),
        "customerMsisdn": resp.get("customerMsisdn", ""),
        "title": payment.title,
    }

    # টাকা কাটা হয়ে গেছে, তাই সমস্যা হলেও রেকর্ড (subscription ছাড়া) রাখি — অডিটের জন্য
    if not plan:
        BkashPaymentExecute.objects.create(**exec_fields)
        logger.error("bKash paid but plan not found: paymentID=%s invoice=%s", payment_id, payment.merchantInvoiceNumber)
        return {"error": "Plan not found to activate subscription"}

    if not _amount_matches(resp.get("amount"), plan.price) or (resp.get("currency") or "BDT") != "BDT":
        BkashPaymentExecute.objects.create(**exec_fields)
        logger.error(
            "bKash amount mismatch: paymentID=%s paid=%s %s plan=%s price=%s",
            payment_id, resp.get("amount"), resp.get("currency"), plan.slug, plan.price,
        )
        return {"error": "Paid amount does not match the plan price. Please contact support."}

    # সাবস্ক্রিপশন সক্রিয়/Extend + execute রেকর্ড একসাথে (atomic)
    with transaction.atomic():
        sub = activate_or_extend_subscription(user, plan)
        BkashPaymentExecute.objects.create(subscription=sub, **exec_fields)
    return {"status": "success"}


@login_required  # settings.LOGIN_URL (/login/)
def execute_bkash_payment(request):
    """ম্যানুয়াল execute: ?paymentID=... (সাধারণত get_payment_status থেকেই হয়)"""
    result = _execute_payment(request.user, request.GET.get("paymentID"))
    return JsonResponse(result, status=200 if result.get("status") == "success" else 400)


# -------------------------------
# Callback handler from bKash
# -------------------------------
@login_required  # settings.LOGIN_URL (/login/)
def get_payment_status(request):
    """
    bKash callbackURL এই ভিউ-তে পিং করে।
    কুয়েরি-প্যারাম status: success/cancel/failure
    """
    status = request.GET.get("status")
    payment_id = request.GET.get("paymentID")

    if status == "success":
        result = _execute_payment(request.user, payment_id)
        if "status" in result:
            if result["status"] in {"insufficient_balance", "duplicate_transaction"}:
                return redirect("payment_app:cancel")
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
@login_required  # settings.LOGIN_URL (/login/)
def success(request):
    return render(request, "success.html")

@login_required  # settings.LOGIN_URL (/login/)
def cancel(request):
    return render(request, "cancel.html")


# -------------------------------
# Example detail page (optional)
# -------------------------------
# @login_required  # settings.LOGIN_URL (/login/)
# def payment_details(request, slug):
#     payment = get_object_or_404(BkashPaymentExecute, slug=slug)
#     assignments = Assignment.objects.filter(course__titel=payment.title)
#     return render(request, "payment_details.html", {"payment": payment, "assignments": assignments})
