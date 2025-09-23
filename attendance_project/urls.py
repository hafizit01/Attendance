from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from decorator_include import decorator_include
from subscription_app.decorators import subscription_required

urlpatterns = [
    # EXEMPT / PUBLIC
    path('admin/', admin.site.urls),
    path('subscription/', include(('subscription_app.urls', 'subscription_app'), namespace='subscription_app')),
    path('', include(('payment_app.urls', 'payment_app'), namespace='payment_app')),  # ✅ গার্ড ছাড়া, নিজস্ব প্রিফিক্স

    # PROTECTED
    path("", decorator_include(subscription_required, ("attendance_app.urls", "attendance_app"))),
    path('', decorator_include(subscription_required, ('userapp.urls', 'userapp'))),
    path('', decorator_include(subscription_required, ('payroll.urls', 'payroll'))),
    path('ckeditor/', include('ckeditor_uploader.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) \
  + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

