from django.urls import path

from .views import WalletDetailView, WalletLedgerListView

urlpatterns = [
    path("wallet/", WalletDetailView.as_view(), name="wallet-detail"),
    path("wallet/ledger/", WalletLedgerListView.as_view(), name="wallet-ledger"),
]
