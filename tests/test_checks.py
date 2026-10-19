import pandas as pd
import pytest

from django_pandas_forms import (
    FieldCheck,
    IsInCheck,
    LengthCheck,
    PandasValidationColumn,
    PandasValidationFrame,
)


def test_base_check_requires_implementation():
    check = FieldCheck("value")
    with pytest.raises(NotImplementedError):
        check.validate(pd.DataFrame())
    with pytest.raises(NotImplementedError):
        check.get_error()


@pytest.mark.parametrize("nullable", [False, True])
def test_is_in_check_returns_boolean_mask(nullable):
    data = pd.DataFrame({"value": ["yes", "no", None, pd.NA]})
    result = IsInCheck("value", ["yes"]).validate(data, nullable=nullable)
    assert isinstance(result, pd.Series)
    assert result.tolist() == [True, False, nullable, nullable]


@pytest.mark.parametrize("nullable", [False, True])
def test_length_check_handles_nulls_types_and_operator_precedence(nullable):
    data = pd.DataFrame({"value": ["ab", "abcd", "", None, pd.NA, 12]})
    result = LengthCheck("value", 3).validate(data, nullable=nullable)
    assert result.tolist() == [True, False, True, nullable, nullable, False]
    assert LengthCheck("value", 3).get_error().params == {"limit_value": 3}
    assert LengthCheck("value").length == 0
    with pytest.raises(ValueError, match="non-negative"):
        LengthCheck("value", -1)


def test_column_accumulates_errors_using_actual_index_labels():
    data = pd.DataFrame({"value": ["bad", None]}, index=["first", "last"])
    column = PandasValidationColumn(
        "value", checks=[IsInCheck("value", ["ok"]), LengthCheck("value", 2)]
    )
    assert column.validate(data) is False
    assert len(column.errors["first"]["value"]) == 2
    assert column.errors["last"]["value"][0].code == "null"


def test_nullable_column_and_default_checks():
    column = PandasValidationColumn("value", nullable=True)
    assert column.validate(pd.DataFrame({"value": [None]}))
    assert column.errors == {}
    assert PandasValidationColumn("value").validate(pd.DataFrame({"value": [0, False]}))


def test_coercion_does_not_mutate_input():
    data = pd.DataFrame({"value": [12, None]})
    original = data.copy(deep=True)
    column = PandasValidationColumn(
        "value", checks=[LengthCheck("value", 4)], coerce=True, nullable=True
    )
    assert column.validate(data)
    pd.testing.assert_frame_equal(data, original)


def test_missing_column_reports_error_for_every_row():
    column = PandasValidationColumn("value")
    assert column.validate(pd.DataFrame({"other": [1, 2]}, index=[3, 7])) is False
    assert set(column.errors) == {3, 7}
    assert column.errors[3]["value"][0].code == "missing_column"


def test_frame_and_column_state_resets_between_validations():
    column = PandasValidationColumn("value", checks=[IsInCheck("value", [1])])
    frame = PandasValidationFrame({"value": column})
    assert frame.validate(pd.DataFrame({"value": [2]}, index=[8])) is False
    assert set(frame.errors) == {8}
    assert frame.validate(pd.DataFrame({"value": [1]}, index=[3])) is True
    assert frame.errors == column.errors == {}
    assert frame.valid is True
    assert frame.validate(pd.DataFrame({"value": []})) is True
    assert PandasValidationFrame().validate(pd.DataFrame()) is True


def test_frame_merges_columns_and_rejects_duplicate_index():
    frame = PandasValidationFrame(
        {name: PandasValidationColumn(name) for name in ("first", "last")}
    )
    assert frame.validate(pd.DataFrame({"first": [None], "last": [None]}, index=[42])) is False
    assert set(frame.errors[42]) == {"first", "last"}
    with pytest.raises(ValueError, match="unique"):
        frame.validate(pd.DataFrame({"first": [1, 2]}, index=[0, 0]))


@pytest.mark.parametrize("result", [True, pd.Series([True], index=[99])])
def test_custom_checks_must_return_aligned_series(result):
    class BadCheck(FieldCheck):
        def validate(self, df, **kwargs):
            return result

    column = PandasValidationColumn("value", checks=[BadCheck("value")])
    with pytest.raises(TypeError, match="input index"):
        column.validate(pd.DataFrame({"value": [1]}))
