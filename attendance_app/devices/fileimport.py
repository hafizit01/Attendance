"""
অফলাইন মেশিন: USB তে নেওয়া হাজিরা ফাইল ইমপোর্ট।
  • ZKTeco/eSSL `1_attlog.dat` / `attlog.dat`:  PIN \t 2026-11-18 09:01:02 \t state \t verify \t ...
  • যেকোনো CSV/TXT: ID, তারিখ-সময় (বা আলাদা তারিখ ও সময় কলাম) — হেডার লাইন নিজে থেকেই বাদ যায়
"""
import re
from datetime import datetime

from attendance_app.utils.zk_import import _to_int
from .ingest import Punch, method_from_text, method_from_zk

DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
    "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p",
)
MAX_BYTES = 20 * 1024 * 1024


def _dt(text):
    text = re.sub(r'\s+', ' ', (text or '').strip())
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse(content):
    """bytes/str → (Punch লিস্ট, বাদ পড়া লাইন)"""
    if isinstance(content, bytes):
        for enc in ('utf-8-sig', 'utf-16', 'latin-1'):
            try:
                content = content.decode(enc)
                if '\x00' not in content:
                    break
            except UnicodeDecodeError:
                continue
    punches, bad = [], 0
    for line in content.splitlines():
        if not line.strip():
            continue
        sep = '\t' if '\t' in line else (';' if line.count(';') > line.count(',') else ',')
        cols = [c.strip().strip('"') for c in line.split(sep)]
        uid = _to_int(cols[0]) if cols else None
        ts, rest = None, []
        if uid is not None and len(cols) > 1:
            ts = _dt(cols[1])
            rest = cols[2:]
            if ts is None and len(cols) > 2:          # তারিখ ও সময় আলাদা কলামে
                ts = _dt(f"{cols[1]} {cols[2]}")
                rest = cols[3:]
        if uid is None or ts is None:
            bad += 1
            continue
        verify = _to_int(rest[1]) if len(rest) > 1 else None
        text_method = next((method_from_text(c) for c in rest if method_from_text(c) != 'other'), None)
        method = text_method or (method_from_zk(verify) if verify is not None else 'other')
        punches.append(Punch(uid=uid, timestamp=ts, method=method, verify_code=verify,
                             state=_to_int(rest[0]) if rest else None))
    return punches, bad
