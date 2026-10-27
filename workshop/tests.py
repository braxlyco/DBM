"""Run with: python manage.py test workshop"""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from . import workflow
from .forms import JobCardForm
from .models import (
    Branch, Customer, ImmutableRecordError, JobCard, JobCardItem, JobCardStatusLog, LabourOperation, Part,
    SapPostingLog, Vehicle,
)

S = JobCard.Status
Stage = JobCardItem.Stage
ItemType = JobCardItem.ItemType


class WorkflowTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            code="T01", name="Test", legal_name="Test (Pvt) Ltd", address="Lahore",
            goods_tax_rate=D("18.00"), services_tax_rate=D("16.00"),
        )
        customer = Customer.objects.create(branch=self.branch, name="C", phone="1")
        vehicle = Vehicle.objects.create(branch=self.branch, customer=customer, registration_no="abc-1", make="M", model="X")
        self.part = Part.objects.create(
            branch=self.branch, part_no="P1", name="Pad", unit_cost=D("500"), unit_price=D("1000"),
            stock_on_hand=D("6"), reorder_level=D("5"),
        )
        self.op = LabourOperation.objects.create(
            branch=self.branch, code="L1", name="Fit", standard_hours=D("1.5"), hourly_rate=D("2000")
        )
        self.job = workflow.create_job_card(branch=self.branch, vehicle=vehicle, complaint="noise")

    def estimate(self):
        workflow.add_item(self.job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=self.op)
        workflow.add_item(self.job, stage=Stage.ESTIMATE, item_type=ItemType.PART, part=self.part, quantity=D("1"))

    def run_to(self, *statuses):
        for status in statuses:
            self.job = workflow.transition(self.job, status, note="n")

    def test_happy_path_invoice_and_postings(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS)
        self.assertEqual(self.job.estimate_total, D("4000.00"))  # 1.5h x 2000 + 1 x 1000
        workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=self.op, quantity=D("2"))
        _, warnings = workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.PART, part=self.part, quantity=D("2"))
        workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.SUBLET, description="paint", unit_price=D("500"))
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_on_hand, D("4"))
        self.assertTrue(warnings)  # 4 < reorder level 5: flagged, not blocked
        self.run_to(S.COMPLETED, S.INVOICED)

        inv = self.job.invoice
        # labour 4000 + parts 2000 + sublet 500; tax: 2000*18% = 360, (4000+500)*16% = 720
        self.assertEqual(inv.total_excl_tax, D("6500.00"))
        self.assertEqual(inv.goods_tax_amount, D("360.00"))
        self.assertEqual(inv.services_tax_amount, D("720.00"))
        self.assertEqual(inv.grand_total, D("7580.00"))
        self.assertEqual(inv.number, "T01-INV-000001")

        invoice_posts = SapPostingLog.objects.filter(event_type=SapPostingLog.EventType.INVOICE)
        self.assertEqual(invoice_posts.count(), 1)
        self.assertEqual(invoice_posts[0].total_debit, invoice_posts[0].total_credit)
        self.assertEqual(SapPostingLog.objects.filter(event_type=SapPostingLog.EventType.PARTS_ISSUE).count(), 1)
        # creation + 4 transitions
        self.assertEqual(JobCardStatusLog.objects.filter(job_card=self.job).count(), 5)

    def test_illegal_transition_rejected_and_not_logged(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.transition(self.job, S.COMPLETED)
        self.assertEqual(self.job.status_logs.count(), 1)

    def test_status_cannot_change_silently(self):
        self.job.status = S.APPROVED
        with self.assertRaises(ImmutableRecordError):
            self.job.save()

    def test_invoiced_job_is_read_only(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS)
        workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=self.op)
        self.run_to(S.COMPLETED, S.INVOICED)
        self.job.complaint = "changed"
        with self.assertRaises(ImmutableRecordError):
            self.job.save()
        with self.assertRaises(ImmutableRecordError):
            workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=self.op)

    def test_estimate_locked_after_approval(self):
        self.estimate()
        self.run_to(S.APPROVED)
        with self.assertRaises(ImmutableRecordError):
            workflow.add_item(self.job, stage=Stage.ESTIMATE, item_type=ItemType.LABOUR, labour_operation=self.op)

    def test_cancel_needs_reason_and_never_posts(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.transition(self.job, S.CANCELLED, note="")
        workflow.transition(self.job, S.CANCELLED, note="Customer declined")
        self.assertFalse(SapPostingLog.objects.exists())

    def test_hold_records_period(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS, S.ON_HOLD)
        self.assertIsNone(self.job.holds.get().left_at)
        self.run_to(S.IN_PROGRESS)
        self.assertIsNotNone(self.job.holds.get().left_at)
        self.assertFalse(SapPostingLog.objects.exists())

    def test_insufficient_stock_blocks_consumption(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS)
        with self.assertRaises(workflow.WorkflowError):
            workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.PART, part=self.part, quantity=D("99"))
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_on_hand, D("6"))

    def test_removing_consumed_part_restocks_and_reverses(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS)
        item, _ = workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.PART, part=self.part, quantity=D("2"))
        workflow.remove_item(item)
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_on_hand, D("6"))
        self.assertEqual(SapPostingLog.objects.filter(event_type=SapPostingLog.EventType.PARTS_RETURN).count(), 1)

    def test_comeback_creates_linked_job(self):
        self.estimate()
        self.run_to(S.APPROVED, S.IN_PROGRESS)
        workflow.add_item(self.job, stage=Stage.ACTUAL, item_type=ItemType.LABOUR, labour_operation=self.op)
        self.run_to(S.COMPLETED, S.INVOICED)
        comeback = workflow.create_comeback(self.job, complaint="noise again")
        self.assertEqual(comeback.parent, self.job)
        self.assertEqual(comeback.status, S.DRAFT)
        self.assertNotEqual(comeback.number, self.job.number)

    def test_vehicle_must_match_branch(self):
        other = Branch.objects.create(code="T02", name="Other", legal_name="Other", address="Karachi")
        form = JobCardForm(data={
            "branch": other.pk,
            "vehicle": self.job.vehicle_id,
            "complaint": "Inspection",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("vehicle", form.errors)
        with self.assertRaises(workflow.WorkflowError):
            workflow.create_job_card(branch=other, vehicle=self.job.vehicle, complaint="Inspection")

    def test_job_list_search_and_pagination(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="operator", password="test-password")
        self.client.force_login(user)
        for index in range(25):
            workflow.create_job_card(branch=self.branch, vehicle=self.job.vehicle, complaint=f"Job {index}")
        url = reverse("workshop:job_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["jobs"]), 20)
        self.assertEqual(response.context["page"].paginator.count, 26)
        response = self.client.get(url, {"q": self.job.vehicle.registration_no})
        self.assertEqual(response.context["page"].paginator.count, 26)
        response = self.client.get(url, {"q": "nonexistent"})
        self.assertEqual(response.context["page"].paginator.count, 0)
