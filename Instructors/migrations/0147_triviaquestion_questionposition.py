# Generated by Django 2.2.4 on 2022-09-28 11:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Instructors', '0146_triviaquestion_questiontype'),
    ]

    operations = [
        migrations.AddField(
            model_name='triviaquestion',
            name='questionPosition',
            field=models.IntegerField(default=0),
        ),
    ]
