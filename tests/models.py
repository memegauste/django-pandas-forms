import uuid

from django.core.validators import MinValueValidator
from django.db import models


class Category(models.Model):
    code = models.CharField(primary_key=True, max_length=12)
    slug = models.CharField(unique=True, max_length=20)


class Tag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Product(models.Model):
    name = models.CharField(max_length=12)
    count = models.IntegerField(default=7, validators=[MinValueValidator(0)])
    active = models.BooleanField(default=True)
    price = models.DecimalField(max_digits=6, decimal_places=2, default="0.00")
    status = models.CharField(
        max_length=12,
        choices=[("Available", [("new", "New"), ("used", "Used")])],
        default="new",
    )
    optional_status = models.CharField(max_length=12, blank=True, choices=[("new", "New")])
    note = models.CharField(max_length=12, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, null=True, blank=True)
    category_slug = models.ForeignKey(
        Category,
        to_field="slug",
        on_delete=models.CASCADE,
        related_name="slug_products",
        null=True,
        blank=True,
    )
    tags = models.ManyToManyField(Tag, blank=True)
    required_tags = models.ManyToManyField(Tag, related_name="required_products")
    external_id = models.UUIDField(default=uuid.uuid4)
    published = models.DateField(null=True, blank=True)


class Profile(models.Model):
    category = models.OneToOneField(Category, on_delete=models.CASCADE)
