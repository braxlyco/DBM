from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import workflow
from .forms import ItemForm, JobCardForm
from .models import Branch, ImmutableRecordError, Invoice, JobCard, JobCardItem, LabourOperation, Part

S = JobCard.Status
Stage = JobCardItem.Stage
RULE_ERRORS = (workflow.WorkflowError, ImmutableRecordError)

# Button label and Bootstrap style for each target status.
TRANSITION_BUTTONS = {
    S.APPROVED: ("Approve estimate", "success"),
    S.IN_PROGRESS: ("Start work", "primary"),
    S.ON_HOLD: ("Put on hold", "warning"),
    S.COMPLETED: ("Mark completed", "success"),
    S.INVOICED: ("Generate invoice & log SAP posting", "dark"),
    S.CANCELLED: ("Cancel (customer declined)", "outline-danger"),
}


@login_required
def job_list(request):
    jobs = JobCard.objects.select_related("branch", "customer", "vehicle")
    status = request.GET.get("status", "")
    branch = request.GET.get("branch", "")
    query = request.GET.get("q", "").strip()[:100]
    if status in S.values:
        jobs = jobs.filter(status=status)
    if branch:
        jobs = jobs.filter(branch__code=branch)
    if query:
        jobs = jobs.filter(
            Q(number__icontains=query)
            | Q(vehicle__registration_no__icontains=query)
            | Q(customer__name__icontains=query)
        )
    page = Paginator(jobs, 20).get_page(request.GET.get("page"))
    return render(request, "workshop/job_list.html", {
        "jobs": page,
        "page": page,
        "statuses": S.choices,
        "branches": Branch.objects.all(),
        "current_status": status,
        "current_branch": branch,
        "current_query": query,
        "low_stock": Part.objects.filter(stock_on_hand__lt=F("reorder_level")).select_related("branch"),
    })


@login_required
def job_create(request):
    form = JobCardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        job = workflow.create_job_card(user=request.user, **form.cleaned_data)
        messages.success(request, f"Job card {job.number} opened. Add the estimate lines, then approve.")
        return redirect(job)
    return render(request, "workshop/job_form.html", {"form": form})


@login_required
def job_detail(request, pk):
    job = get_object_or_404(JobCard.objects.select_related("branch", "customer", "vehicle", "parent"), pk=pk)
    buttons = []
    for target in workflow.allowed_targets(job):
        label, style = TRANSITION_BUTTONS[target]
        if target == S.IN_PROGRESS and job.status == S.ON_HOLD:
            label = "Resume work"
        buttons.append({"status": target, "label": label, "style": style})
    items = job.items.select_related("part", "labour_operation")
    return render(request, "workshop/job_detail.html", {
        "job": job,
        "buttons": buttons,
        "estimate_lines": items.filter(stage=Stage.ESTIMATE),
        "actual_lines": items.filter(stage=Stage.ACTUAL),
        "estimate_totals": job.totals(Stage.ESTIMATE),
        "actual_totals": job.totals(Stage.ACTUAL),
        "variance": job.variance_breakdown() if job.estimate_total is not None else None,
        "can_edit_estimate": job.status == S.DRAFT,
        "can_record_work": job.status == S.IN_PROGRESS,
        "parts": Part.objects.filter(branch=job.branch),
        "labour_ops": LabourOperation.objects.filter(branch=job.branch),
        "holds": job.holds.all(),
        "logs": job.status_logs.select_related("changed_by"),
        "postings": job.sap_postings.all(),
        "comebacks": job.comebacks.all(),
        "invoice": getattr(job, "invoice", None),
    })


@login_required
@require_POST
def job_transition(request, pk):
    job = get_object_or_404(JobCard, pk=pk)
    to_status = request.POST.get("to_status", "")
    try:
        job = workflow.transition(job, to_status, user=request.user, note=request.POST.get("note", ""))
    except RULE_ERRORS as exc:
        messages.error(request, str(exc))
        return redirect(job)
    messages.success(request, f"{job.number} is now {job.get_status_display()}.")
    if job.status == S.INVOICED:
        return redirect(job.invoice)
    return redirect(job)


@login_required
@require_POST
def item_add(request, pk):
    job = get_object_or_404(JobCard, pk=pk)
    form = ItemForm(request.POST, branch=job.branch)
    if not form.is_valid():
        messages.error(request, form.error_text())
        return redirect(job)
    try:
        item, warnings = workflow.add_item(job, user=request.user, **form.cleaned_data)
    except RULE_ERRORS as exc:
        messages.error(request, str(exc))
        return redirect(job)
    messages.success(request, f"Added {item.get_item_type_display().lower()}: {item.description}.")
    for warning in warnings:
        messages.warning(request, f"Low stock: {warning}")
    return redirect(job)


@login_required
@require_POST
def item_remove(request, pk, item_pk):
    job = get_object_or_404(JobCard, pk=pk)
    item = get_object_or_404(JobCardItem, pk=item_pk, job_card=job)
    try:
        workflow.remove_item(item, user=request.user)
    except RULE_ERRORS as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Removed: {item.description}.")
    return redirect(job)


@login_required
@require_POST
def job_comeback(request, pk):
    original = get_object_or_404(JobCard, pk=pk)
    complaint = request.POST.get("complaint", "").strip()
    if not complaint:
        messages.error(request, "Describe the returning fault.")
        return redirect(original)
    try:
        job = workflow.create_comeback(original, complaint=complaint, user=request.user)
    except RULE_ERRORS as exc:
        messages.error(request, str(exc))
        return redirect(original)
    messages.success(request, f"Comeback job card {job.number} opened, linked to {original.number}.")
    return redirect(job)


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("job_card", "branch"), pk=pk)
    return render(request, "workshop/invoice_detail.html", {"invoice": invoice, "lines": invoice.lines()})
