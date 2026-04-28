from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vision_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='image_data',
            field=models.TextField(default='[]'),
        ),
    ]
