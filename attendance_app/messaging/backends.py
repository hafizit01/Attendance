"""
SMS / WhatsApp গেটওয়ে অ্যাডাপ্টার। কোনটা চলবে তা .env ঠিক করে:

    MESSAGING_SMS_BACKEND=console | http | twilio          (ডিফল্ট console = কিছু পাঠায় না, শুধু লগ)
    MESSAGING_WHATSAPP_BACKEND=console | twilio | meta

  http (বাংলাদেশের বেশিরভাগ SMS গেটওয়ে — BulkSMSBD, SSL Wireless, Alpha SMS ইত্যাদি):
    MESSAGING_HTTP_URL=https://gateway.example/api/send?api_key=XXX&senderid=YYY&number={to}&message={text}
    MESSAGING_HTTP_METHOD=GET  (বা POST — তখন URL এর query না থাকলে {to}/{text} ফর্ম ফিল্ড হিসেবে যায়)
    MESSAGING_HTTP_SUCCESS=     (ঐচ্ছিক: উত্তরে এই লেখা থাকলে সফল; খালি = HTTP 2xx হলেই সফল)
  twilio:  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_SMS_FROM, TWILIO_WHATSAPP_FROM (যেমন +14155238886)
  meta (WhatsApp Cloud API): WHATSAPP_META_TOKEN, WHATSAPP_META_PHONE_ID
    ⚠️ Meta শুধু গত ২৪ ঘণ্টায় কথা বলা নম্বরে সাধারণ টেক্সট দেয়; অন্যদের জন্য অনুমোদিত template লাগে।

প্রতিটা backend রিটার্ন করে (status, provider, response) — status: 'sent' | 'logged' | 'failed'
"""
import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)
TIMEOUT = 15


def _env(name, default=''):
    return (os.getenv(name) or default).strip()


def normalize_bd(phone):
    """01712345678 / +8801712345678 / 8801712345678 → 8801712345678 (ঠিক না হলে '')"""
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if digits.startswith('880') and len(digits) == 13:
        return digits
    if digits.startswith('01') and len(digits) == 11:
        return '88' + digits
    if digits.startswith('1') and len(digits) == 10:
        return '880' + digits
    if 10 <= len(digits) <= 15 and str(phone).strip().startswith('+'):
        return digits          # অন্য দেশের নম্বর
    return ''


def _console(channel, to, text):
    logger.info("[%s console] to %s: %s", channel, to, text)
    return 'logged', 'console', 'MESSAGING backend not configured — message was only logged.'


def _http(to, text):
    import requests
    url = _env('MESSAGING_HTTP_URL')
    if not url:
        return 'failed', 'http', 'MESSAGING_HTTP_URL is not set'
    method = _env('MESSAGING_HTTP_METHOD', 'GET').upper()
    try:
        if method == 'POST' and '{to}' not in url:
            r = requests.post(url, data={'to': to, 'number': to, 'message': text, 'text': text}, timeout=TIMEOUT)
        else:
            full = url.replace('{to}', quote(to)).replace('{text}', quote(text))
            r = requests.request(method, full, timeout=TIMEOUT)
    except requests.RequestException as e:
        return 'failed', 'http', str(e)[:500]
    body = (r.text or '')[:500]
    ok_marker = _env('MESSAGING_HTTP_SUCCESS')
    ok = r.ok and (not ok_marker or ok_marker in body)
    return ('sent' if ok else 'failed'), 'http', f"{r.status_code} {body}"[:500]


def _twilio(channel, to, text):
    import requests
    sid, token = _env('TWILIO_ACCOUNT_SID'), _env('TWILIO_AUTH_TOKEN')
    sender = _env('TWILIO_WHATSAPP_FROM' if channel == 'whatsapp' else 'TWILIO_SMS_FROM')
    if not (sid and token and sender):
        return 'failed', 'twilio', 'Twilio credentials are not set'
    prefix = 'whatsapp:' if channel == 'whatsapp' else ''
    try:
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, token),
                          data={'From': prefix + sender, 'To': f"{prefix}+{to}", 'Body': text}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return 'failed', 'twilio', str(e)[:500]
    return ('sent' if r.ok else 'failed'), 'twilio', f"{r.status_code} {r.text[:400]}"


def _meta(to, text):
    import requests
    token, phone_id = _env('WHATSAPP_META_TOKEN'), _env('WHATSAPP_META_PHONE_ID')
    if not (token and phone_id):
        return 'failed', 'meta', 'WhatsApp Cloud API credentials are not set'
    try:
        r = requests.post(f"https://graph.facebook.com/v19.0/{phone_id}/messages",
                          headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT,
                          json={'messaging_product': 'whatsapp', 'to': to, 'type': 'text', 'text': {'body': text}})
    except requests.RequestException as e:
        return 'failed', 'meta', str(e)[:500]
    return ('sent' if r.ok else 'failed'), 'meta', f"{r.status_code} {r.text[:400]}"


def send(channel, to, text):
    """একটা বার্তা পাঠায়। to = normalize_bd() করা নম্বর।"""
    if channel == 'whatsapp':
        backend = _env('MESSAGING_WHATSAPP_BACKEND', 'console').lower()
        if backend == 'twilio':
            return _twilio('whatsapp', to, text)
        if backend == 'meta':
            return _meta(to, text)
        return _console('whatsapp', to, text)
    backend = _env('MESSAGING_SMS_BACKEND', 'console').lower()
    if backend == 'http':
        return _http(to, text)
    if backend == 'twilio':
        return _twilio('sms', to, text)
    return _console('sms', to, text)


def configured(channel):
    key = 'MESSAGING_WHATSAPP_BACKEND' if channel == 'whatsapp' else 'MESSAGING_SMS_BACKEND'
    return _env(key, 'console').lower() != 'console'
