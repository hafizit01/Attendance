from django.shortcuts import render, get_object_or_404, redirect,Http404
from .models import *
from attendance_app.models import *
# from .forms import *
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponseRedirect
from django.core.exceptions import ObjectDoesNotExist
import logging
from django.urls import reverse
from django.db.models import Sum
from django.db.models import Count
from django.http import Http404
from django.db.models import Avg

# Create your views here.

import requests
from subscription_app.models import SubscriptionPlan

from django.core.cache import cache
import requests
import json

def grant_token_function():
    """
    bKash token নেয়ার ফাংশন।
    Token cache-এ সংরক্ষণ করা হয় 20 মিনিটের জন্য
    যাতে repeated requests এ 429 error না আসে।
    """
    # চেক করি cached token আছে কি না
    token = cache.get("bkash_token")
    if token:
        return token

    url = "https://tokenized.pay.bka.sh/v1.2.0-beta/tokenized/checkout/token/grant"
    payload = {
        "app_key": "gUHvbWFK0wXZMP5iuFaIlTtUtc",
        "app_secret": "fAatVju3echAasIAHpqub1ijHfClvWKiWkxDMivcRVdtYDGQ4Wih"
    }
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "username": "01822755994",
        "password": "yVD&fY:v)N5"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        print("Status code:", response.status_code)
        print("Response content:", response.content.decode())

        if response.status_code == 200:
            r = response.json()
            token = r.get("id_token")
            if token:
                # Token cache-এ রাখি 20 মিনিটের জন্য
                cache.set("bkash_token", token, timeout=20*60)
                return token
        # যদি 200 আসে কিন্তু token না আসে
        print("Failed to get token from bKash response.")
        return None

    except requests.exceptions.RequestException as e:
        print("Request Exception:", e)
        return None


token = grant_token_function()



@csrf_exempt
@login_required(login_url='login')
def create_bkash_payment(request, slug):
    id_token = grant_token_function()

    if not id_token:
        return JsonResponse({'error': 'Failed to obtain bKash token'})

    plan = get_object_or_404(SubscriptionPlan, slug=slug)

    # সর্বশেষ payment নেয়া কিন্তু এটি concurrency তে সমস্যা দিতে পারে,
    # তাই invoice নাম্বার তৈরির জন্য ইউনিক কিছু ব্যবহার করো, যেমন user id + timestamp
    import time
    invoice_number = f"Easy{request.user.id}{int(time.time())}"

    payload = {
        "mode": "0011",
        "payerReference": str(request.user.id),  # user এর id বা অন্য কিছু দিতে পারো
        "callbackURL": "http://127.0.0.1:8000/Payment-Status/",  # নিশ্চিত হও এই URL কাজ করে
        "amount": str(plan.price),  # discount_price না থাকলে price নাও
        "currency": "BDT",
        "intent": "sale",
        "merchantInvoiceNumber": invoice_number,
        "title": plan.name,
    }

    headers = {
        "accept": "application/json",
        "Authorization": f"{id_token}",
        "X-APP-Key": "gUHvbWFK0wXZMP5iuFaIlTtUtc",  # settings থেকে নেয়া ভালো
        "content-type": "application/json"
    }

    url = "https://tokenized.pay.bka.sh/v1.2.0-beta/tokenized/checkout/create"

    response = requests.post(url, json=payload, headers=headers)
    create_response = response.json()

    if 'paymentID' in create_response:
        # payment ID পাওয়া গেলে ডাটাবেজে সেভ করো
        BkashPayment.objects.create(
            user=request.user,
            paymentID=create_response['paymentID'],
            createTime=create_response.get('createTime', ''),
            orgLogo=create_response.get('orgLogo', ''),
            orgName=create_response.get('orgName', ''),
            transactionStatus=create_response.get('transactionStatus', ''),
            amount=create_response.get('amount', ''),
            currency=create_response.get('currency', ''),
            intent=create_response.get('intent', ''),
            merchantInvoiceNumber=create_response.get('merchantInvoiceNumber', ''),
            title=plan.name
        )

        bkash_url = create_response.get('bkashURL')
        return redirect(bkash_url)
    else:
        return JsonResponse({'error': 'Payment creation failed', 'details': create_response})


@csrf_exempt
@login_required(login_url='login')
def execute_bkash_payment(request, payment_id=None):
    try:
        token = grant_token_function()

        latest_payment = BkashPayment.objects.filter(user=request.user).last()
        if not latest_payment:
            return {'error': 'No payment found for the user'}

        payment_id = latest_payment.paymentID

        payload = {
            "paymentID": payment_id,
        }

        headers = {
            "accept": "application/json",
            "Authorization": token,
            "X-APP-Key": "gUHvbWFK0wXZMP5iuFaIlTtUtc"
        }

        response_create = requests.post("https://tokenized.pay.bka.sh/v1.2.0-beta/tokenized/checkout/execute", json=payload, headers=headers)
        response = response_create.json()

        if response.get('statusCode') and response.get('statusCode') != '0000':
            if response.get('statusCode') == '2023':
                return {'status': 'insufficient_balance'}
                
            elif response.get('statusCode') == '2029':
                return {'status': 'duplicate_transaction'}
            else:
                text = response.get('statusMessage')
                return {'error': text}
        else:
            payment_execute_data = {
                'user': request.user,
                'paymentID': response.get('paymentID'),
                'createTime': response.get('paymentExecuteTime'),
                'trxID': response.get('trxID'),
                'transactionStatus': response.get('transactionStatus'),
                'amount': response.get('amount'),
                'currency': response.get('currency'),
                'intent': response.get('intent'),
                'merchantInvoiceNumber': response.get('merchantInvoiceNumber'),
                'customerMsisdn': response.get('customerMsisdn'),
                'title': latest_payment.title,
            }

            try:
                new_payment_execute = BkashPaymentExecute.objects.create(**payment_execute_data)
                return {'status': 'success'}
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.exception("Exception occurred while executing bKash payment")
                return {'error': 'Internal server error'}
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception("Exception occurred while executing bKash payment")
        return {'error': 'Payment execution failed. Please try again.'}



@login_required(login_url='login')
def get_payment_status(request):
    status = request.GET.get('status')
    payment_id = request.GET.get('paymentID')

    if status == 'success':
        execute_response = execute_bkash_payment(request, payment_id)
        if 'status' in execute_response:
            if execute_response['status'] == 'insufficient_balance':
                return redirect('cancel')
            elif execute_response['status'] == 'duplicate_transaction':
                return redirect('cancel')
            else:
                return redirect('success')
        elif 'error' in execute_response:
            return JsonResponse({'error': execute_response['error']})
        else:
            return JsonResponse({'error': 'Unknown response from payment execution'})
    elif status == 'cancel':
        return redirect('cancel')
    elif status == 'failure':
        message = 'লেনদেন ব্যর্থ হয়েছে'
        return render(request, 'failer.html', {'message': message})
    else:
        message = 'Unknown status.'
        return render(request, 'failer.html', {'message': message, 'status': status, 'payment_id': payment_id})

@login_required(login_url='login')
def success(request):
    return render(request, 'success.html')

@login_required(login_url='login')
def cancel(request):
    return render(request, 'cancel.html')


@login_required(login_url='login')
def payment_details(request, slug):
    payment = get_object_or_404(BkashPaymentExecute, slug=slug)
    
    assignments = Assignment.objects.filter(course__titel=payment.title)
    
    return render(request, 'payment_details.html', {'payment': payment, 'assignments': assignments})
    