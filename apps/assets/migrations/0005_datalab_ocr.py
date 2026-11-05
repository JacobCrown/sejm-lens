from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0004_alter_assetdeclaration_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="assetdeclaration",
            name="ocr_cost_usd",
            field=models.DecimalField(
                blank=True, decimal_places=6, help_text="What Datalab billed for this OCR, in USD",
                max_digits=12, null=True,
            ),
        ),
        migrations.AddField(
            model_name="assetdeclaration",
            name="ocr_pages",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assetdeclaration",
            name="ocr_response",
            field=models.JSONField(
                blank=True, help_text="Stored Datalab convert response, without extracted images",
                null=True,
            ),
        ),
    ]
