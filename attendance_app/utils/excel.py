"""
সব রিপোর্টের Excel (.xlsx) এক্সপোর্ট — openpyxl দিয়ে।

ব্যবহার:
    cols = [Col('Employee', 'name', width=24), Col('Salary', 'salary', kind='money')]
    return excel_response('Salary_2026-11', [Sheet('Salary', cols, rows, title='Salary sheet', meta=[('Month', 'Nov 2026')],
                                                    totals={'salary': 1234})])

Col.value: dict key, অথবা row → value ফাংশন। kind: text | int | money | decimal | duration | date | datetime | time | percent
"""
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any, Callable, List, Optional, Union

from django.http import HttpResponse
from django.utils import timezone

BRAND = "4F46E5"
BRAND_DARK = "312E81"
HEADER_FONT_COLOR = "FFFFFF"
ZEBRA = "F5F7FF"
BORDER = "D9DEEA"
TOTAL_FILL = "EEF2FF"

NUMBER_FORMATS = {
    'int': '#,##0',
    'money': '#,##0.00',
    'decimal': '#,##0.00',
    'percent': '0%',
    'date': 'DD-MMM-YYYY',
    'datetime': 'DD-MMM-YYYY HH:MM',
    'time': 'HH:MM',
    'duration': '[h]:mm',
}


@dataclass
class Col:
    header: str
    value: Union[str, Callable[[Any], Any]]
    kind: str = 'text'
    width: Optional[float] = None


@dataclass
class Sheet:
    name: str
    columns: List[Col]
    rows: List[Any]
    title: str = ''
    meta: List[tuple] = field(default_factory=list)       # [(label, value)] — শিরোনামের নিচে
    totals: Optional[dict] = None                          # {col.value key: total} — শেষ সারি
    totals_label: str = 'Total'
    notes: List[str] = field(default_factory=list)


def _cell_value(col, row):
    v = col.value(row) if callable(col.value) else (row.get(col.value) if isinstance(row, dict) else getattr(row, col.value, None))
    return _normalize(v, col.kind)


def _normalize(v, kind):
    if v is None or v == '':
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        if timezone.is_aware(v):
            v = timezone.localtime(v)
        v = v.replace(tzinfo=None)
        return v.time() if kind == 'time' else v
    if isinstance(v, timedelta):
        return v.total_seconds() / 86400 if kind == 'duration' else v.total_seconds() / 3600
    if kind == 'duration' and isinstance(v, str) and ':' in v:   # 'HH:MM' বা 'HH:MM:SS' স্ট্রিং
        neg = v.startswith('-')
        parts = [int(p) for p in v.lstrip('+-').split(':')]
        secs = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
        return (-secs if neg else secs) / 86400
    return v


def build_workbook(sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style='thin', color=BORDER)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor=BRAND)
    zebra = PatternFill('solid', fgColor=ZEBRA)
    total_fill = PatternFill('solid', fgColor=TOTAL_FILL)

    for sh in sheets:
        ws = wb.create_sheet(title=(sh.name or 'Sheet')[:31])
        ncols = max(len(sh.columns), 1)
        r = 1
        if sh.title:
            ws.cell(row=r, column=1, value=sh.title).font = Font(size=15, bold=True, color=BRAND_DARK)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
            ws.row_dimensions[r].height = 24
            r += 1
        meta = list(sh.meta) + [('Generated', timezone.localtime().strftime('%d %b %Y, %I:%M %p'))]
        for label, value in meta:
            ws.cell(row=r, column=1, value=f"{label}:").font = Font(bold=True, color="64748B")
            c = ws.cell(row=r, column=2, value=value)
            c.font = Font(color="334155")
            if ncols > 2:
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=ncols)
            r += 1
        r += 1

        header_row = r
        for i, col in enumerate(sh.columns, start=1):
            c = ws.cell(row=r, column=i, value=col.header)
            c.font = Font(bold=True, color=HEADER_FONT_COLOR)
            c.fill = head_fill
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = border
        ws.row_dimensions[r].height = 30
        r += 1

        widths = [len(str(c.header)) + 2 for c in sh.columns]
        # প্রতিটা (ধরন, জেব্রা) এর স্টাইল একবারই বানিয়ে বাকি ঘরে কপি — হুবহু একই ফাইল, কিন্তু
        # হাজার সারিতে প্রতি ঘরে আলাদা করে font/border/fill বসানোর চেয়ে বহু গুণ দ্রুত
        protos = {}
        for n, row in enumerate(sh.rows):
            for i, col in enumerate(sh.columns, start=1):
                v = _cell_value(col, row)
                c = ws.cell(row=r, column=i, value=v)
                key = (col.kind, n % 2)
                proto = protos.get(key)
                if proto is not None:
                    c._style = copy(proto)
                else:
                    c.border = border
                    if col.kind in NUMBER_FORMATS:
                        c.number_format = NUMBER_FORMATS[col.kind]
                        c.alignment = Alignment(horizontal='right' if col.kind not in ('date', 'datetime', 'time') else 'center')
                    if n % 2:
                        c.fill = zebra
                    protos[key] = copy(c._style)
                shown = (c.number_format and isinstance(v, (int, float)) and f"{v:,.2f}") or (
                    v.strftime('%d-%b-%Y %H:%M') if isinstance(v, datetime) else str(v if v is not None else ''))
                widths[i - 1] = max(widths[i - 1], min(len(shown) + 2, 60))
            r += 1

        if sh.totals:
            for i, col in enumerate(sh.columns, start=1):
                key = col.value if isinstance(col.value, str) else col.header
                v = sh.totals_label if i == 1 else _normalize(sh.totals.get(key), col.kind)
                c = ws.cell(row=r, column=i, value=v)
                c.font = Font(bold=True, color=BRAND_DARK)
                c.fill = total_fill
                c.border = border
                if i > 1 and col.kind in NUMBER_FORMATS:
                    c.number_format = NUMBER_FORMATS[col.kind]
            r += 1

        for note in sh.notes:
            r += 1
            ws.cell(row=r, column=1, value=note).font = Font(italic=True, color="64748B")

        for i, col in enumerate(sh.columns, start=1):
            ws.column_dimensions[get_column_letter(i)].width = col.width or max(10, min(widths[i - 1], 45))
        ws.freeze_panes = ws.cell(row=header_row + 1, column=2 if ncols > 3 else 1)
        if sh.rows:
            ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncols)}{header_row + len(sh.rows)}"
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{header_row}:{header_row}"
    return wb


def excel_response(filename, sheets):
    wb = build_workbook(sheets)
    buf = BytesIO()
    wb.save(buf)
    safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in filename) or 'report'
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{safe}.xlsx"'
    return resp
