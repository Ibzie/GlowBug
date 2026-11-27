from enum import StrEnum


class AccountType(StrEnum):
    ASSET = "asset"
    CASH = "cash"
    EXPENSE = "expense"
    REVENUE = "revenue"
    LIABILITY = "liability"
    DEBT = "debt"
    LOAN = "loan"
    MORTGAGE = "mortgage"


class JournalType(StrEnum):
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"
    OPENING = "opening"
    RECONCILIATION = "reconciliation"


ACCOUNT_TYPE_LABELS: dict[AccountType, str] = {
    AccountType.ASSET: "Bank / savings",
    AccountType.CASH: "Cash",
    AccountType.EXPENSE: "Expense",
    AccountType.REVENUE: "Revenue",
    AccountType.LIABILITY: "Liability",
    AccountType.DEBT: "Debt",
    AccountType.LOAN: "Loan",
    AccountType.MORTGAGE: "Mortgage",
}

NET_WORTH_TYPES = {
    AccountType.ASSET,
    AccountType.CASH,
    AccountType.LIABILITY,
    AccountType.DEBT,
    AccountType.LOAN,
    AccountType.MORTGAGE,
}
