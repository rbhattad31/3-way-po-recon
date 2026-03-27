from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extraction', '0009_bulk_extraction_intake'),
    ]

    operations = [
        migrations.AddField(
            model_name='extractionresult',
            name='ocr_text',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Raw OCR text sent to the LLM -- preserved for debugging missed extractions',
            ),
        ),
    ]
