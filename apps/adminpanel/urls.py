from django.urls import path

from .views import (
    AdminOrderCancelView,
    AdminOrderListView,
    AdminOverviewView,
    AdminPositionListView,
    AdminTradeListView,
    AdminUserDetailView,
    AdminUserLedgerView,
    AdminUserListView,
    AdminWalletListView,
)

urlpatterns = [
    path("overview/", AdminOverviewView.as_view(), name="admin-overview"),
    path("users/", AdminUserListView.as_view(), name="admin-user-list"),
    path(
        "users/<int:pk>/",
        AdminUserDetailView.as_view(),
        name="admin-user-detail",
    ),
    path(
        "users/<int:pk>/ledger/",
        AdminUserLedgerView.as_view(),
        name="admin-user-ledger",
    ),
    path("wallets/", AdminWalletListView.as_view(), name="admin-wallet-list"),
    path("orders/", AdminOrderListView.as_view(), name="admin-order-list"),
    path(
        "orders/<int:pk>/cancel/",
        AdminOrderCancelView.as_view(),
        name="admin-order-cancel",
    ),
    path(
        "positions/",
        AdminPositionListView.as_view(),
        name="admin-position-list",
    ),
    path("trades/", AdminTradeListView.as_view(), name="admin-trade-list"),
]
