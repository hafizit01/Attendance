"""বার্তা পাঠানো + লগ। একই dedupe_key দিয়ে দুবার পাঠানো হয় না (সফল বা শুধু-লগ হলে)।"""
import logging

from django.db import IntegrityError, transaction

from attendance_app.models import MessageLog
from . import backends

logger = logging.getLogger(__name__)


def send_message(company, phone, text, *, kind, channel='sms', dedupe_key=None):
    """
    রিটার্ন: MessageLog। নম্বর ঠিক না হলে status=skipped।
    dedupe_key দিয়ে আগেই সফল/লগ হয়ে থাকলে আবার পাঠায় না (আগেরটাই রিটার্ন)।
    """
    if dedupe_key:
        prev = MessageLog.objects.filter(dedupe_key=dedupe_key).first()
        if prev and prev.status in ('sent', 'logged'):
            return prev
        if prev:                      # আগে ব্যর্থ হয়েছিল — আবার চেষ্টা, পুরনোটার key মুক্ত করে
            MessageLog.objects.filter(pk=prev.pk).update(dedupe_key=None)
    to = backends.normalize_bd(phone)
    if not to:
        status, provider, response = 'skipped', '', f'Invalid phone number: {phone!r}'
    else:
        try:
            status, provider, response = backends.send(channel, to, text)
        except Exception as e:  # গেটওয়ের সমস্যায় পুরো অ্যালার্ট থামবে না
            logger.exception("Message send failed")
            status, provider, response = 'failed', channel, str(e)[:500]
    try:
        with transaction.atomic():
            return MessageLog.objects.create(company=company, channel=channel, to=to or str(phone or '')[:20], text=text,
                                             kind=kind, status=status, provider=provider, response=response[:500],
                                             dedupe_key=dedupe_key)
    except IntegrityError:            # একই সময়ে আরেকটা প্রসেস পাঠিয়ে ফেলেছে
        return MessageLog.objects.get(dedupe_key=dedupe_key)
