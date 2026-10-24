from decimal import Decimal

from django import template

register = template.Library()

STATUS_CLASSES = {
    "DRAFT": "secondary",
    "APPROVED": "info",
    "IN_PROGRESS": "primary",
    "ON_HOLD": "warning",
    "COMPLETED": "success",
    "INVOICED": "dark",
    "CANCELLED": "danger",
}


@register.filter
def pkr(value):
    """1234.5 -> 'PKR 1,234.50'."""
    if value is None or value == "":
        return "—"
    return f"PKR {Decimal(value):,.2f}"


@register.filter
def amount(value):
    """1234.5 -> '1,234.50' (for table cells under a PKR heading)."""
    if value is None or value == "":
        return "—"
    return f"{Decimal(value):,.2f}"


@register.filter
def status_class(status):
    return STATUS_CLASSES.get(status, "secondary")


@register.filter
def duration(td):
    if td is None:
        return "—"
    hours, minutes = divmod(int(td.total_seconds() // 60), 60)
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
