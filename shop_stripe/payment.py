from django.conf import settings
from django.utils.text import format_lazy
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.models import User
from rest_framework.exceptions import ValidationError
import stripe
from shop.payment.providers import PaymentProvider
from shop.models.order import BaseOrder, OrderModel, OrderPayment
from django_fsm import transition
from models import StripeCustomer


class StripePayment(PaymentProvider):
    """
    Provides a payment service for Stripe.
    """
    namespace = 'stripe-payment'

    def get_payment_request(self, cart, request):
        """
        From the given request, add a snippet to the page.
        """
        stripe.api_key = settings.SHOP_STRIPE['APIKEY']
        stripe.api_version = settings.SHOP_STRIPE['API_VERSION']

        try:
            self.charge(cart, request)
            self.subscribe(cart, request)
            thank_you_url = OrderModel.objects.get_latest_url()
            js_expression = '$window.location.href="{}";'.format(thank_you_url)
            return js_expression
        except stripe.error.StripeError as err:
            raise ValidationError(err._message)
        except KeyError as err:
            raise ValidationError(str(err))

    def charge(self, cart, request):
        """
        Use the Stripe token from the request and charge immediately.
        This view is invoked by the Javascript function `scope.charge()` delivered
        by `get_payment_request`.
        """
        token_id = cart.extra['payment_extra_data'].get('token_id')
        if not token_id:
            message = _("Stripe payment token is missing")
            raise stripe.error.StripeError(message)

        order = OrderModel.objects.create_from_cart(cart, request)
        charge = stripe.Charge.create(
            amount=cart.total.as_integer(),
            currency=cart.total.currency,
            source=token_id,
            transfer_group=order.get_number(),
            description=settings.SHOP_STRIPE['PURCHASE_DESCRIPTION'],
        )
        if charge['status'] == 'succeeded':
            order.populate_from_cart(cart, request)
            order.add_stripe_payment(charge)
            order.save(with_notification=True)
        else:
            message = _("Stripe returned status '{status}' for id: {id}")
            raise stripe.error.StripeError(format_lazy(message, **charge))
        return order

    def subscribe(self, cart, request):
        """
        Create a new customer and subscribe the customer
        to the default payment plan.
        """
        user_id = cart.customer.user_id;
        user = User.objects.get(id=user_id)

        try:
            customer = StripeCustomer.objects.get(user_id=user_id)
            stripe_customer = stripe.Customer.retrieve(customer.stripe_customer_id)
        except StripeCustomer.DoesNotExist:
            stripe_customer = stripe.Customer.create(
                email=user.email,
                description=u"{0}, {1}".format(
                    user.last_name,
                    user.first_name))

            StripeCustomer.objects.create(
                user_id=user_id,
                stripe_customer_id=stripe_customer.id)

        stripe_customer.subscriptions.create(
            plan=settings.DEFAULT_PAYMENT_PLAN)


class OrderWorkflowMixin(object):
    TRANSITION_TARGETS = {
        'paid_with_stripe': _("Paid using Stripe"),
    }

    def __init__(self, *args, **kwargs):
        if not isinstance(self, BaseOrder):
            raise ImproperlyConfigured('OrderWorkflowMixin is not of type BaseOrder')
        super(OrderWorkflowMixin, self).__init__(*args, **kwargs)

    @transition(field='status', source=['created'], target='paid_with_stripe')
    def add_stripe_payment(self, charge):
        payment = OrderPayment(order=self, transaction_id=charge['id'], payment_method=StripePayment.namespace)
        assert payment.amount.currency == charge['currency'].upper(), "Currency mismatch"
        payment.amount = payment.amount.__class__(Decimal(charge['amount']) / payment.amount.subunits)
        payment.save()

    def is_fully_paid(self):
        return super(OrderWorkflowMixin, self).is_fully_paid()

    @transition(field='status', source='paid_with_stripe', conditions=[is_fully_paid],
        custom=dict(admin=True, button_name=_("Acknowledge Payment")))
    def acknowledge_stripe_payment(self):
        self.acknowledge_payment()
