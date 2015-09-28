# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json
import stripe
from django.conf import settings
from django.conf.urls import patterns, url
from django.core.exceptions import ImproperlyConfigured
from django.http.response import HttpResponse, HttpResponseBadRequest
from django.utils.translation import ugettext_lazy as _
from shop.models.cart import CartModel
from shop.models.order import BaseOrder, OrderModel, OrderPayment
from shop.payment.base import PaymentProvider
from django_fsm import transition


class StripePayment(PaymentProvider):
    """
    Provides a payment service for Stripe.
    """
    namespace = 'stripe-payment'

    def get_urls(self):
        urlpatterns = patterns('',
            url(r'^charge$', self.charge_view, name='charge'),
            url(r'^save-token$', self.charge_view, name='save-token'),
        )
        return urlpatterns

    def get_payment_request(self, cart, request):
        """
        From the given request, add a snippet to the page.
        """
        js_expression = 'scope.charge().then(function(response) { $window.location.href=response.data.thank_you_url; });'
        return js_expression

    @classmethod
    def save_token_view(cls, request):
        """
        Store the Stripe token in the cart for later usage.
        """
        body = json.loads(request.body)
        cart = CartModel.objects.get_from_request(request)
        cart.payment_method['stripe_token'] = body['token']
        cart.save()

    @classmethod
    def charge_view(cls, request):
        """
        Use the Stripe token from the request and charge immediately.
        """
        stripe.api_key = settings.SHOP_STRIPE['APIKEY']
        body = json.loads(request.body)
        cart = CartModel.objects.get_from_request(request)
        cart.update(request)  # to calculate the total
        try:
            charge = stripe.Charge.create(
                amount=cart.total.as_integer(),
                currency=cart.total.get_currency(),
                source=body['token'],
                description=settings.SHOP_STRIPE['PURCHASE_DESCRIPTION']
            )
            if charge['status'] == 'succeeded':
                order = OrderModel.objects.create_from_cart(cart, request)
                order.add_charge(charge)
                order.save()
                response = {'thank_you_url': OrderModel.objects.get_latest_url()}
                return HttpResponse(json.dumps(response), content_type='application/json;charset=UTF-8')
            return HttpResponseBadRequest(charge)
        except (KeyError, stripe.error.CardError) as err:
            return HttpResponseBadRequest(err)


class OrderWorkflowMixin(object):
    TRANSITION_TARGETS = {
        'charge_credit_card': _("Paid by Credit Card"),
    }

    def __init__(self, *args, **kwargs):
        if not isinstance(self, BaseOrder):
            raise ImproperlyConfigured('OrderWorkflowMixin is not of type BaseOrder')
        super(OrderWorkflowMixin, self).__init__(*args, **kwargs)

    @transition(field='status', source=['created'], target='charge_credit_card')
    def add_charge(self, charge):
        payment = OrderPayment(order=self, transaction_id=charge['id'], payment_method=StripePayment.namespace)
        assert payment.amount.get_currency() == charge['currency'].upper(), "Currency mismatch"
        payment.amount = charge['amount']
        payment.amount /= payment.amount.subunits
        payment.save()