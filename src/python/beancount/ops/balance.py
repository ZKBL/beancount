"""Automatic padding of gaps between entries.
"""
import collections

from beancount.core.amount import D, amount_sub
from beancount.core.data import Transaction, Balance
from beancount.core import inventory
from beancount.core import realization
from beancount.core import getters

__plugins__ = ('check',)


BalanceError = collections.namedtuple('BalanceError', 'fileloc message entry')


# This is based on some real-world usage: FOREX brokerage, for instance,
# accumulates error up to 1bp, and we need to tolerate that if our importers
# insert checks on at regular spaces, so we set the maximum limit at 1bp.
# FIXME: Move this up to options?
CHECK_PRECISION = D('.015')

def check(entries, unused_options_map):
    """Process the balance assertion directives.

    For each Balance directive, check that their expected balance corresponds to
    the actual balance computed at that time and replace failing ones by new
    ones with a flag that indicates failure.

    Args:
      entries: A list of directives.
      unused_options_map: A dict of options, parsed from the input file.
    Returns:
      A pair of a list of directives and a list of balance check errors.
    """
    new_entries = []
    check_errors = []

    # This is similar to realization, but performed in a different order, and
    # where we only accumulate inventories for accounts that have balance
    # assertions in them (this saves on time). Here we process the entries one
    # by one along with the balance checks. We use a temporary realization in
    # order to hold the incremental tree of balances, so that we can easily get
    # the amounts of an account's subaccounts for making checks on parent
    # accounts.
    real_root = realization.RealAccount('')

    # Figure out the set of accounts for which we need to compute a running
    # inventory balance.
    asserted_accounts = {entry.account
                         for entry in entries
                         if isinstance(entry, Balance)}

    # Add all children accounts of an asserted account to be calculated as well,
    # and pre-create these accounts, and only those (we're just being tight to
    # make sure).
    for account in getters.get_accounts(entries):
        if (account in asserted_accounts or
            any(account.startswith(asserted_account)
                for asserted_account in asserted_accounts)):
            realization.get_or_create(real_root, account)

    for entry in entries:
        if isinstance(entry, Transaction):
            # For each of the postings' accounts, update the balance inventory.
            for posting in entry.postings:
                real_account = realization.get(real_root, posting.account)

                # The account will have been created only if we're meant to track it.
                if real_account is not None:
                    # Note: Always allow negative lots for the purpose of balancing.
                    # This error should show up somewhere else than here.
                    real_account.balance.add_position(posting.position, True)

        elif isinstance(entry, Balance):
            # Check the balance against the check entry.
            expected_amount = entry.amount

            real_account = realization.get(real_root, entry.account)
            assert real_account is not None, "Missing {}".format(entry.account)

            # Sum up the current balances for this account and its
            # sub-accounts. We want to support checks for parent accounts
            # for the total sum of their subaccounts.
            subtree_balance = inventory.Inventory()
            for real_child in realization.iter_children(real_account, False):
                subtree_balance += real_child.balance

            # Get only the amount in the desired currency.
            balance_amount = subtree_balance.get_amount(expected_amount.currency)

            # Check if the amount is within bounds of the expected amount.
            diff_amount = amount_sub(balance_amount, expected_amount)
            if abs(diff_amount.number) > CHECK_PRECISION:
                check_errors.append(
                    BalanceError(entry.fileloc,
                                 ("Balance failed for '{}': "
                                  "expected {} != accumulated {} ({} {})").format(
                                      entry.account, balance_amount, expected_amount,
                                      diff_amount,
                                      'too much' if diff_amount else 'too little'),
                                 entry))

                # Substitute the entry by a failing entry, with the diff_amount
                # field set on it. I'm not entirely sure that this is the best
                # of ideas, maybe leaving the original check intact and insert a
                # new error entry might be more functional or easier to
                # understand.
                entry = Balance(entry.fileloc, entry.date, entry.account,
                                entry.amount, diff_amount)

        new_entries.append(entry)

    return new_entries, check_errors
