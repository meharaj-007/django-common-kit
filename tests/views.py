"""Views the exception-handler tests drive through the real DRF stack.

Asserting on the handler by calling it directly proves less than it looks: the
thing that actually breaks is the wiring — a handler DRF never reaches, headers
dropped between the exception and the envelope. These go through a request.
"""

from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from django_common_utils.api.response import ApiResponse


class SampleSerializer(serializers.Serializer):
    email = serializers.EmailField()
    start_time = serializers.CharField()


class ValidatedView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SampleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return ApiResponse.success(data=serializer.validated_data)


class BoomView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise AttributeError("'NoneType' object has no attribute 'organisation_id'")


class SecondRequestThrottle(SimpleRateThrottle):
    """Allows one request a minute, keyed on a constant so the test controls it."""

    scope = "test_always"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": "throttle-test"}


class ThrottledView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SecondRequestThrottle]

    def get(self, request):
        return ApiResponse.success()
