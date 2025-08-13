from django.urls import path
from . views import *

urlpatterns = [
    
    path('Payment-Status/', get_payment_status, name='get_payment_status'),
    path('success/', success, name='success'),
    path('cancel/', cancel, name='cancel'),
   path('create_bkash_payment/<slug:slug>/', create_bkash_payment, name='create_bkash_payment'),
    path('execute_bkash_payment/',execute_bkash_payment, name='execute_bkash_payment'),
    path('payment-details/<slug:slug>/', payment_details, name='payment_details'),
    

]