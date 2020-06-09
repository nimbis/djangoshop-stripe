# -*- coding: utf-8 -*-

from __future__ import absolute_import

import logging
import stripe
from django.db import models
from django.conf import settings

logger = logging.getLogger(__name__)


class StripeCustomer(models.Model):
    """
    A simple model linking a site user with Stripe
    customer created using stripe API.
    """

    class Meta:
        verbose_name = "Stripe Customer"
        verbose_name_plural = "Stripe Customers"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    stripe_customer_id = models.CharField(
        help_text="Stripe customer id.",
        max_length=255,
        blank=True)
