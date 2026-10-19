"""Run with python -I against an installed wheel or source distribution."""

from importlib.metadata import version

import django
import pandas as pd
from django.conf import settings
from django.db import models

from django_pandas_forms import PandasForm, PandasModelForm

settings.configure(INSTALLED_APPS=[], USE_I18N=False)
django.setup()


class ValueForm(PandasForm):
    class Meta:
        fields = ["value"]


class Reading(models.Model):
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "smoke"


class ReadingForm(PandasModelForm):
    class Meta:
        model = Reading
        fields = ["value"]


form = ValueForm(pd.DataFrame({"value": [0, False]}))
assert form.is_valid()
assert form.cleaned_data[0]["value"] == 0
assert form.cleaned_data[1]["value"] is False
model_form = ReadingForm([{}, {"value": "12"}])
assert model_form.is_valid()
assert model_form.cleaned_data == {0: {"value": 0}, 1: {"value": 12}}
assert not ReadingForm([{"value": "invalid"}]).is_valid()
print(f"django-pandas-forms {version('django-pandas-forms')}: installation smoke test passed")
