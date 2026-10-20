# Generated manually for the initial vertical slice.
import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Application",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("organization_name", models.CharField(max_length=200, validators=[django.core.validators.MinLengthValidator(2)])),
                ("inn", models.CharField(db_index=True, max_length=12, validators=[django.core.validators.RegexValidator("^(\\d{10}|\\d{12})$", "ИНН должен содержать 10 или 12 цифр")])),
                ("status", models.CharField(choices=[("DRAFT", "Черновик"), ("SUBMITTED", "Отправлена")], db_index=True, default="DRAFT", max_length=32)),
                ("current_version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("-updated_at",)},
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("code", models.SlugField(max_length=64, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("description", models.TextField()),
                ("min_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("max_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("min_term_months", models.PositiveSmallIntegerField()),
                ("max_term_months", models.PositiveSmallIntegerField()),
                ("badge", models.CharField(blank=True, max_length=80)),
                ("active", models.BooleanField(default=True)),
            ],
            options={"ordering": ("code",)},
        ),
        migrations.CreateModel(
            name="ApplicationVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version_number", models.PositiveIntegerField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("term_months", models.PositiveSmallIntegerField()),
                ("purpose", models.TextField(validators=[django.core.validators.MinLengthValidator(10)])),
                ("snapshot", models.JSONField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("application", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="credit_applications.application")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="credit_applications.product")),
            ],
            options={"ordering": ("application", "version_number")},
        ),
        migrations.AddConstraint(
            model_name="applicationversion",
            constraint=models.UniqueConstraint(fields=("application", "version_number"), name="unique_application_version"),
        ),
    ]

