# payment_app/urls.py
from django.urls import path
from . import views

app_name = "payment_app"   # ✅
urlpatterns = [
    path("bkash/create/<slug:slug>/", views.create_bkash_payment, name="create_bkash_payment"),
    path("bkash/execute/", views.execute_bkash_payment, name="execute_bkash_payment"),
    path("bkash/status/", views.get_payment_status, name="get_payment_status"),  # ✅
    path("success/", views.success, name="success"),
    path("cancel/", views.cancel, name="cancel"),
]
