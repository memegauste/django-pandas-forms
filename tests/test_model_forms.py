import datetime
import uuid
from decimal import Decimal

import pandas as pd
import pytest
from django.core.exceptions import ImproperlyConfigured

from django_pandas_forms import PandasModelForm

from .models import Category, Product, Profile, Tag


class ProductForm(PandasModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "count",
            "active",
            "price",
            "status",
            "note",
            "category",
            "category_slug",
            "tags",
            "external_id",
            "published",
        ]


def test_model_defaults_are_called_per_row_and_falsy_values_are_preserved():
    form = ProductForm([{"name": "one"}, {"name": "two", "count": 0, "active": False}])
    assert form.is_valid()
    first, second = form.cleaned_data.values()
    assert first["count"] == 7
    assert first["active"] is True
    assert first["price"] == Decimal("0.00")
    assert first["status"] == "new"
    assert first["tags"] == []
    assert isinstance(first["external_id"], uuid.UUID)
    assert first["external_id"] != second["external_id"]
    assert second["count"] == 0
    assert second["active"] is False


def test_model_fields_coerce_values_and_validate_grouped_choices():
    form = ProductForm(
        [
            {
                "name": "example",
                "count": "2",
                "active": "False",
                "price": "12.50",
                "status": "used",
                "published": "2026-10-19",
            }
        ]
    )
    assert form.is_valid()
    row = form.cleaned_data[0]
    assert row["count"] == 2
    assert row["active"] is False
    assert row["price"] == Decimal("12.50")
    assert row["published"] == datetime.date(2026, 10, 19)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "too long a product name"),
        ("name", ""),
        ("name", None),
        ("count", -1),
        ("count", "not an integer"),
        ("count", None),
        ("active", "maybe"),
        ("price", "12345.67"),
        ("price", "1.234"),
        ("status", "unknown"),
        ("external_id", "not a uuid"),
        ("published", "not a date"),
    ],
)
def test_model_field_validation_errors(field, value):
    form = ProductForm([{"name": "valid", field: value}])
    assert form.is_valid() is False
    assert field in form.errors[0]
    assert len(form.errors[0][field]) == 1
    assert field not in form.cleaned_data[0]


def test_dataframe_nulls_do_not_bypass_required_fields_or_call_defaults():
    form = ProductForm(
        pd.DataFrame(
            [
                {"name": "one", "count": pd.NA, "note": pd.NA},
                {"name": "two", "count": 0, "note": ""},
            ]
        )
    )
    assert not form.is_valid()
    assert "count" in form.errors[0]
    assert form.cleaned_data[0]["note"] is None
    assert form.cleaned_data[1]["count"] == 0
    assert form.cleaned_data[1]["note"] == ""


@pytest.mark.django_db
def test_relations_are_lazy_cached_and_support_non_integer_keys(django_assert_num_queries):
    category = Category.objects.create(code="books", slug="book-slug")
    tag = Tag.objects.create()
    with django_assert_num_queries(0):
        form = ProductForm(
            [
                {
                    "name": "first",
                    "category": category.pk,
                    "category_slug": category.slug,
                    "tags": [str(tag.pk)],
                },
                {
                    "name": "second",
                    "category": category.pk,
                    "category_slug": category.slug,
                    "tags": [tag.pk],
                },
            ]
        )
    with django_assert_num_queries(3):
        assert form.is_valid()
    assert form.cleaned_data[0]["tags"] == [tag.pk]
    assert form.cleaned_data[0]["category_slug"] == category.slug
    with django_assert_num_queries(0):
        assert form.is_valid()
    assert Product.objects.count() == 0


@pytest.mark.django_db
def test_missing_relations_and_bad_relation_types_are_rejected():
    form = ProductForm(
        [
            {
                "name": "first",
                "category": "missing",
                "category_slug": "missing",
                "tags": [str(uuid.uuid4())],
            },
            {"name": "second", "tags": "not a list"},
            {"name": "third", "tags": ["not a uuid"]},
        ]
    )
    assert form.is_valid() is False
    assert set(form.errors[0]) == {"category", "category_slug", "tags"}
    assert form.errors[1]["tags"][0].code == "invalid_list"
    assert "tags" in form.errors[2]


@pytest.mark.django_db
def test_many_to_many_blank_rules_and_empty_collections(django_assert_num_queries):
    class TagsForm(PandasModelForm):
        class Meta:
            model = Product
            fields = ["required_tags", "tags"]

    with django_assert_num_queries(0):
        form = TagsForm([{"required_tags": [], "tags": []}])
        assert form.is_valid() is False
    assert form.errors[0]["required_tags"][0].code == "blank"
    tag = Tag.objects.create()
    valid = TagsForm([{"required_tags": (tag.pk,), "tags": {tag.pk}}])
    assert valid.is_valid()


@pytest.mark.django_db
def test_one_to_one_relations_use_target_keys():
    category = Category.objects.create(code="cat", slug="category")

    class ProfileForm(PandasModelForm):
        class Meta:
            model = Profile
            fields = ["category"]

    form = ProfileForm([{"category": category.pk}, {"category": "missing"}])
    assert form.is_valid() is False
    assert form.cleaned_data[0]["category"] == category.pk
    assert form.errors[1]["category"][0].code == "invalid_choice"


def test_relation_caches_are_not_shared_between_instances():
    first, second = ProductForm([]), ProductForm([])
    first.cached_relations["category"] = {"only-first"}
    assert second.cached_relations == {}


def test_all_fields_excludes_auto_primary_key():
    class AllFieldsForm(PandasModelForm):
        class Meta:
            model = Product
            fields = "__all__"

    form = AllFieldsForm([])
    assert "id" not in form.fields
    assert "required_tags" in form.fields
    assert form.is_valid()


def test_blank_choices_follow_django_field_rules():
    class OptionalStatusForm(PandasModelForm):
        class Meta:
            model = Product
            fields = ["optional_status"]

    form = OptionalStatusForm([{}, {"optional_status": ""}, {"optional_status": "new"}])
    assert form.is_valid()
    assert form.cleaned_data[0]["optional_status"] == ""


@pytest.mark.parametrize("model", [None, object, "Product"])
def test_missing_or_invalid_model_is_a_configuration_error(model):
    class BadForm(PandasModelForm):
        pass

    BadForm.Meta = type("Meta", (), {"model": model, "fields": []})
    with pytest.raises(ImproperlyConfigured, match="Django model"):
        BadForm([])


@pytest.mark.parametrize("field", ["missing", "id"])
def test_invalid_model_fields_are_configuration_errors(field):
    class BadForm(PandasModelForm):
        class Meta:
            model = Product
            fields = [field]

    with pytest.raises(ImproperlyConfigured):
        BadForm([])
