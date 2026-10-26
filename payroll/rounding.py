# payroll/rounding.py
# নগদ বেতনের খুচরা টাকা মেলানো (ফিগার মিল) — কোম্পানির সেটিং চালু থাকলেই প্রযোজ্য।
#
# নিয়ম (সবসময় উপরের দিকে):
#   শেষ দুই অঙ্ক 00        -> যেমন আছে      (7,400 -> 7,400)
#   শেষ দুই অঙ্ক 01 – 50   -> 50 এ          (7,440 -> 7,450)
#   শেষ দুই অঙ্ক 51 – 99   -> পরের 100 এ    (7,460 -> 7,500)
# পয়সা আগে পূর্ণ টাকায় (half-up) নেওয়া হয়, যাতে 7,450.40 পয়সার জন্য 7,500 না হয়ে যায়।
from decimal import Decimal, ROUND_HALF_UP

HALF = Decimal(50)
HUNDRED = Decimal(100)


def round_cash_figure(amount) -> Decimal:
    amount = Decimal(amount).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if amount <= 0:
        return amount
    remainder = amount % HUNDRED
    if remainder == 0:
        return amount
    base = amount - remainder
    return base + (HALF if remainder <= HALF else HUNDRED)
