import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# সব গোপন তথ্য প্রজেক্ট রুটের .env ফাইল থেকে আসবে (.env.example দেখুন)
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [v.strip() for v in os.getenv(name, default).split(',') if v.strip()]


SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY সেট করা নেই। .env ফাইলে SECRET_KEY দিন।")

# প্রোডাকশনে সবসময় False; লোকাল ডেভেলপমেন্টে .env এ DEBUG=True দিতে পারেন
DEBUG = env_bool('DEBUG', False)

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')



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
                'attendance_app.context_processors.branch_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'attendance_project.wsgi.application'

# DB_ENGINE=postgresql দিলে PostgreSQL, না দিলে লোকাল SQLite
if os.getenv('DB_ENGINE', 'sqlite3') == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', ''),
            'USER': os.getenv('DB_USER', ''),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        }
    }

# bKash config
BKASH_BASE_URL   = os.getenv('BKASH_BASE_URL', "https://tokenized.pay.bka.sh/v1.2.0-beta/")
BKASH_APP_KEY    = os.getenv('BKASH_APP_KEY', '')
BKASH_APP_SECRET = os.getenv('BKASH_APP_SECRET', '')
BKASH_USERNAME   = os.getenv('BKASH_USERNAME', '')
BKASH_PASSWORD   = os.getenv('BKASH_PASSWORD', '')
BKASH_USE_BEARER = env_bool('BKASH_USE_BEARER', True)  # আপনার ইন্টিগ্রেশন যেটা চায় True/False দিন



# ------------------Email--------------------

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', 'mail.easycodingbd.com')   # তোমার হোস্টিং SMTP সার্ভার
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '465'))                # SSL পোর্ট
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', True)                 # SSL সক্রিয়
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)                # TLS বন্ধ
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', 'info@easycodingbd.com')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL', EMAIL_HOST_USER)




LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'  # Bangladesh time
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_ROOT = BASE_DIR / 'staticfiles'
STATIC_URL = '/static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# For large form submissions (e.g., bulk delete in admin)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = "subscription_app:post_login_router"

SUBSCRIPTION_SUPERUSER_BYPASS = False

CKEDITOR_UPLOAD_PATH = "uploads/"
CKEDITOR_IMAGE_BACKEND = "pillow"
CKEDITOR_ALLOW_NONIMAGE_FILES = False

CKEDITOR_CONFIGS = {
    "default": {
        "toolbar": "full",          # চাইলে 'basic' করতে পারেন
        "height": 300,
        "width": "100%",
        # প্রয়োজন হলে অতিরিক্ত অনুমোদিত ট্যাগ/অ্যাট্রিবিউট যোগ করুন
        # "extraAllowedContent": "iframe[*];span[*];p[*];img[!src,alt,width,height];a[*];",
    }
}
