"""
python manage.py seed_demo [--with-invoice]

Loads a demo branch, parts, labour operations, one customer + vehicle and one DRAFT job
card with an estimate. Safe to run more than once (existing records are reused).
All names, numbers and tax registrations below are fictitious demo data.
"""
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from workshop import workflow
from workshop.models import Branch, Customer, JobCard, JobCardItem, LabourOperation, Part, Vehicle

S = JobCard.Status
Stage = JobCardItem.Stage
ItemType = JobCardItem.ItemType

PARTS = [
    # part_no, name, uom, hs_code, unit_cost, unit_price, stock, reorder_level
    ("OF-1001", "Oil filter (Corolla 1.6/1.8)", "PCS", "8421.23", "850", "1200", "25", "10"),
    ("EO-5W30-4L", "Engine oil 5W-30, 4 litre", "CAN", "2710.19", "6500", "8200", "12", "6"),
    ("BP-F-2002", "Front brake pad set", "SET", "8708.30", "5200", "7400", "4", "5"),  # already below reorder
    ("AF-3003", "Engine air filter", "PCS", "8421.31", "1100", "1650", "15", "5"),
    ("SP-4004", "Spark plug, iridium", "PCS", "8511.10", "1400", "2100", "16", "8"),
]

LABOUR = [
    # code, name, standard_hours, hourly_rate
    ("PM-10K", "Periodic maintenance (10,000 km)", "1.50", "3000"),
    ("BRK-F", "Front brake pad replacement", "1.00", "3000"),
    ("AC-SVC", "AC service and gas top-up", "2.00", "3000"),
    ("DIAG", "Computer diagnostic scan", "0.50", "3000"),
]


class Command(BaseCommand):
    help = "Load demo master data and one draft job card."

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-invoice", action="store_true",
            help="Also run a second job card through the whole lifecycle to INVOICED.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        branch, _ = Branch.objects.get_or_create(
            code="LHR01",
            defaults=dict(
                name="Demo Motors — Lahore",
                legal_name="Demo Motors (Pvt) Ltd",
                address="Plot 1, Main Boulevard, Gulberg III, Lahore",
                phone="042-00000000",
                ntn="0000000-0",          # PLACEHOLDER
                strn="00-00-0000-000-00",  # PLACEHOLDER
                provincial_tax_authority="PRA",
                provincial_tax_reg_no="P0000000-0",  # PLACEHOLDER
                goods_tax_rate=D("18.00"),
                services_tax_rate=D("16.00"),
                sap_profit_center="PC-LHR01",
            ),
        )
        parts = {}
        for part_no, name, uom, hs, cost, price, stock, reorder in PARTS:
            parts[part_no], _ = Part.objects.get_or_create(
                branch=branch, part_no=part_no,
                defaults=dict(name=name, uom=uom, hs_code=hs, unit_cost=D(cost), unit_price=D(price),
                              stock_on_hand=D(stock), reorder_level=D(reorder)),
            )
        ops = {}
        for code, name, hours, rate in LABOUR:
            ops[code], _ = LabourOperation.objects.get_or_create(
                branch=branch, code=code,
                defaults=dict(name=name, standard_hours=D(hours), hourly_rate=D(rate)),
            )
        customer, _ = Customer.objects.get_or_create(
            branch=branch, phone="0300-0000000",
            defaults=dict(name="Demo Customer", address="House 12, Street 4, Model Town, Lahore",
                          cnic="", ntn=""),
        )
        vehicle, _ = Vehicle.objects.get_or_create(
            registration_no="LEA-1234",
            defaults=dict(branch=branch, customer=customer, vin="DEMOVIN0000000001",
                          make="Toyota", model="Corolla Altis 1.8", year=2019, colour="White"),
        )

        if not vehicle.job_cards.filter(status=S.DRAFT).exists():
            job = workflow.create_job_card(
                branch=branch, vehicle=vehicle, odometer_km=40150,
                complaint="10,000 km periodic service. Squeal from front brakes when stopping.",
            )
            self._add_estimate(job, parts, ops)
            self.stdout.write(f"Created draft job card {job.number} with an estimate.")

        if options["with_invoice"] and not vehicle.job_cards.filter(status=S.INVOICED).exists():
            job = workflow.create_job_card(
                branch=branch, vehicle=vehicle, odometer_km=30020,
                complaint="Periodic service and AC not cooling.",
            )
            workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=ops["PM-10K"])
            workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=ops["AC-SVC"])
            workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=parts["OF-1001"], quantity=D("1"))
            workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=parts["EO-5W30-4L"], quantity=D("1"))
            workflow.transition(job, S.APPROVED, note="Customer approved by phone")
            workflow.transition(job, S.IN_PROGRESS)
            workflow.add_item(job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=ops["PM-10K"])
            workflow.add_item(job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=ops["AC-SVC"],
                              quantity=D("2.50"))
            workflow.add_item(job, stage=Stage.ACTUAL, item_type=ItemType.PART, part=parts["OF-1001"], quantity=D("1"))
            workflow.add_item(job, stage=Stage.ACTUAL, item_type=ItemType.PART, part=parts["EO-5W30-4L"], quantity=D("1"))
            workflow.add_item(job, stage=Stage.ACTUAL, item_type=ItemType.SUBLET,
                              description="AC condenser flush — Demo Cooling Works", unit_price=D("4500"),
                              unit_cost=D("3000"))
            workflow.transition(job, S.COMPLETED)
            workflow.transition(job, S.INVOICED)
            self.stdout.write(f"Created invoiced job card {job.number} (invoice {job.invoice.number}).")

        self.stdout.write(self.style.SUCCESS("Demo data ready."))

    @staticmethod
    def _add_estimate(job, parts, ops):
        workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=ops["PM-10K"])
        workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=ops["BRK-F"])
        workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=parts["OF-1001"], quantity=D("1"))
        workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=parts["EO-5W30-4L"], quantity=D("1"))
        workflow.add_item(job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=parts["BP-F-2002"], quantity=D("1"))
