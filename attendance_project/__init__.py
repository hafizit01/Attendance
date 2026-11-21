import pymysql

pymysql.install_as_MySQLdb()

# Celery ঐচ্ছিক — ইনস্টল থাকলে লোড হয়, না থাকলে কিছু হয় না
try:
    from .celery import app as celery_app  # noqa: F401
except ImportError:
    celery_app = None
