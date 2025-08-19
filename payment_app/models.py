from django.db import models
from django.db import models
from attendance_app.models import *
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from autoslug import AutoSlugField

from subscription_app.models import SubscriptionPlan, UserSubscription

class BkashPayment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    paymentID = models.CharField(max_length=150)
    createTime = models.CharField(max_length=150)
    orgLogo = models.CharField(max_length=1000, blank=True, null=True)
    orgName = models.CharField(max_length=150)
    transactionStatus = models.CharField(max_length=150)
    amount = models.CharField(max_length=150)
    currency = models.CharField(max_length=150)
    intent = models.CharField(max_length=150)
    merchantInvoiceNumber = models.CharField(max_length=150)
    title = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        verbose_name = 'BkashPayment'
        verbose_name_plural = 'BkashPayments'

    def __str__(self):
        return self.paymentID


class BkashPaymentExecute(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    paymentID = models.CharField(max_length=150)
    createTime = models.CharField(max_length=150)
    updateTime = models.CharField(max_length=150, blank=True, null=True)
    trxID = models.CharField(max_length=150)
    transactionStatus = models.CharField(max_length=150)
    amount = models.CharField(max_length=150)
    currency = models.CharField(max_length=150)
    intent = models.CharField(max_length=150)
    merchantInvoiceNumber = models.CharField(max_length=150)
    customerMsisdn = models.CharField(max_length=150)
    title = models.CharField(max_length=150, blank=True, null=True)
    batch = models.CharField(max_length=50, blank=True, null=True)
    # models.py
    subscription = models.OneToOneField(
        UserSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="bkash_payment"
    )

    slug = AutoSlugField(populate_from='merchantInvoiceNumber' ,unique=True,blank=True,null=True)

    class Meta:
        verbose_name_plural = 'BkashPaymentExecute'

    def __str__(self):
        return f'{self.user.username} + {self.title}'