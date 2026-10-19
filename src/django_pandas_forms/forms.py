"""Dataset validation using custom cleaners and Django model fields."""

from collections.abc import Iterable, Mapping

import pandas as pd
from django.core.exceptions import (
    NON_FIELD_ERRORS,
    FieldDoesNotExist,
    ImproperlyConfigured,
    ValidationError,
)
from django.db import models
from django.utils.translation import gettext_lazy as _
from pandas.api.types import is_scalar

from .checks import IsInCheck, LengthCheck, PandasValidationColumn, PandasValidationFrame


def _normalize_missing(value):
    """Normalize scalar pandas missing values without testing arrays for truth."""
    return None if is_scalar(value) and pd.isna(value) else value


class PandasForm:
    """Validate an iterable of mappings or a DataFrame using clean_<field> hooks.

    Errors and cleaned_data use zero-based row positions, even for DataFrame
    input. Validation is cached per instance; create a new form for new data.
    """

    class Meta:
        fields = ()

    def __init__(self, data=None):
        self.opts = self.Meta
        self.fields = self.get_fields()
        self.is_bound = data is not None
        self.data = data
        if isinstance(data, pd.DataFrame):
            if not data.columns.is_unique:
                raise ValueError("DataFrame column names must be unique.")
            self._rows = data.to_dict(orient="records")
        elif data is None:
            self._rows = []
        elif isinstance(data, (str, bytes, Mapping)) or not isinstance(data, Iterable):
            raise TypeError("data must be a DataFrame or an iterable of row mappings.")
        else:
            self._rows = list(data)
        if any(not isinstance(row, Mapping) for row in self._rows):
            raise TypeError("Each data row must be a mapping.")
        self.cached_valid = None
        self._is_cleaned = False
        self.schema = None
        self.pandas_data = None
        self.cleaned_data = {}
        self.errors = {}
        self.row_data = {}
        self.row_index = None

    def get_fields(self):
        fields = self.opts.fields
        if isinstance(fields, str) or not isinstance(fields, (list, tuple)):
            raise ImproperlyConfigured("Meta.fields must be a list or tuple of field names.")
        if any(not isinstance(name, str) for name in fields) or len(set(fields)) != len(fields):
            raise ImproperlyConfigured("Meta.fields must contain unique string names.")
        return tuple(fields)

    def handle_default(self, item):
        return None

    def _clean_field(self, item, value):
        return value

    def clean(self):
        """Optional cross-field hook, called once per row after field cleaning."""
        return self.row_data

    def add_error(self, field, error, row=None):
        """Add a ValidationError globally or to a row, including row zero."""
        error = ValidationError(error)
        if hasattr(error, "error_dict"):
            if field is not None:
                raise TypeError("A dictionary of errors requires field=None.")
            for name, messages in error.error_dict.items():
                self.add_error(name, messages, row=row)
            return
        name = field or NON_FIELD_ERRORS
        if row is None:
            self.errors.setdefault(NON_FIELD_ERRORS, []).extend(error.error_list)
        else:
            self.errors.setdefault(row, {}).setdefault(name, []).extend(error.error_list)
            self.cleaned_data.get(row, {}).pop(name, None)
        self.cached_valid = False

    def _clean_data(self):
        for index, values in enumerate(self._rows):
            self.row_index = index
            self.row_data = self.cleaned_data.setdefault(index, {})
            for item in self.fields:
                try:
                    value = values[item] if item in values else self.handle_default(item)
                    self.row_data[item] = self._clean_field(item, _normalize_missing(value))
                    cleaner = getattr(self, f"clean_{item}", None)
                    if cleaner is not None:
                        value = cleaner()
                        if item not in self.errors.get(index, {}):
                            self.row_data[item] = value
                except ValidationError as error:
                    self.add_error(item, error, row=index)
            try:
                cleaned = self.clean()
                if cleaned is not None:
                    if not isinstance(cleaned, Mapping):
                        raise TypeError("clean() must return a mapping or None.")
                    self.cleaned_data[index] = dict(cleaned)
            except ValidationError as error:
                self.add_error(None, error, row=index)
            # A row cleaner must not put invalid fields back into cleaned_data.
            for field in self.errors.get(index, {}):
                self.cleaned_data[index].pop(field, None)
        self.row_data = {}
        self.row_index = None

    def is_valid(self):
        if self._is_cleaned:
            return self.cached_valid
        if not self.is_bound:
            self.cached_valid = False
            self._is_cleaned = True
            return False
        self._clean_data()
        self.pandas_data = pd.DataFrame(
            self.cleaned_data.values(), columns=self.fields, dtype=object
        )
        if self.schema is not None:
            self.schema.validate(self.pandas_data)
            for index, fields in self.schema.errors.items():
                for field, messages in fields.items():
                    if field not in self.errors.get(index, {}):
                        self.add_error(field, messages, row=index)
        self.cached_valid = not self.errors
        self._is_cleaned = True
        return self.cached_valid


class PandasModelForm(PandasForm):
    """Validate selected Django model fields without creating or saving objects.

    Related values are fetched once per field, lazily during validation.
    Model.clean(), uniqueness and database constraints are not evaluated.
    """

    class Meta:
        fields = ()
        model = None

    def __init__(self, data=None):
        super().__init__(data)
        self.cached_relations = {}
        self.init_validation_types()
        self.schema = PandasValidationFrame(self.validation_fields)

    def get_fields(self):
        self.model = getattr(self.opts, "model", None)
        if not isinstance(self.model, type) or not issubclass(self.model, models.Model):
            raise ImproperlyConfigured("PandasModelForm.Meta.model must be a Django model.")
        if self.opts.fields == "__all__":
            names = tuple(
                field.name
                for field in (*self.model._meta.fields, *self.model._meta.many_to_many)
                if field.editable and not field.primary_key
            )
        else:
            names = super().get_fields()
        self.model_fields = {}
        for name in names:
            try:
                field = self.model._meta.get_field(name)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(f"Unknown model field: {name}") from error
            if field.auto_created or not field.editable:
                raise ImproperlyConfigured(f"Field is not editable: {name}")
            self.model_fields[name] = field
        return names

    def handle_default(self, item):
        field = self.model_fields[item]
        if field.many_to_many and not field.has_default():
            return []
        return field.get_default()

    def handle_relation(self, curr_field):
        name = curr_field.name
        if name not in self.cached_relations:
            target = (
                curr_field.target_field
                if not curr_field.many_to_many
                else curr_field.remote_field.model._meta.pk
            )
            self.cached_relations[name] = frozenset(
                curr_field.related_model._default_manager.values_list(target.name, flat=True)
            )
        return [IsInCheck(name, self.cached_relations[name])]

    def handle_type(self, curr_field):
        checks = []
        if isinstance(curr_field, (models.CharField, models.TextField)) and curr_field.max_length:
            checks.append(LengthCheck(curr_field.name, curr_field.max_length))
        if curr_field.choices:
            choices = [key for key, _ in curr_field.flatchoices]
            if curr_field.blank:
                choices.append("")
            checks.append(IsInCheck(curr_field.name, choices))
        return PandasValidationColumn(
            curr_field.name,
            checks=checks,
            nullable=bool(curr_field.null or curr_field.blank),
        )

    def init_validation_types(self):
        self.validation_fields = {
            name: self.handle_type(field) for name, field in self.model_fields.items()
        }

    def _validate_relation(self, field, values):
        if field.name not in self.cached_relations:
            self.handle_relation(field)
        allowed = self.cached_relations[field.name]
        if any(value not in allowed for value in values):
            raise ValidationError(_("Select a valid choice."), code="invalid_choice")

    def _clean_field(self, item, value):
        field = self.model_fields[item]
        if field.many_to_many:
            if value is None:
                value = []
            if not isinstance(value, (list, tuple, set, frozenset)):
                raise ValidationError(_("Enter a list of related keys."), code="invalid_list")
            if not value and not field.blank:
                raise ValidationError(_("This field cannot be blank."), code="blank")
            target = field.remote_field.model._meta.pk
            values = [target.clean(_normalize_missing(key), None) for key in value]
            if values:
                self._validate_relation(field, values)
            return values
        if field.is_relation:
            value = field.to_python(value)
            # ForeignKey.validate() queries once per row. Use Field's null/blank/
            # choice validation, then check membership against the cached keys.
            models.Field.validate(field, value, None)
            field.run_validators(value)
            if value is not None and value != "":
                self._validate_relation(field, [value])
            return value
        return field.clean(value, None)
