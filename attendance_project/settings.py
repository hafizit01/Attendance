import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = 'your-secret-key'

DEBUG = True


ALLOWED_HOSTS = ['*']
# ALLOWED_HOSTS = ['rssoftbd.com', '118.179.173.172', 'localhost']
# CSRF_TRUSTED_ORIGINS = ['https://rssoftbd.com']



INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'attendance_app',
    'userapp',
    'payroll',
    'widget_tweaks',
    'subscription_app',
    'payment_app',
    "ckeditor",
    "ckeditor_uploader",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    "attendance_project.middleware.SubscriptionExpiryMiddleware",
    'django.contrib.messages.middleware.MessageMiddleware'
]

ROOT_URLCONF = 'attendance_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'attendance_project.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         'NAME': 'your_database_name',
#         'USER': 'your_mysql_user',
#         'PASSWORD': 'your_mysql_password',
#         'HOST': 'your_mysql_host',  # যেমন: 'localhost' বা '127.0.0.1' বা রিমোট IP
#         'PORT': '3306',
#         'OPTIONS': {
#             'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
#         },
#     }
# }


# bKash config
BKASH_BASE_URL   = "https://tokenized.pay.bka.sh/v1.2.0-beta/"
BKASH_APP_KEY    = "gUHvbWFK0wXZMP5iuFaIlTtUtc"
BKASH_APP_SECRET = "fAatVju3echAasIAHpqub1ijHfClvWKiWkxDMivcRVdtYDGQ4Wih"
BKASH_USERNAME   = "01822755994"
BKASH_PASSWORD   = "yVD&fY:v)N5"
BKASH_USE_BEARER = True  # আপনার ইন্টিগ্রেশন যেটা চায় True/False দিন



# ------------------Email--------------------
# settings.py

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'mail.easycodingbd.com'       # তোমার হোস্টিং SMTP সার্ভার
EMAIL_PORT = 465                           # SSL পোর্ট
EMAIL_USE_SSL = True                       # SSL সক্রিয়
EMAIL_USE_TLS = False                      # TLS বন্ধ
EMAIL_HOST_USER = 'info@easycodingbd.com'  # তোমার ইমেইল
EMAIL_HOST_PASSWORD = 'Easy@#1122'  # ইমেইল অ্যাকাউন্টের আসল পাসওয়ার্ড
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER




LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATIC_URL = '/static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_ROOT = BASE_DIR / 'staticfiles'

# For large form submissions (e.g., bulk delete in admin)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 15000
USE_TZ = True  # ✅ এটা ঠিক আছে
TIME_ZONE = 'Asia/Dhaka'  # ✅ Bangladesh time

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = "post_login_router"

SUBSCRIPTION_SUPERUSER_BYPASS = False

CKEDITOR_UPLOAD_PATH = "uploads/"
CKEDITOR_IMAGE_BACKEND = "pillow"
CKEDITOR_ALLOW_NONIMAGE_FILES = False

CKEDITOR_CONFIGS = {
    "default": {
        "toolbar": "full",          # চাইলে 'basic' করতে পারেন
        "height": 300,
        "width": "100%",
        # প্রয়োজন হলে অতিরিক্ত অনুমোদিত ট্যাগ/অ্যাট্রিবিউট যোগ করুন
        # "extraAllowedContent": "iframe[*];span[*];p[*];img[!src,alt,width,height];a[*];",
    }
}