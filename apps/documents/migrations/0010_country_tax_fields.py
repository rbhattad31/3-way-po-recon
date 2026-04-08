"""Add country/tax compliance fields to PurchaseOrder and PurchaseOrderLineItem."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0009_add_tax_breakdown_vendor_tax_id_buyer_due_date'),
    ]

    operations = [
        # ── PurchaseOrder header fields ───────────────────────────────────────
        migrations.AddField(
            model_name='purchaseorder',
            name='country',
            field=models.CharField(
                blank=True, default='', max_length=10,
                help_text='ISO country code — drives tax schema (IN, AE, SA, US, …)',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='gstin',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='Buyer GSTIN (India) / TRN (UAE/SA)',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='state_code',
            field=models.CharField(
                blank=True, default='', max_length=10,
                help_text='Buyer state code (India)',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='vendor_gstin',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='Vendor GSTIN on this PO',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='vendor_state_code',
            field=models.CharField(
                blank=True, default='', max_length=10,
                help_text='Vendor state code (India)',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='reverse_charge',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='place_of_supply',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='india_supply_type',
            field=models.CharField(
                blank=True, default='INTRA', max_length=10,
                choices=[('INTRA', 'Intra-state (CGST + SGST)'), ('INTER', 'Inter-state (IGST)')],
            ),
        ),

        # ── PurchaseOrderLineItem tax fields ──────────────────────────────────
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='hsn_sac_code',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='HSN (goods) / SAC (services) code — India',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='discount_percent',
            field=models.DecimalField(decimal_places=4, max_digits=7, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='cgst_rate',
            field=models.DecimalField(decimal_places=4, max_digits=7, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='cgst_amount',
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='sgst_rate',
            field=models.DecimalField(decimal_places=4, max_digits=7, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='sgst_amount',
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='igst_rate',
            field=models.DecimalField(decimal_places=4, max_digits=7, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='igst_amount',
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='cess_rate',
            field=models.DecimalField(decimal_places=4, max_digits=7, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='cess_amount',
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='vat_rate',
            field=models.DecimalField(
                decimal_places=4, max_digits=7, null=True, blank=True,
                help_text='VAT rate % — UAE, SA',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='vat_amount',
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True),
        ),
    ]
