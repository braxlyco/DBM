"""
Admin = master-data CRUD (branches, customers, vehicles, parts, labour operations).
Operational and financial records are VIEW-ONLY here: they change only through the
workshop screens, so the workflow rules and audit trail cannot be bypassed.
"""
import json

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Branch, Customer, DocumentSequence, HoldPeriod, Invoice, JobCard, JobCardItem, JobCardStatusLog,
    LabourOperation, Part, SapPostingLog, Vehicle,
)


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --- Master data (editable) ---------------------------------------------------

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "ntn", "strn", "goods_tax_rate", "services_tax_rate", "is_active")


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0
    fields = ("registration_no", "vin", "make", "model", "year", "colour", "branch")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "cnic", "ntn", "branch")
    list_filter = ("branch",)
    search_fields = ("name", "phone", "cnic", "ntn")
    inlines = [VehicleInline]


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_no", "make", "model", "year", "customer", "branch")
    list_filter = ("branch", "make")
    search_fields = ("registration_no", "vin", "customer__name")
    autocomplete_fields = ("customer",)


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    list_display = ("part_no", "name", "branch", "stock_on_hand", "reorder_level", "low_stock",
                    "unit_cost", "unit_price")
    list_filter = ("branch",)
    search_fields = ("part_no", "name")

    @admin.display(boolean=True, description="Below reorder")
    def low_stock(self, obj):
        return obj.is_below_reorder


@admin.register(LabourOperation)
class LabourOperationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "branch", "standard_hours", "hourly_rate", "standard_charge")
    list_filter = ("branch",)
    search_fields = ("code", "name")


# --- Operational / financial records (view-only) -----------------------------------

class ItemInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = JobCardItem
    fields = readonly_fields = (
        "stage", "item_type", "description", "quantity", "unit_price", "line_total", "below_reorder_flag",
    )


class StatusLogInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = JobCardStatusLog
    fields = readonly_fields = ("changed_at", "from_status", "to_status", "changed_by", "note")


class HoldInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = HoldPeriod
    fields = readonly_fields = ("entered_at", "left_at", "reason")


@admin.register(JobCard)
class JobCardAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("number", "status", "branch", "vehicle", "customer", "estimate_total", "created_at")
    list_filter = ("status", "branch")
    search_fields = ("number", "vehicle__registration_no", "customer__name")
    inlines = [ItemInline, HoldInline, StatusLogInline]


@admin.register(JobCardStatusLog)
class JobCardStatusLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("job_card", "from_status", "to_status", "changed_by", "changed_at")
    list_filter = ("to_status", "branch")


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("number", "issued_at", "buyer_name", "total_excl_tax", "sales_tax_total", "grand_total", "branch")
    list_filter = ("branch",)
    search_fields = ("number", "buyer_name", "vehicle_registration")


@admin.register(SapPostingLog)
class SapPostingLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "event_type", "reference", "total_debit", "total_credit", "status", "adapter")
    list_filter = ("event_type", "status", "branch")
    search_fields = ("reference", "idempotency_key")
    exclude = ("payload",)
    readonly_fields = ("payload_pretty",)

    @admin.display(description="Payload")
    def payload_pretty(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.payload, indent=2))


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("branch", "key", "last_value")
