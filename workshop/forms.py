from decimal import Decimal

from django import forms

from .models import Branch, JobCardItem, LabourOperation, Part, Vehicle

ItemType = JobCardItem.ItemType


class JobCardForm(forms.Form):
    branch = forms.ModelChoiceField(Branch.objects.filter(is_active=True))
    vehicle = forms.ModelChoiceField(Vehicle.objects.select_related("customer"))
    odometer_km = forms.IntegerField(label="Odometer (km)", required=False, min_value=0)
    complaint = forms.CharField(label="Customer complaint / work requested", widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        data = super().clean()
        branch = data.get("branch")
        vehicle = data.get("vehicle")
        if branch and vehicle and (vehicle.branch_id != branch.pk or vehicle.customer.branch_id != branch.pk):
            self.add_error("vehicle", "Choose a vehicle belonging to this branch.")
        return data


class ItemForm(forms.Form):
    stage = forms.ChoiceField(choices=JobCardItem.Stage.choices)
    item_type = forms.ChoiceField(choices=ItemType.choices)
    labour_operation = forms.ModelChoiceField(LabourOperation.objects.none(), required=False)
    part = forms.ModelChoiceField(Part.objects.none(), required=False)
    description = forms.CharField(required=False, max_length=255)
    quantity = forms.DecimalField(required=False, min_value=Decimal("0.01"), max_digits=10, decimal_places=2)
    unit_price = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)
    unit_cost = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)

    def __init__(self, *args, branch, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["labour_operation"].queryset = LabourOperation.objects.filter(branch=branch)
        self.fields["part"].queryset = Part.objects.filter(branch=branch)

    def clean(self):
        data = super().clean()
        item_type = data.get("item_type")
        if item_type == ItemType.LABOUR and not data.get("labour_operation"):
            self.add_error("labour_operation", "Choose a labour operation.")
        if item_type == ItemType.PART:
            if not data.get("part"):
                self.add_error("part", "Choose a part.")
            if not data.get("quantity"):
                self.add_error("quantity", "Enter a quantity.")
        if item_type == ItemType.SUBLET:
            if not data.get("description"):
                self.add_error("description", "Describe the sublet work (and vendor).")
            if data.get("unit_price") is None:
                self.add_error("unit_price", "Enter the price charged to the customer.")
        return data

    def error_text(self) -> str:
        return "; ".join(f"{field}: {' '.join(errors)}" for field, errors in self.errors.items())
