from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0002_ocr_llm_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="extractionrun",
            name="completion_tokens",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="extractionrun",
            name="cost_usd",
            field=models.DecimalField(
                blank=True,
                decimal_places=8,
                help_text="Cost of the model calls in USD, when the provider reported it",
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="extractionrun",
            name="llm_calls",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="extractionrun",
            name="prompt_tokens",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
