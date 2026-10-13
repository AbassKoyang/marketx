from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WalletLedgerEntry
from .serializers import WalletLedgerEntrySerializer, WalletSerializer
from .services import get_or_create_wallet


class WalletDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        wallet = get_or_create_wallet(request.user)
        return Response(WalletSerializer(wallet).data)


class WalletLedgerListView(generics.ListAPIView):
    serializer_class = WalletLedgerEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        wallet = get_or_create_wallet(self.request.user)
        return WalletLedgerEntry.objects.filter(wallet=wallet).order_by("-created_at")
