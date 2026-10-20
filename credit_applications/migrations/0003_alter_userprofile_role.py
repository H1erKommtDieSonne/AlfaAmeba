from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("credit_applications", "0002_userprofile_application_created_by")]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("BUSINESS", "Представитель бизнеса"),
                    ("BANK_EMPLOYEE", "Сотрудник банка"),
                    ("BANK_ADMIN", "Администратор банка"),
                ],
                default="BUSINESS",
                max_length=32,
            ),
        )
    ]

