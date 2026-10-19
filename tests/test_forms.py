from unittest.mock import Mock

import pandas as pd
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError

from django_pandas_forms import (
    IsInCheck,
    PandasForm,
    PandasValidationColumn,
    PandasValidationFrame,
)


class ValueForm(PandasForm):
    class Meta:
        fields = ["value"]


def test_unbound_form_is_invalid_and_empty_bound_form_is_valid():
    form = ValueForm()
    assert form.is_valid() is False
    assert form.is_valid() is False
    assert form.errors == {}
    empty = ValueForm([])
    assert empty.is_valid() is True
    assert list(empty.pandas_data.columns) == ["value"]


@pytest.mark.parametrize("value", [0, False, "", None, [], {}])
def test_falsy_values_are_preserved(value):
    form = ValueForm([{"value": value, "ignored": "not a form field"}])
    assert form.is_valid() is True
    assert form.cleaned_data == {0: {"value": value}}


def test_missing_values_and_defaults():
    class DefaultForm(ValueForm):
        def handle_default(self, item):
            return 12

    form = DefaultForm([{}, {"value": None}, {"value": 0}])
    assert form.is_valid()
    assert form.cleaned_data == {0: {"value": 12}, 1: {"value": None}, 2: {"value": 0}}
    assert ValueForm([{}]).is_valid()


def test_dataframe_missing_values_are_normalized_and_input_is_not_changed():
    data = pd.DataFrame({"value": [pd.NA, float("nan"), pd.NaT]}, index=[9, 5, 7])
    original = data.copy(deep=True)
    form = ValueForm(data)
    assert form.is_valid()
    assert form.cleaned_data == {index: {"value": None} for index in range(3)}
    pd.testing.assert_frame_equal(data, original)


def test_cleaners_run_once_and_errors_include_first_row():
    class CleanForm(ValueForm):
        calls = 0

        def clean_value(self):
            self.calls += 1
            assert self.row_index in (0, 1)
            if self.row_data["value"] < 0:
                raise ValidationError("Must be positive", code="positive")
            return self.row_data["value"] * 2

    form = CleanForm([{"value": -1}, {"value": 2}])
    assert form.is_valid() is False
    assert form.is_valid() is False
    assert form.calls == 2
    assert form.errors[0]["value"][0].code == "positive"
    assert form.cleaned_data == {0: {}, 1: {"value": 4}}
    assert form.row_data == {}
    assert form.row_index is None


def test_add_error_handles_row_zero_and_multiple_errors():
    form = ValueForm([{"value": 1}])
    assert form.is_valid()
    form.add_error("value", "first", row=0)
    form.add_error("value", ValidationError("second", code="second"), row=0)
    form.add_error(None, "row error", row=0)
    form.add_error(None, "dataset error")
    form.add_error(None, "another dataset error")
    assert form.is_valid() is False
    assert len(form.errors[0]["value"]) == 2
    assert form.errors[0]["__all__"][0].message == "row error"
    assert len(form.errors["__all__"]) == 2
    assert form.cleaned_data[0] == {}


def test_add_dictionary_errors():
    form = ValueForm([{"value": 1}])
    form.add_error(None, {"value": ["one", "two"]}, row=0)
    assert form.is_valid() is False
    assert len(form.errors[0]["value"]) == 2
    assert form.cleaned_data[0] == {}
    with pytest.raises(TypeError, match="field=None"):
        form.add_error("value", {"value": "error"}, row=0)


def test_row_cleaner_can_transform_or_reject_rows():
    class CleanForm(ValueForm):
        def clean(self):
            if self.row_data["value"] < 0:
                raise ValidationError("Invalid row")
            return {"value": self.row_data["value"] + 1}

    form = CleanForm([{"value": 2}, {"value": -1}])
    assert form.is_valid() is False
    assert form.cleaned_data[0] == {"value": 3}
    assert form.errors[1]["__all__"][0].message == "Invalid row"


def test_cleaner_can_add_error_without_raising():
    class CleanForm(ValueForm):
        def clean_value(self):
            self.add_error("value", "Invalid", row=self.row_index)
            return "must not reappear"

    form = CleanForm([{"value": 2}])
    assert form.is_valid() is False
    assert form.cleaned_data[0] == {}


def test_clean_can_return_none():
    class CleanForm(ValueForm):
        def clean(self):
            return None

    form = CleanForm([{"value": 1}])
    assert form.is_valid()
    assert form.cleaned_data[0] == {"value": 1}


def test_invalid_row_cleaner_result_is_reported_as_programming_error():
    class CleanForm(ValueForm):
        def clean(self):
            return 42

    with pytest.raises(TypeError, match="mapping"):
        CleanForm([{"value": 1}]).is_valid()


def test_schema_errors_are_merged_with_cleaner_errors():
    class CleanForm(ValueForm):
        def clean_value(self):
            if self.row_index == 0:
                raise ValidationError("Custom error")
            return self.row_data["value"]

    form = CleanForm([{"value": 1}, {"value": 2}])
    form.schema = PandasValidationFrame(
        {"value": PandasValidationColumn("value", checks=[IsInCheck("value", [3])])}
    )
    assert form.is_valid() is False
    assert len(form.errors[0]["value"]) == 1
    assert form.errors[1]["value"][0].code == "invalid_choice"
    assert form.cleaned_data == {0: {}, 1: {}}


def test_generator_input_is_materialized_once():
    form = ValueForm({"value": index} for index in range(3))
    assert form.is_valid()
    assert form.is_valid()
    assert len(form.cleaned_data) == 3


def test_errors_and_fields_are_not_shared_between_instances():
    first, second = ValueForm([]), ValueForm([])
    first.add_error(None, "only first")
    assert not first.is_valid()
    assert second.is_valid()
    assert ValueForm.Meta.fields == ["value"]


@pytest.mark.parametrize("data", ["text", b"text", 42, {"value": 1}, [[1]], [None]])
def test_invalid_input_is_rejected(data):
    with pytest.raises(TypeError):
        ValueForm(data)


def test_duplicate_dataframe_columns_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        ValueForm(pd.DataFrame([[1, 2]], columns=["value", "value"]))


@pytest.mark.parametrize("fields", ["value", [1], ["value", "value"], None])
def test_invalid_meta_fields(fields):
    class BadForm(PandasForm):
        pass

    BadForm.Meta = type("Meta", (), {"fields": fields})
    with pytest.raises(ImproperlyConfigured):
        BadForm([])


def test_valid_result_is_cached():
    form = ValueForm([{"value": 2}])
    form.clean_value = Mock(return_value=3)
    assert form.is_valid()
    assert form.is_valid()
    form.clean_value.assert_called_once_with()
