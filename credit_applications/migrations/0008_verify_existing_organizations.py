from django.db import migrations


def verify_existing_organizations(apps, schema_editor):
    Organization = apps.get_model("credit_applications", "Organization")
    Organization.objects.filter(memberships__is_active=True).distinct().update(
        verification_status="VERIFIED"
    )


class Migration(migrations.Migration):
    dependencies = [("credit_applications", "0007_organizationaccessrequest")]

    operations = [migrations.RunPython(verify_existing_organizations, migrations.RunPython.noop)]
