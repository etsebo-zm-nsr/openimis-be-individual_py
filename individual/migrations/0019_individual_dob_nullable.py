import core.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('individual', '0018_alter_groupindividual_role_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicalindividual',
            name='dob',
            field=core.fields.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='individual',
            name='dob',
            field=core.fields.DateField(blank=True, null=True),
        ),
    ]
