from django.db import models


class Product(models.Model):
    code = models.SlugField(primary_key=True, max_length=64)
    name = models.CharField(max_length=160)
    description = models.TextField()
    min_amount = models.DecimalField(max_digits=14, decimal_places=2)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2)
    min_term_months = models.PositiveSmallIntegerField()
    max_term_months = models.PositiveSmallIntegerField()
    badge = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("code",)

    def __str__(self):
        return self.name
