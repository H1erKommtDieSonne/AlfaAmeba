from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0001_initial")]
    operations = [migrations.AddField(
        model_name="applicationdocument", name="requirement_code",
        field=models.CharField(max_length=64, blank=True),
    )]
