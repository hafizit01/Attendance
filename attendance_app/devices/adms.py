"""
ZKTeco ADMS / "Cloud Server" push প্রোটোকল (iclock)।

মেশিনে: Menu → Comm. → Cloud Server Setting (বা ADMS)
    Server address = আপনার ডোমেইন / IP,  Port = 80 বা 443 (HTTPS),  Proxy = Off
তারপর মেশিন নিজেই:
    GET  /iclock/cdata?SN=..&options=all     → সার্ভার কনফিগ পাঠায় (কোন ডাটা কখন পাঠাবে)
    POST /iclock/cdata?SN=..&table=ATTLOG    → পাঞ্চ (প্রতি লাইনে: PIN \t সময় \t state \t verify \t ...)
    POST /iclock/cdata?SN=..&table=OPERLOG   → ইউজার যোগ/বদল (USER PIN=1\tName=...)
    GET  /iclock/getrequest?SN=..            → সার্ভারের কমান্ড নেয় (C:<id>:<command>)
    POST /iclock/devicecmd?SN=..             → কমান্ডের ফলাফল (ID=..&Return=..&CMD=..)

মেশিন সার্ভারের HTTP `Date` হেডার দেখে নিজের ঘড়িও মিলিয়ে নেয়।
"""
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qsl

from attendance_app.utils.zk_import import _to_int
from .ingest import Punch, method_from_zk

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S")


def _parse_time(text):
    text = (text or '').strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _lines(body):
    if isinstance(body, bytes):
        for enc in ('utf-8', 'gb18030', 'latin-1'):
            try:
                body = body.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    return [ln for ln in body.replace('\r\n', '\n').replace('\r', '\n').split('\n') if ln.strip()]


def parse_attlog(body):
    """ATTLOG: `PIN\\tYYYY-MM-DD HH:MM:SS\\tstate\\tverify\\tworkcode...` → (Punch লিস্ট, বাদ পড়া)"""
    punches, bad = [], 0
    for line in _lines(body):
        parts = [p.strip() for p in line.split('\t')]
        uid = _to_int(parts[0]) if parts else None
        ts = _parse_time(parts[1]) if len(parts) > 1 else None
        if uid is None or ts is None:
            bad += 1
            continue
        state = _to_int(parts[2]) if len(parts) > 2 else None
        verify = _to_int(parts[3]) if len(parts) > 3 else None
        punches.append(Punch(uid=uid, timestamp=ts, method=method_from_zk(verify), verify_code=verify, state=state))
    return punches, bad


def _kv(text):
    """`PIN=1\\tName=Rahim\\tPri=0` → dict"""
    out = {}
    for part in text.split('\t'):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def parse_users(body):
    """
    OPERLOG / USERINFO টেবিল থেকে ইউজার। রিটার্ন: (ইউজার লিস্ট [obj(user_id, name)], মোট লাইন)
    FP/FACE/BIODATA/OPLOG লাইন গোনা হয় (মেশিনকে OK: n জানাতে) কিন্তু সংরক্ষণ করা হয় না।
    """
    users, total = [], 0
    for line in _lines(body):
        total += 1
        head, _, rest = line.partition(' ')
        if head == 'USER':
            data = _kv(rest)
        elif head.startswith('PIN='):
            data = _kv(line)
        else:
            continue
        uid = _to_int(data.get('PIN'))
        if uid is not None:
            users.append(SimpleNamespace(user_id=uid, name=data.get('Name', '')))
    return users, total


def parse_info(info):
    """getrequest এর INFO=ver,users,fp,att,ip,fpver,facever,faceenroll,faces,... """
    parts = [p.strip() for p in (info or '').split(',')]
    get = lambda i: parts[i] if len(parts) > i else ''
    out = {}
    if get(0):
        out['firmware'] = get(0)[:100]
    for key, idx in (('user_count', 1), ('finger_count', 2), ('record_count', 3), ('face_count', 8)):
        v = _to_int(get(idx))
        if v is not None:
            out[key] = v
    return out


def parse_command_results(body):
    """devicecmd: প্রতি লাইনে `ID=12&Return=0&CMD=DATA` → [(id, return_code)]"""
    out = []
    for line in _lines(body):
        data = dict(parse_qsl(line.strip(), keep_blank_values=True))
        cid = _to_int(data.get('ID'))
        if cid is not None:
            out.append((cid, data.get('Return', '')))
    return out


def options_response(device):
    """GET /iclock/cdata?options=all এর উত্তর"""
    stamp = device.adms_att_stamp or 'None'
    op = device.adms_op_stamp or 'None'
    return "\n".join([
        f"GET OPTION FROM: {device.serial_number}",
        f"ATTLOGStamp={stamp}",
        f"OPERLOGStamp={op}",
        "ATTPHOTOStamp=None",
        "ErrorDelay=30",
        "Delay=10",
        "TransTimes=00:00;14:05",
        "TransInterval=1",
        "TransFlag=TransData AttLog OpLog EnrollUser ChgUser",
        "TimeZone=6",
        "Realtime=1",
        "Encrypt=None",
        "",
    ])


# ---------- কমান্ড (DeviceCommand.command এ যা যায়) ----------
def cmd_upsert_user(emp):
    name = (emp.name or '').replace('\t', ' ')[:24]
    return f"DATA UPDATE USERINFO PIN={emp.device_user_id}\tName={name}\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000"


def cmd_delete_user(pin):
    return f"DATA DELETE USERINFO PIN={pin}"


def cmd_query_attlog(start, end):
    return f"DATA QUERY ATTLOG StartTime={start:%Y-%m-%d %H:%M:%S}\tEndTime={end:%Y-%m-%d %H:%M:%S}"


CMD_REBOOT = "REBOOT"
CMD_INFO = "INFO"
CMD_CHECK = "CHECK"
