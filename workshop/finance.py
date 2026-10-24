"""
SAP FI integration boundary.

The DMS does not own the general ledger. Business code builds a PostingEvent (balanced
debit/credit lines using *logical* account keys such as "AR" or "REVENUE_LABOUR") and
hands it to get_finance_poster().post(). Which adapter runs is decided only by
settings.DMS_FINANCE_POSTER. Today that is LoggingAdapter, which stores the payload in
SapPostingLog. A real OData/BAPI adapter subclasses FinancePoster and is named in
settings; no business code changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import ZERO, SapPostingLog, money


@dataclass
class PostingLine:
    account_key: str  # logical key, mapped to a GL number via settings.DMS_SAP_GL_ACCOUNTS
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    text: str = ""
    customer_no: str = ""  # only on the AR line (SAP customer sub-ledger)


@dataclass
class PostingEvent:
    event_type: str
    idempotency_key: str
    branch: object
    reference: str
    header_text: str
    lines: list
    job_card: object = None
    document_date: date = field(default_factory=timezone.localdate)

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), ZERO)

    def validate(self):
        if not self.lines:
            raise ValueError(f"Posting {self.idempotency_key} has no lines.")
        if self.total_debit != self.total_credit:
            raise ValueError(
                f"Posting {self.idempotency_key} is unbalanced: Dr {self.total_debit} / Cr {self.total_credit}."
            )

    def to_payload(self) -> dict:
        """Adapter-neutral JSON payload, shaped like an SAP accounting document."""
        accounts = settings.DMS_SAP_GL_ACCOUNTS
        return {
            "header": {
                "company_code": settings.DMS_SAP_COMPANY_CODE,
                "document_type": settings.DMS_SAP_DOC_TYPES[self.event_type],
                "document_date": self.document_date.isoformat(),
                "posting_date": self.document_date.isoformat(),
                "currency": settings.DMS_CURRENCY,
                "reference": self.reference,
                "header_text": self.header_text,
            },
            "lines": [
                {
                    "line_no": n,
                    "account_key": line.account_key,
                    "gl_account": accounts[line.account_key],
                    "customer_no": line.customer_no,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                    "profit_center": self.branch.sap_profit_center,
                    "text": line.text,
                }
                for n, line in enumerate(self.lines, start=1)
            ],
            "source": {
                "system": "DMS",
                "branch": self.branch.code,
                "event_type": self.event_type,
                "idempotency_key": self.idempotency_key,
                "job_card": self.job_card.number if self.job_card else "",
            },
        }


class FinancePoster(ABC):
    """Interface every SAP adapter implements."""

    @abstractmethod
    def post(self, event: PostingEvent) -> str:
        """Post one balanced event; return a reference. Must be idempotent on event.idempotency_key."""


class LoggingAdapter(FinancePoster):
    """Stub adapter: records the payload in SapPostingLog instead of calling SAP."""

    def post(self, event: PostingEvent) -> str:
        event.validate()
        existing = SapPostingLog.objects.filter(idempotency_key=event.idempotency_key).first()
        if existing:
            return f"LOG-{existing.pk}"
        log = SapPostingLog.objects.create(
            branch=event.branch,
            job_card=event.job_card,
            event_type=event.event_type,
            idempotency_key=event.idempotency_key,
            reference=event.reference,
            adapter=type(self).__name__,
            payload=event.to_payload(),
            total_debit=event.total_debit,
            total_credit=event.total_credit,
        )
        return f"LOG-{log.pk}"


def get_finance_poster() -> FinancePoster:
    return import_string(settings.DMS_FINANCE_POSTER)()


# --- Event builders (adapter-agnostic) ------------------------------------------

def invoice_event(invoice) -> PostingEvent:
    """One event per invoice: Dr AR (gross); Cr revenue by type; Cr output sales tax."""
    customer = invoice.job_card.customer
    lines = [
        PostingLine(
            "AR", debit=invoice.grand_total, text=f"Invoice {invoice.number}",
            customer_no=customer.sap_customer_no or settings.DMS_SAP_ONE_TIME_CUSTOMER,
        )
    ]
    credits = [
        ("REVENUE_LABOUR", invoice.labour_total, "Labour revenue"),
        ("REVENUE_PARTS", invoice.parts_total, "Parts revenue"),
        ("REVENUE_SUBLET", invoice.sublet_total, "Sublet revenue"),
        ("OUTPUT_TAX_GOODS", invoice.goods_tax_amount, f"Output sales tax on goods @ {invoice.goods_tax_rate}%"),
        ("OUTPUT_TAX_SERVICES", invoice.services_tax_amount,
         f"Output sales tax on services @ {invoice.services_tax_rate}%"),
    ]
    lines += [PostingLine(key, credit=amount, text=text) for key, amount, text in credits if amount]
    return PostingEvent(
        event_type=SapPostingLog.EventType.INVOICE,
        idempotency_key=f"INVOICE:{invoice.number}",
        branch=invoice.branch,
        job_card=invoice.job_card,
        reference=invoice.number,
        header_text=f"Workshop invoice {invoice.number} / {invoice.job_card.number}",
        document_date=timezone.localdate(invoice.issued_at),
        lines=lines,
    )


def parts_event(item, reverse=False):
    """Parts issue: Dr COGS / Cr Inventory at cost. reverse=True for a return to stock.
    Returns None for zero-cost parts (nothing to post)."""
    amount = money(item.quantity * item.unit_cost)
    if not amount:
        return None
    text = f"{item.quantity} x {item.description}"
    if reverse:
        event_type, key = SapPostingLog.EventType.PARTS_RETURN, f"PARTS_RETURN:{item.pk}"
        lines = [PostingLine("INVENTORY_PARTS", debit=amount, text=text), PostingLine("COGS_PARTS", credit=amount, text=text)]
    else:
        event_type, key = SapPostingLog.EventType.PARTS_ISSUE, f"PARTS_ISSUE:{item.pk}"
        lines = [PostingLine("COGS_PARTS", debit=amount, text=text), PostingLine("INVENTORY_PARTS", credit=amount, text=text)]
    return PostingEvent(
        event_type=event_type,
        idempotency_key=key,
        branch=item.job_card.branch,
        job_card=item.job_card,
        reference=item.job_card.number,
        header_text=f"Parts {'return' if reverse else 'issue'} {item.job_card.number}",
        lines=lines,
    )
