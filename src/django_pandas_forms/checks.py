"""Reusable column checks that return boolean pandas Series."""

import pandas as pd
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from pandas.api.types import is_scalar


class FieldCheck:
    """Base class for a column check; subclasses provide validate and get_error."""

    def __init__(self, field_name):
        self.field_name = field_name

    def validate(self, df, **kwargs):
        raise NotImplementedError

    def get_error(self):
        raise NotImplementedError


class IsInCheck(FieldCheck):
    """Check that each value belongs to the supplied collection."""

    def __init__(self, field_name, values):
        super().__init__(field_name)
        self.cached_list = tuple(values)

    def validate(self, df, **kwargs):
        values = df[self.field_name]
        result = values.isin(self.cached_list)
        if kwargs.get("nullable"):
            result |= values.isna()
        return result

    @staticmethod
    def get_error():
        return ValidationError(_("Select a valid choice."), code="invalid_choice")


class LengthCheck(FieldCheck):
    """Check the maximum length of string values."""

    def __init__(self, field_name, length=0):
        super().__init__(field_name)
        if length < 0:
            raise ValueError("length must be non-negative")
        self.length = length

    def validate(self, df, **kwargs):
        values = df[self.field_name]
        result = values.map(lambda value: isinstance(value, str) and len(value) <= self.length)
        if kwargs.get("nullable"):
            result |= values.isna()
        return result.astype(bool)

    def get_error(self):
        return ValidationError(
            _("Ensure this value has at most %(limit_value)s characters."),
            code="max_length",
            params={"limit_value": self.length},
        )


class PandasValidationColumn:
    """Validate a column, collecting errors by the DataFrame's index labels.

    With coerce=True, non-null values are converted to strings for checks only.
    The caller's DataFrame is never changed.
    """

    def __init__(self, name, frame=None, checks=None, coerce=False, nullable=False):
        self.name = name
        self.df = frame
        self.checks = list(checks or ())
        self.errors = {}
        self.coerce = coerce
        self.nullable = nullable

    def get_col_kwargs(self):
        return {"coerce": self.coerce, "nullable": self.nullable}

    def _add_error(self, index, error):
        self.errors.setdefault(index, {}).setdefault(self.name, []).append(error)

    def handle_error(self, check, index):
        self._add_error(index, check.get_error())

    def validate(self, pandas_data):
        self.errors = {}
        if self.name not in pandas_data:
            for index in pandas_data.index:
                self._add_error(
                    index, ValidationError(_("This column is required."), code="missing_column")
                )
            return not self.errors

        data = pandas_data
        values = data[self.name]
        if self.coerce:
            data = data.copy()
            data[self.name] = values.map(
                lambda value: value if is_scalar(value) and pd.isna(value) else str(value)
            )

        if not self.nullable:
            for index in values.index[values.isna()]:
                self._add_error(
                    index, ValidationError(_("This field cannot be null."), code="null")
                )

        for check in self.checks:
            result = check.validate(data, **self.get_col_kwargs())
            if not isinstance(result, pd.Series) or not result.index.equals(data.index):
                raise TypeError("Column checks must return a Series with the input index.")
            # Missing values have already been handled by the nullability check.
            result = result.fillna(False).astype(bool) | values.isna()
            for index in result.index[~result]:
                self.handle_error(check, index)
        return not self.errors


class PandasValidationFrame:
    """Validate a set of columns; each call replaces the previous result."""

    def __init__(self, cached_fields=None):
        self.cached_fields = dict(cached_fields or {})
        self.errors = {}
        self.valid = True

    def bind_errors(self, errors):
        for index, fields in errors.items():
            for name, messages in fields.items():
                self.errors.setdefault(index, {}).setdefault(name, []).extend(messages)

    def validate(self, pandas_data):
        if not pandas_data.index.is_unique:
            raise ValueError("Validation requires unique DataFrame index labels.")
        self.errors = {}
        for column in self.cached_fields.values():
            column.validate(pandas_data)
            self.bind_errors(column.errors)
        self.valid = not self.errors
        return self.valid
