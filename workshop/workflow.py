"""
Job card workflow: the state machine and every operation that changes a job card.

All writes run inside one database transaction, so a failure (e.g. insufficient stock,
an unbalanced SAP posting) rolls back everything: status, audit row, stock, invoice.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import finance
from .models import (
    ZERO, DocumentSequence, HoldPeriod, Invoice, JobCard, JobCardItem, JobCardStatusLog, Part,
)

S = JobCard.Status
Stage = JobCardItem.Stage
ItemType = JobCardItem.ItemType

# The only legal moves. Anything not listed here is rejected by guard_transition().
ALLOWED_TRANSITIONS = {
    S.DRAFT: (S.APPROVED, S.CANCELLED),
    S.APPROVED: (S.IN_PROGRESS,),
    S.IN_PROGRESS: (S.ON_HOLD, S.COMPLETED),
    S.ON_HOLD: (S.IN_PROGRESS,),
    S.COMPLETED: (S.INVOICED,),
    S.INVOICED: (),
    S.CANCELLED: (),
}


class WorkflowError(Exception):
    """A business rule refused the requested action. The message is shown to the user."""


def allowed_targets(job) -> tuple:
    return ALLOWED_TRANSITIONS.get(job.status, ())


def guard_transition(job, to_status, note=""):
    """Raise WorkflowError unless `job` may move to `to_status`. Pure check, no writes."""
    if to_status not in allowed_targets(job):
        raise WorkflowError(f"Transition {job.status} -> {to_status} is not allowed.")
    if to_status == S.APPROVED and not job.items.filter(stage=Stage.ESTIMATE).exists():
        raise WorkflowError("Add at least one estimate line before approving.")
    if to_status in (S.ON_HOLD, S.CANCELLED) and not note.strip():
        raise WorkflowError("A reason is required to put a job on hold or cancel it.")
    if to_status == S.COMPLETED and not job.items.filter(stage=Stage.ACTUAL).exists():
        raise WorkflowError("Record the actual work (labour/parts/sublet) before completing.")


def _log(job, from_status, to_status, user, note=""):
    JobCardStatusLog.objects.create(
        job_card=job, from_status=from_status, to_status=to_status, changed_by=user, note=note
    )


@transaction.atomic
def create_job_card(*, branch, vehicle, complaint, user=None, odometer_km=None, parent=None) -> JobCard:
    job = JobCard.objects.create(
        branch=branch, customer=vehicle.customer, vehicle=vehicle, complaint=complaint,
        odometer_km=odometer_km, parent=parent, created_by=user,
    )
    note = f"Comeback of {parent.number}" if parent else "Job card opened"
    _log(job, "", S.DRAFT, user, note)
    return job


@transaction.atomic
def create_comeback(original, *, complaint, user=None) -> JobCard:
    """A returning fault opens a NEW job card linked to the original; the original is never edited."""
    if original.status != S.INVOICED:
        raise WorkflowError("Comebacks are raised against invoiced jobs. Use the open job card instead.")
    return create_job_card(
        branch=original.branch, vehicle=original.vehicle, complaint=complaint, user=user, parent=original
    )


@transaction.atomic
def transition(job, to_status, *, user=None, note="") -> JobCard:
    job = JobCard.objects.select_for_update().get(pk=job.pk)  # fresh row, locked
    guard_transition(job, to_status, note)
    from_status, now = job.status, timezone.now()

    if to_status == S.APPROVED:
        job.estimate_total = job.totals(Stage.ESTIMATE)["TOTAL"]  # locked from here on
        job.approved_at = now
    elif to_status == S.IN_PROGRESS:
        if from_status == S.ON_HOLD:
            job.holds.filter(left_at__isnull=True).update(left_at=now)
        else:
            job.started_at = now
    elif to_status == S.ON_HOLD:
        HoldPeriod.objects.create(job_card=job, entered_at=now, reason=note)
    elif to_status == S.COMPLETED:
        job.completed_at = now
    elif to_status == S.INVOICED:
        job.invoiced_at = now
    elif to_status == S.CANCELLED:
        job.cancelled_at = now

    job.status = to_status
    job._allow_status_change = True
    try:
        job.save()
    finally:
        job._allow_status_change = False
    _log(job, from_status, to_status, user, note)

    if to_status == S.INVOICED:
        invoice = _create_invoice(job, user, now)
        finance.get_finance_poster().post(finance.invoice_event(invoice))
    # CANCELLED / ON_HOLD / resume never post to SAP.
    return job


def _create_invoice(job, user, issued_at) -> Invoice:
    branch, customer = job.branch, job.customer
    items = list(job.items.filter(stage=Stage.ACTUAL))
    by_type = {t: sum((i.line_total for i in items if i.item_type == t), ZERO) for t in ItemType.values}
    goods_tax = sum((Invoice.tax_on(i.line_total, branch.goods_tax_rate) for i in items if i.is_goods), ZERO)
    services_tax = sum(
        (Invoice.tax_on(i.line_total, branch.services_tax_rate) for i in items if not i.is_goods), ZERO
    )
    total_excl = sum(by_type.values(), ZERO)
    return Invoice.objects.create(
        branch=branch,
        number=DocumentSequence.next_number(branch, "INV"),
        job_card=job,
        issued_at=issued_at,
        issued_by=user,
        seller_name=branch.legal_name,
        seller_address=branch.address,
        seller_phone=branch.phone,
        seller_ntn=branch.ntn,
        seller_strn=branch.strn,
        seller_provincial_tax_authority=branch.provincial_tax_authority,
        seller_provincial_tax_reg_no=branch.provincial_tax_reg_no,
        buyer_name=customer.name,
        buyer_address=customer.address,
        buyer_phone=customer.phone,
        buyer_ntn=customer.ntn,
        buyer_cnic=customer.cnic,
        vehicle_registration=job.vehicle.registration_no,
        labour_total=by_type[ItemType.LABOUR],
        parts_total=by_type[ItemType.PART],
        sublet_total=by_type[ItemType.SUBLET],
        total_excl_tax=total_excl,
        goods_tax_rate=branch.goods_tax_rate,
        services_tax_rate=branch.services_tax_rate,
        goods_tax_amount=goods_tax,
        services_tax_amount=services_tax,
        sales_tax_total=goods_tax + services_tax,
        grand_total=total_excl + goods_tax + services_tax,
    )


@transaction.atomic
def add_item(job, *, stage, item_type, user=None, labour_operation=None, part=None,
             description="", quantity=None, unit_price=None, unit_cost=None):
    """Add an estimate or actual line. An ACTUAL PART line consumes stock and posts COGS.
    Returns (item, warnings)."""
    job = JobCard.objects.select_for_update().get(pk=job.pk)
    item = JobCardItem(job_card=job, stage=stage, item_type=item_type, created_by=user)
    item.check_editable()
    warnings = []

    if item_type == ItemType.LABOUR:
        if labour_operation is None or labour_operation.branch_id != job.branch_id:
            raise WorkflowError("Choose a labour operation from this branch.")
        item.labour_operation = labour_operation
        item.description = description or labour_operation.name
        item.quantity = quantity or labour_operation.standard_hours
        item.unit_price = labour_operation.hourly_rate
    elif item_type == ItemType.PART:
        if part is None or part.branch_id != job.branch_id:
            raise WorkflowError("Choose a part from this branch.")
        if not quantity:
            raise WorkflowError("Enter a quantity.")
        part = Part.objects.select_for_update().get(pk=part.pk)
        item.part = part
        item.description = description or part.name
        item.quantity = quantity
        item.unit_price = part.unit_price
        item.unit_cost = part.unit_cost
        if stage == Stage.ACTUAL:
            if quantity > part.stock_on_hand:
                raise WorkflowError(
                    f"Only {part.stock_on_hand} {part.uom} of {part.part_no} in stock. "
                    "Put the job ON HOLD until the part arrives."
                )
            part.stock_on_hand -= quantity
            part.save(update_fields=["stock_on_hand"])
            if part.is_below_reorder:  # flag, never block
                item.below_reorder_flag = True
                warnings.append(
                    f"{part.part_no} is below its reorder level "
                    f"({part.stock_on_hand} left, reorder at {part.reorder_level})."
                )
    elif item_type == ItemType.SUBLET:
        if not description or unit_price is None:
            raise WorkflowError("A sublet line needs a description and a price.")
        item.description = description
        item.quantity = quantity or Decimal("1")
        item.unit_price = unit_price
        item.unit_cost = unit_cost or ZERO
    else:
        raise WorkflowError(f"Unknown item type {item_type}.")

    item.save()
    if item_type == ItemType.PART and stage == Stage.ACTUAL:
        event = finance.parts_event(item)
        if event:
            finance.get_finance_poster().post(event)
    return item, warnings


@transaction.atomic
def remove_item(item, *, user=None):
    """Remove a line. Removing a consumed part returns it to stock and posts a reversal."""
    item.check_editable()
    if item.item_type == ItemType.PART and item.stage == Stage.ACTUAL:
        part = Part.objects.select_for_update().get(pk=item.part_id)
        part.stock_on_hand += item.quantity
        part.save(update_fields=["stock_on_hand"])
        event = finance.parts_event(item, reverse=True)
        if event:
            finance.get_finance_poster().post(event)
    item.delete()
