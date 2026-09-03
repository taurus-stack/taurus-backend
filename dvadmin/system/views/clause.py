from rest_framework.views import APIView
from django.shortcuts import render


class PrivacyView(APIView):
    """
    Backend privacy policy
    """
    permission_classes = []

    def get(self, request, *args, **kwargs):
        return render(request, 'privacy.html')



class TermsServiceView(APIView):
    """
    Backend service terms
    """
    permission_classes = []

    def get(self, request, *args, **kwargs):
        return render(request, 'terms_service.html')