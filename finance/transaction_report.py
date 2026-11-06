#!/usr/bin/env python3
"""
Parser for QuickBooks "Transaction Detail - Camel" workbooks.

QuickBooks emails two of these alongside the weekly P&L - "Camel Weekly
Transaction Report" (the past week) and "Camel YTD Transactions" - and Hillel's
roll-forward report has one sheet per fiscal year ("FY26 Detail", "FY27
Detail"). Every one is laid out like:

    Hillel Foundation of Cambridge
    Transaction Detail - Camel
    October 18-24, 2026
       | Account Name | Transaction date | Transaction type | Num | Name | Description | Amount | A/P paid
    4200 Restricted Contributions
      | 4200 ...:425040 The Camel | 10/19/2026 | Deposit | | <payer> | <memo> | <amount> |
    Total for 425040 The Camel                                          <total>
    TOTAL                                                               <total>
    Accrual Basis Sunday, October 25, 2026 09:00 AM GMT-04:00

A report restates every transaction dated in its period, so a newer report
replaces an older one wherever their periods overlap (see the camel_ledger
view). The footer timestamp is when QuickBooks ran the report, which is what
"newer" means.
"""

import io
import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

import openpyxl

from pl_report import _PERIOD, CENT, ReportFormatError, _amount

TITLE_MARKER = 'transaction detail'

# Month-only periods: "July-September, 2026", "July, 2025-June, 2026", "October, 2026"
_MONTH_RANGE = re.compile(r'^([A-Z][a-z]+),? (\d{4})?\s*[-–]\s*([A-Z][a-z]+),? (\d{4})$|'
                          r'^([A-Z][a-z]+)\s*[-–]\s*([A-Z][a-z]+),? (\d{4})$')
_ONE_MONTH = re.compile(r'^([A-Z][a-z]+),? (\d{4})$')

# "Accrual Basis Sunday, October 25, 2026 09:00 AM GMT-04:00"
_GENERATED = re.compile(r'([A-Z][a-z]+ \d{1,2}, \d{4}) (\d{1,2}:\d{2} [AP]M) GMT([+-])(\d{2}):?(\d{2})')

COLUMNS = {
    'account name': 'account', 'transaction date': 'date', 'transaction type': 'type',
    'num': 'num', 'name': 'name', 'description': 'description', 'amount': 'amount',
    'a/p paid': 'ap_paid',
}


@dataclass
class TransactionLine:
    txn_date: date
    account: str        # leaf account: "610030 Food and Beverage"
    section: str        # 'income', 'expense', or 'balance' (balance-sheet accounts, e.g. prepaid)
    txn_type: str
    num: Optional[str]
    name: Optional[str]
    description: Optional[str]
    amount: Decimal     # as QuickBooks shows it: spending and deposits positive, refunds negative
    ap_paid: Optional[str]


@dataclass
class TransactionReport:
    period_start: date
    as_of: date
    generated_at: Optional[datetime]
    lines: List[TransactionLine] = field(default_factory=list)


def section_of(account_path: str) -> str:
    """
    Hillel's chart of accounts: 1xxx-3xxx balance sheet, 4xxx income, the rest
    expenses - except income parked among them, like "7030 Income - Other:703010
    Realized Gain/Loss on Investmnt", so the full path is checked for that too.
    """
    code = account_path.split(' ', 1)[0]
    if re.search(r'\b(income|revenue)\b', account_path, re.I) or code[:1] == '4':
        return 'income'
    return 'balance' if code.isdigit() and code[0] in '123' else 'expense'


def parse_transaction_workbook(data: bytes) -> Optional[TransactionReport]:
    """
    Every Transaction Detail sheet in the workbook, as one report. Returns None
    if there is none (e.g. a P&L). Raises ReportFormatError if one cannot be
    read, or if several sheets leave a gap between their periods - a gap would
    claim days the workbook says nothing about.
    """
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    sheets = [_parse_sheet(ws) for ws in wb.worksheets if _is_detail_sheet(ws)]
    if not sheets:
        return None
    sheets.sort(key=lambda s: s.period_start)
    for before, after in zip(sheets, sheets[1:]):
        if after.period_start > before.as_of + timedelta(days=1):
            raise ReportFormatError(f"detail sheets cover {before.period_start}-{before.as_of} and "
                                    f"{after.period_start}-{after.as_of}, with a gap between them")
    stamps = [s.generated_at for s in sheets if s.generated_at]
    return TransactionReport(
        period_start=sheets[0].period_start,
        as_of=max(s.as_of for s in sheets),
        generated_at=max(stamps) if stamps else None,
        lines=[line for s in sheets for line in s.lines],
    )


def _is_detail_sheet(ws) -> bool:
    for row in ws.iter_rows(min_row=1, max_row=6, max_col=3, values_only=True):
        if any(isinstance(v, str) and TITLE_MARKER in v.lower() for v in row):
            return True
    return False


def _parse_sheet(ws) -> TransactionReport:
    period_start, as_of = _parse_period(ws)
    header_row, cols = _find_header(ws)

    lines, generated_at = [], None
    for r in range(header_row + 1, ws.max_row + 1):
        first = ws.cell(row=r, column=1).value
        if isinstance(first, str) and _GENERATED.search(first):
            generated_at = _parse_generated(first)
            continue
        txn_date = _date(ws.cell(row=r, column=cols['date']).value)
        if txn_date is None:
            continue  # account headings, "Total for ..." and TOTAL rows

        def text(key):
            if key not in cols:
                return None
            value = ws.cell(row=r, column=cols[key]).value
            if value is None:
                return None
            return str(value).strip() or None

        path = text('account') or ''
        account = path.split(':')[-1].strip()
        if not account:
            raise ReportFormatError(f"sheet '{ws.title}' row {r}: transaction with no account")
        lines.append(TransactionLine(
            txn_date=txn_date,
            account=account,
            section=section_of(path),
            txn_type=text('type') or '',
            num=text('num'),
            name=text('name'),
            description=text('description'),
            amount=_amount(ws, ws.cell(row=r, column=cols['amount']).value).quantize(CENT),
            ap_paid=text('ap_paid'),
        ))
        if not period_start <= txn_date <= as_of:
            raise ReportFormatError(f"sheet '{ws.title}' row {r}: {txn_date} is outside the "
                                    f"report period {period_start}-{as_of}")
    return TransactionReport(period_start, as_of, generated_at, lines)


def _parse_period(ws):
    for row in ws.iter_rows(min_row=1, max_row=6, max_col=3, values_only=True):
        for value in row:
            if not isinstance(value, str):
                continue
            value = value.strip()
            m = _PERIOD.search(value)
            if m:
                start_month, start_day, start_year, end_month, end_day, end_year = m.groups()
                end = datetime.strptime(f"{end_month or start_month} {end_day} {end_year}", '%B %d %Y').date()
                start = datetime.strptime(f"{start_month} {start_day} {start_year or end.year}",
                                          '%B %d %Y').date()
                if start > end:  # "December 28-January 3, 2027"
                    start = start.replace(year=start.year - 1)
                return start, end
            m = _MONTH_RANGE.match(value)
            if m:
                g = m.groups()
                start_month, start_year, end_month, end_year = (
                    (g[0], g[1], g[2], g[3]) if g[0] else (g[4], None, g[5], g[6]))
                return _month_span(start_month, start_year or end_year, end_month, end_year)
            m = _ONE_MONTH.match(value)
            if m:
                return _month_span(m.group(1), m.group(2), m.group(1), m.group(2))
    raise ReportFormatError(f"sheet '{ws.title}': no reporting period like 'October 18-24, 2026'")


def _month_span(start_month, start_year, end_month, end_year):
    start = datetime.strptime(f"{start_month} 1 {start_year}", '%B %d %Y').date()
    end = datetime.strptime(f"{end_month} 1 {end_year}", '%B %d %Y').date()
    if start > end:
        start = start.replace(year=start.year - 1)
    return start, end.replace(day=monthrange(end.year, end.month)[1])


def _find_header(ws):
    for r in range(1, min(ws.max_row, 12) + 1):
        cols = {}
        for c in range(1, ws.max_column + 1):
            value = ws.cell(row=r, column=c).value
            if isinstance(value, str) and value.strip().lower() in COLUMNS:
                cols[COLUMNS[value.strip().lower()]] = c
        if 'date' in cols and 'amount' in cols:
            return r, cols
    raise ReportFormatError(f"sheet '{ws.title}': no header row with 'Transaction date' and 'Amount'")


def _date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), '%m/%d/%Y').date()
        except ValueError:
            return None
    return None


def _parse_generated(text: str) -> Optional[datetime]:
    m = _GENERATED.search(text)
    if not m:
        return None
    day_text, time_text, sign, hours, minutes = m.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    tz = timezone(offset if sign == '+' else -offset)
    return datetime.strptime(f"{day_text} {time_text}", '%B %d, %Y %I:%M %p').replace(tzinfo=tz)


if __name__ == '__main__':
    import sys
    for path in sys.argv[1:]:
        with open(path, 'rb') as fh:
            report = parse_transaction_workbook(fh.read())
        if report is None:
            print(f"{path}: no Transaction Detail sheet")
            continue
        by_section = {}
        for line in report.lines:
            by_section[line.section] = by_section.get(line.section, Decimal('0')) + line.amount
        print(f"{path}: {report.period_start} -> {report.as_of}, run {report.generated_at}, "
              f"{len(report.lines)} lines, " + ', '.join(f"{k} {v}" for k, v in sorted(by_section.items())))
