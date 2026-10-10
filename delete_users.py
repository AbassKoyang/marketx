import os
import django

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.account.models import User
from apps.wallet.models import Wallet, WalletLedgerEntry

def delete_all_users():
    try:
        # We must delete WalletLedgerEntries and Wallets first 
        # because Wallet has on_delete=models.PROTECT pointing to User.
        print("Deleting wallet ledger entries...")
        WalletLedgerEntry.objects.all().delete()
        
        print("Deleting wallets...")
        Wallet.objects.all().delete()
        
        print("Deleting users...")
        deleted_count, _ = User.objects.all().delete()
        
        print(f"Success! Deleted {deleted_count} users and all associated wallets.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == '__main__':
    delete_all_users()
