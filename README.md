# django-pandas-forms

[![Python](https://github.com/memegauste/django-pandas-forms/actions/workflows/python.yml/badge.svg)](https://github.com/memegauste/django-pandas-forms/actions/workflows/python.yml)

Validate tabular data with pandas and Django model fields. Use form-like hooks
for individual cells and rows, collect errors by row, and validate related keys
with one database query per relation field per form instance.

## Requirements

- Python 3.12 or newer; CI runs Python 3.12, 3.13, 3.14 and 3.15.
- Django `>=6.1.2,<6.2` and pandas `>=3.0.6,<4` (installed automatically).
- Configured Django settings, normally provided by your Django project.

Python 3.15 is included as a compatibility check. Consult Django's
[supported Python versions](https://docs.djangoproject.com/en/6.1/faq/install/#what-python-version-can-i-use-with-django)
before choosing your production interpreter.

## Installation

From a checkout of this repository:

```sh
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` installs the package itself and its runtime dependencies;
dependency versions are declared in `pyproject.toml`.

To consume the repository from another project's requirements file:

```text
django-pandas-forms @ git+https://github.com/memegauste/django-pandas-forms.git@main
```

Replace `main` with a commit SHA for a reproducible installation. A GitHub token
is not needed to install this public repository. The project can also be built
as a wheel or source distribution; these instructions do not require a PyPI
release.

## Custom forms

```python
import pandas as pd
from django.core.exceptions import ValidationError
from django_pandas_forms import PandasForm


class QuantityForm(PandasForm):
    class Meta:
        fields = ["name", "quantity"]

    def clean_quantity(self):
        try:
            quantity = int(self.row_data["quantity"])
        except (TypeError, ValueError) as error:
            raise ValidationError("Enter an integer.") from error
        if quantity < 0:
            raise ValidationError("Quantity cannot be negative.")
        return quantity

    def clean(self):
        # Optional cross-field validation, called after field cleaners per row.
        return self.row_data


form = QuantityForm(
    pd.DataFrame(
        [
            {"name": "Book", "quantity": "2"},
            {"name": "Pen", "quantity": 0},
        ]
    )
)
assert form.is_valid()
assert form.cleaned_data[0]["quantity"] == 2
assert form.cleaned_data[1]["quantity"] == 0
validated_frame = form.pandas_data
```

Input can be a DataFrame or an iterable of dictionaries. Only `Meta.fields` are
processed. Missing scalar pandas values (`NaN`, `pd.NA`, `NaT`) become `None`;
`0`, `False`, empty strings and empty collections are preserved. Input data is
not modified. `PandasForm` applies custom cleaners and an optional schema; it
does not infer field types or required fields.

`clean_<field>()` accesses the current row through `self.row_data`.
`self.row_index` gives its zero-based position. `clean()` may return a replacement
row mapping, return `None`, or raise `ValidationError`.

## Model forms

```python
from django_pandas_forms import PandasModelForm
from myapp.models import Product


class ProductImportForm(PandasModelForm):
    class Meta:
        model = Product
        fields = ["name", "price", "category", "tags"]


form = ProductImportForm(
    [
        {"name": "Book", "price": "12.50", "category": 1, "tags": [2, 3]},
    ]
)
if form.is_valid():
    rows = form.cleaned_data
else:
    errors = form.errors
```

Select model fields explicitly, or use `fields = "__all__"` for all editable
fields except the primary key. Unknown, reverse and non-editable fields are
configuration errors.

Model forms use Django field conversion, validators, choices (including grouped
choices), null/blank rules and callable defaults. Defaults apply to absent keys;
an explicit `None` is validated as submitted. Foreign keys and one-to-one fields
accept the related key, including custom primary keys and `to_field`. Many-to-many
fields accept a list, tuple or set of related primary keys. Relations are fetched
lazily and cached separately for each field and form instance.

These forms validate data without saving model instances. They do not call
`Model.clean()`, check uniqueness, validate database constraints, or write
many-to-many relations. Perform those operations in your application's import
service before persisting data.

## Errors and validation lifecycle

```python
form.add_error("name", "Invalid name.", row=0)
form.add_error(None, "Invalid row.", row=0)
form.add_error(None, "Invalid dataset.")
```

Call `is_valid()` before reading the validation result. Errors are Django
`ValidationError` objects, grouped as follows:

```python
form.errors[0]["name"]  # field errors in the first row
form.errors[0]["__all__"]  # non-field errors in the first row
form.errors["__all__"]  # dataset errors
```

Row keys in `errors` and `cleaned_data` are always zero-based positions, including
when input is a DataFrame with a custom index. Invalid fields are omitted from
`cleaned_data`. `pandas_data` contains the cleaned rows used for schema validation;
consume it only when the form is valid.

Validation runs once per instance. Create a new form for changed input or fresh
relation data. `PandasForm()` is unbound and invalid; `PandasForm([])` is a valid,
empty bound dataset.

## Standalone DataFrame schemas

```python
from django_pandas_forms import (
    IsInCheck,
    LengthCheck,
    PandasValidationColumn,
    PandasValidationFrame,
)

schema = PandasValidationFrame(
    {
        "name": PandasValidationColumn("name", checks=[LengthCheck("name", 50)]),
        "status": PandasValidationColumn(
            "status",
            checks=[IsInCheck("status", ["new", "used"])],
            nullable=True,
        ),
    }
)
valid = schema.validate(dataframe)
errors = schema.errors
```

Standalone schemas retain the DataFrame's index labels, which must be unique.
Each call resets previous errors. `nullable=True` allows null values;
`coerce=True` converts non-null values to strings for column checks without
changing the input. Custom checks subclass `FieldCheck`, implement
`validate(dataframe, **kwargs)` to return an aligned boolean Series, and implement
`get_error()` to return a `ValidationError`. Assign a schema to `form.schema` to
apply it after custom cleaning.

The old root-level `pandas.py` has been replaced with the `django_pandas_forms`
package to avoid shadowing pandas. Import the public classes from
`django_pandas_forms`.

## Development and distributions

```sh
python -m pip install -r requirements-dev.txt
python -m pytest --cov=django_pandas_forms --cov-report=term-missing
python -m ruff check .
python -m ruff format --check .
python -m build
python -m twine check --strict dist/*
```

Tests use an in-memory SQLite database and require no external services. CI
enforces at least 90% test coverage, runs the four Python versions, checks style,
builds wheel and source distributions, installs both in clean environments and
runs `tests/smoke_install.py`. Build artifacts can be downloaded from the workflow
run. To install a locally built wheel:

```sh
python -m pip install dist/django_pandas_forms-0.1.0-py3-none-any.whl
```
