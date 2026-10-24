"""
Data model for the workshop DMS.

Rules that must hold no matter how a record is touched (our views, the admin, the
Django shell) are enforced here in save()/delete():
  * an INVOICED (or CANCELLED) job card is read-only forever;
  * the estimate total is locked once approved;
  * status only changes through workflow.transition(), which writes the audit log;
  * audit rows and invoices are append-only.
QuerySet.update()/bulk_create() skip save(); the app never uses them on these models.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

ZERO = Decimal("0.00")


def money(value) -> Decimal:
    """Round to 2 decimals (rupees and paisa), half-up."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ImmutableRecordError(Exception):
    """Raised when code tries to change a record that the business rules freeze."""


# --- Organisation ------------------------------------------------------------

class Branch(models.Model):
    code = models.CharField(max_length=10, unique=True, help_text="Used in document numbers, e.g. LHR01.")
    name = models.CharField(max_length=120)
    # Seller details printed on the tax invoice. PLACEHOLDERS until verified (see README NOTES).
    legal_name = models.CharField(max_length=200)
    address = models.TextField()
    phone = models.CharField(max_length=30, blank=True)
    ntn = models.CharField("NTN", max_length=20, blank=True)
    strn = models.CharField("STRN", max_length=20, blank=True, help_text="FBR sales tax registration number.")
    provincial_tax_authority = models.CharField(
        max_length=20, blank=True, help_text="Authority for sales tax on services, e.g. PRA, SRB, KPRA, BRA."
    )
    provincial_tax_reg_no = models.CharField(max_length=30, blank=True)
    goods_tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("18.00"),
        help_text="% sales tax on goods (parts). PLACEHOLDER: verify current rate.",
    )
    services_tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("16.00"),
        help_text="% sales tax on services (labour, sublet). Depends on province. PLACEHOLDER: verify.",
    )
    sap_profit_center = models.CharField("SAP profit center", max_length=10, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "branches"

    def __str__(self):
        return f"{self.code} — {self.name}"


class BranchScoped(models.Model):
    """Every core record carries the branch it belongs to."""

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class DocumentSequence(models.Model):
    """Gap-free, per-branch document counters (job cards, invoices)."""

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    key = models.CharField(max_length=10)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("branch", "key")]

    def __str__(self):
        return f"{self.branch.code}-{self.key}: {self.last_value}"

    @classmethod
    def next_number(cls, branch, key) -> str:
        # select_for_update serialises concurrent callers on Postgres; SQLite locks the
        # whole database on write. Called inside the caller's transaction, so a rollback
        # also rolls the counter back and no number is skipped.
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(branch=branch, key=key)
            seq.last_value += 1
            seq.save(update_fields=["last_value"])
        return f"{branch.code}-{key}-{seq.last_value:06d}"


# --- Customers & vehicles ----------------------------------------------------

class Customer(BranchScoped):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    cnic = models.CharField("CNIC", max_length=15, blank=True, help_text="Optional. Format 12345-1234567-1.")
    ntn = models.CharField("NTN", max_length=20, blank=True, help_text="Optional; for registered buyers.")
    sap_customer_no = models.CharField(
        "SAP customer no.", max_length=10, blank=True,
        help_text="Leave blank to post to the one-time customer account.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Vehicle(BranchScoped):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="vehicles")
    registration_no = models.CharField(max_length=20, unique=True)
    vin = models.CharField("VIN / chassis no.", max_length=30, unique=True, null=True, blank=True)
    make = models.CharField(max_length=50)
    model = models.CharField(max_length=80)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    colour = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["registration_no"]

    def __str__(self):
        return f"{self.registration_no} — {self.make} {self.model} ({self.customer.name})"

    def save(self, *args, **kwargs):
        self.registration_no = self.registration_no.strip().upper()
        self.vin = (self.vin or "").strip().upper() or None  # NULL, not "", so unique allows many blanks
        super().save(*args, **kwargs)


# --- Catalogue -----------------------------------------------------------------

class Part(BranchScoped):
    part_no = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    uom = models.CharField("Unit", max_length=10, default="PCS")
    hs_code = models.CharField("HS / PCT code", max_length=12, blank=True, help_text="PLACEHOLDER: verify per part.")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, help_text="PKR, used for COGS.")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="PKR selling price, excl. tax.")
    stock_on_hand = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["part_no"]
        unique_together = [("branch", "part_no")]

    def __str__(self):
        return f"{self.part_no} — {self.name}"

    @property
    def is_below_reorder(self) -> bool:
        return self.stock_on_hand < self.reorder_level


class LabourOperation(BranchScoped):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    standard_hours = models.DecimalField(max_digits=6, decimal_places=2)
    hourly_rate = models.DecimalField(max_digits=12, decimal_places=2, help_text="PKR per hour, excl. tax.")

    class Meta:
        ordering = ["code"]
        unique_together = [("branch", "code")]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def standard_charge(self) -> Decimal:
        return money(self.standard_hours * self.hourly_rate)


# --- Job card (the central document) -------------------------------------------

class JobCard(BranchScoped):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        ON_HOLD = "ON_HOLD", "On hold"
        COMPLETED = "COMPLETED", "Completed"
        INVOICED = "INVOICED", "Invoiced"
        CANCELLED = "CANCELLED", "Cancelled"

    LOCKED_STATUSES = (Status.INVOICED, Status.CANCELLED)

    number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="job_cards")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="job_cards")
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="comebacks",
        help_text="Set on a comeback: the original job card this fault returned from.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, editable=False)
    complaint = models.TextField("Customer complaint / work requested")
    odometer_km = models.PositiveIntegerField(null=True, blank=True)
    estimate_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Locked at approval (excl. tax).",
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    invoiced_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    # Only workflow.transition() flips this, so status can never change silently.
    _allow_status_change = False

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("workshop:job_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.status != self.Status.DRAFT:
                raise ImmutableRecordError("A new job card must start in DRAFT.")
            if not self.number:
                self.number = DocumentSequence.next_number(self.branch, "JC")
        else:
            previous = JobCard.objects.only("status", "estimate_total").get(pk=self.pk)
            if previous.status in self.LOCKED_STATUSES:
                raise ImmutableRecordError(f"{self.number} is {previous.get_status_display()} and read-only.")
            if previous.status != self.status and not self._allow_status_change:
                raise ImmutableRecordError("Status may only change through the workflow (audit trail).")
            if previous.estimate_total is not None and previous.estimate_total != self.estimate_total:
                raise ImmutableRecordError("The estimate total is locked once approved.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Job cards are never deleted. Cancel a draft instead.")

    @property
    def is_read_only(self) -> bool:
        return self.status in self.LOCKED_STATUSES

    def totals(self, stage) -> dict:
        """Line totals for one stage: {'LABOUR': .., 'PART': .., 'SUBLET': .., 'TOTAL': ..}."""
        result = {item_type: ZERO for item_type in JobCardItem.ItemType.values}
        rows = self.items.filter(stage=stage).values("item_type").annotate(amount=Sum("line_total"))
        for row in rows:
            result[row["item_type"]] = row["amount"]
        result["TOTAL"] = sum(result.values(), ZERO)
        return result

    def variance_breakdown(self) -> list:
        """Estimate vs actual per item type, plus the total (uses the locked estimate total)."""
        estimate = self.totals(JobCardItem.Stage.ESTIMATE)
        actual = self.totals(JobCardItem.Stage.ACTUAL)
        estimate["TOTAL"] = self.estimate_total if self.estimate_total is not None else estimate["TOTAL"]
        rows = []
        for key, label in [("LABOUR", "Labour"), ("PART", "Parts"), ("SUBLET", "Sublet"), ("TOTAL", "Total")]:
            diff = actual[key] - estimate[key]
            pct = (diff / estimate[key] * 100).quantize(Decimal("0.1")) if estimate[key] else None
            rows.append({"label": label, "estimate": estimate[key], "actual": actual[key], "variance": diff, "pct": pct})
        return rows

    @property
    def idle_time(self) -> timedelta:
        """Total time spent ON_HOLD (idle bay time), including a hold that is still open."""
        return sum((hold.duration for hold in self.holds.all()), timedelta())


class JobCardItem(BranchScoped):
    """One line on a job card. ESTIMATE lines are quoted in DRAFT; ACTUAL lines are recorded
    while IN_PROGRESS (an ACTUAL PART line is a stock consumption)."""

    class Stage(models.TextChoices):
        ESTIMATE = "ESTIMATE", "Estimate"
        ACTUAL = "ACTUAL", "Actual"

    class ItemType(models.TextChoices):
        LABOUR = "LABOUR", "Labour"
        PART = "PART", "Part"
        SUBLET = "SUBLET", "Sublet"

    job_card = models.ForeignKey(JobCard, on_delete=models.PROTECT, related_name="items")
    stage = models.CharField(max_length=10, choices=Stage.choices)
    item_type = models.CharField(max_length=10, choices=ItemType.choices)
    description = models.CharField(max_length=255)
    labour_operation = models.ForeignKey(LabourOperation, on_delete=models.PROTECT, null=True, blank=True)
    part = models.ForeignKey(Part, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, help_text="Hours for labour, units for parts.")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    below_reorder_flag = models.BooleanField(default=False, help_text="Stock fell below reorder level on consumption.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_item_type_display()}: {self.description}"

    def check_editable(self):
        status = JobCard.objects.values_list("status", flat=True).get(pk=self.job_card_id)
        if status in JobCard.LOCKED_STATUSES:
            raise ImmutableRecordError("This job card is read-only.")
        if self.stage == self.Stage.ESTIMATE and status != JobCard.Status.DRAFT:
            raise ImmutableRecordError("Estimate lines are locked once the job card is approved.")
        if self.stage == self.Stage.ACTUAL and status != JobCard.Status.IN_PROGRESS:
            raise ImmutableRecordError("Actual work can only be recorded while the job is IN PROGRESS.")

    def save(self, *args, **kwargs):
        self.check_editable()
        self.branch_id = self.job_card.branch_id
        self.line_total = money(self.quantity * self.unit_price)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.check_editable()
        return super().delete(*args, **kwargs)

    @property
    def is_goods(self) -> bool:
        """Parts are goods (federal sales tax); labour and sublet are services (provincial)."""
        return self.item_type == self.ItemType.PART


class JobCardStatusLog(BranchScoped):
    """Append-only audit trail: one row per status change (plus one at creation)."""

    job_card = models.ForeignKey(JobCard, on_delete=models.PROTECT, related_name="status_logs")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["changed_at", "id"]

    def __str__(self):
        return f"{self.job_card.number}: {self.from_status or '—'} -> {self.to_status}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError("Audit log rows cannot be edited.")
        self.branch_id = self.job_card.branch_id
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Audit log rows cannot be deleted.")


class HoldPeriod(BranchScoped):
    """One ON_HOLD episode, to measure idle-bay time."""

    job_card = models.ForeignKey(JobCard, on_delete=models.PROTECT, related_name="holds")
    entered_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()

    class Meta:
        ordering = ["entered_at"]

    def save(self, *args, **kwargs):
        self.branch_id = self.job_card.branch_id
        super().save(*args, **kwargs)

    @property
    def duration(self) -> timedelta:
        return (self.left_at or timezone.now()) - self.entered_at


# --- Invoicing -------------------------------------------------------------------

class Invoice(BranchScoped):
    """Sales tax invoice raised from an INVOICED job card. Seller/buyer details and tax rates
    are snapshotted so later master-data edits never change an issued invoice."""

    number = models.CharField(max_length=30, unique=True)
    job_card = models.OneToOneField(JobCard, on_delete=models.PROTECT, related_name="invoice")
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    seller_name = models.CharField(max_length=200)
    seller_address = models.TextField()
    seller_phone = models.CharField(max_length=30, blank=True)
    seller_ntn = models.CharField(max_length=20, blank=True)
    seller_strn = models.CharField(max_length=20, blank=True)
    seller_provincial_tax_authority = models.CharField(max_length=20, blank=True)
    seller_provincial_tax_reg_no = models.CharField(max_length=30, blank=True)

    buyer_name = models.CharField(max_length=200)
    buyer_address = models.TextField(blank=True)
    buyer_phone = models.CharField(max_length=30, blank=True)
    buyer_ntn = models.CharField(max_length=20, blank=True)
    buyer_cnic = models.CharField(max_length=15, blank=True)
    vehicle_registration = models.CharField(max_length=20)

    labour_total = models.DecimalField(max_digits=14, decimal_places=2)
    parts_total = models.DecimalField(max_digits=14, decimal_places=2)
    sublet_total = models.DecimalField(max_digits=14, decimal_places=2)
    total_excl_tax = models.DecimalField(max_digits=14, decimal_places=2)
    goods_tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    services_tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    goods_tax_amount = models.DecimalField(max_digits=14, decimal_places=2)
    services_tax_amount = models.DecimalField(max_digits=14, decimal_places=2)
    sales_tax_total = models.DecimalField(max_digits=14, decimal_places=2)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("workshop:invoice_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError("An issued invoice cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("An issued invoice cannot be deleted.")

    @staticmethod
    def tax_on(amount, rate) -> Decimal:
        """Tax on one line, rounded per line so printed lines add up to the total."""
        return money(amount * rate / 100)

    def rate_for(self, item) -> Decimal:
        return self.goods_tax_rate if item.is_goods else self.services_tax_rate

    def lines(self) -> list:
        rows = []
        items = self.job_card.items.filter(stage=JobCardItem.Stage.ACTUAL).select_related("part")
        for item in items:
            rate = self.rate_for(item)
            tax = self.tax_on(item.line_total, rate)
            rows.append({"item": item, "rate": rate, "tax": tax, "incl": item.line_total + tax})
        return rows


# --- SAP integration log ----------------------------------------------------------

class SapPostingLog(BranchScoped):
    """Every posting event sent (or, for the stub, that would be sent) to SAP FI.
    The idempotency key guarantees a business event is never posted twice."""

    class EventType(models.TextChoices):
        INVOICE = "INVOICE", "Invoice (AR / revenue / output tax)"
        PARTS_ISSUE = "PARTS_ISSUE", "Parts issue (COGS / inventory)"
        PARTS_RETURN = "PARTS_RETURN", "Parts return (reversal)"

    class PostingStatus(models.TextChoices):
        LOGGED = "LOGGED", "Logged only (stub)"
        POSTED = "POSTED", "Posted to SAP"
        FAILED = "FAILED", "Failed"

    event_type = models.CharField(max_length=20, choices=EventType.choices)
    idempotency_key = models.CharField(max_length=80, unique=True)
    reference = models.CharField(max_length=30)
    job_card = models.ForeignKey(JobCard, on_delete=models.PROTECT, null=True, blank=True, related_name="sap_postings")
    adapter = models.CharField(max_length=60)
    payload = models.JSONField()
    total_debit = models.DecimalField(max_digits=14, decimal_places=2)
    total_credit = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=PostingStatus.choices, default=PostingStatus.LOGGED)
    sap_document_no = models.CharField(max_length=20, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "SAP posting log"

    def __str__(self):
        return f"{self.get_event_type_display()} {self.reference}"
