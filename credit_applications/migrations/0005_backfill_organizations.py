from django.db import migrations


def backfill_organizations(apps, schema_editor):
    Application = apps.get_model("credit_applications", "Application")
    ApplicationEvent = apps.get_model("credit_applications", "ApplicationEvent")
    ApplicationVersion = apps.get_model("credit_applications", "ApplicationVersion")
    Organization = apps.get_model("credit_applications", "Organization")
    OrganizationMembership = apps.get_model("credit_applications", "OrganizationMembership")

    for application in Application.objects.all().iterator():
        organization, _ = Organization.objects.get_or_create(
            inn=application.inn,
            defaults={"name": application.organization_name},
        )
        application.organization_id = organization.pk
        version = ApplicationVersion.objects.filter(
            application_id=application.pk,
            version_number=application.current_version,
        ).first()
        if version and application.submitted_at is None:
            application.submitted_at = version.submitted_at
        application.save(update_fields=("organization", "submitted_at"))

        if application.created_by_id:
            OrganizationMembership.objects.get_or_create(
                organization_id=organization.pk,
                user_id=application.created_by_id,
                defaults={"role": "OWNER", "is_active": True},
            )
        ApplicationEvent.objects.get_or_create(
            application_id=application.pk,
            event_type="SUBMITTED",
            defaults={
                "actor_id": application.created_by_id,
                "to_status": application.status,
                "message": "Заявка перенесена в новый журнал обработки",
                "metadata": {"version": application.current_version, "backfilled": True},
            },
        )


class Migration(migrations.Migration):
    dependencies = [("credit_applications", "0004_organization_application_assigned_to_and_more")]

    operations = [migrations.RunPython(backfill_organizations, migrations.RunPython.noop)]
