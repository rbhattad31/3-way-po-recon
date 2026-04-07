"""
Seed vendor for case AP-260406-0001.

Invoice D96 -- DEETYA GEMS, GSTIN 36BSJPA6076J1ZI, INR 17,170.00

Run:
    python manage.py shell < scripts/seed_vendor_case_0001.py
"""
from apps.vendors.models import Vendor
from apps.documents.models import Invoice

# -- Vendor master --------------------------------------------------------
vendor, created = Vendor.objects.update_or_create(
    code="V-DG-001",
    defaults={
        "name": "DEETYA GEMS",
        "normalized_name": "deetya gems",
        "tax_id": "36BSJPA6076J1ZI",
        "address": (
            "Plot No. 8-2-293/82/A/431, Road No. 22,\n"
            "Jubilee Hills, Hyderabad,\n"
            "Telangana 500033, India"
        ),
        "country": "India",
        "currency": "INR",
        "payment_terms": "Net 30",
        "contact_email": "accounts@deetyagems.com",
        "is_active": True,
    },
)

action = "Created" if created else "Updated"
print(f"{action} vendor: {vendor.code} -- {vendor.name} (pk={vendor.pk})")

# -- Link to invoice ------------------------------------------------------
updated = Invoice.objects.filter(
    invoice_number="D96",
    vendor__isnull=True,
).update(vendor=vendor)

if updated:
    print(f"Linked vendor to invoice D96 (case AP-260406-0001)")
else:
    inv = Invoice.objects.filter(invoice_number="D96").first()
    if inv and inv.vendor_id:
        print(f"Invoice D96 already linked to vendor pk={inv.vendor_id}")
    else:
        print("Invoice D96 not found or already processed")

print("Done.")
