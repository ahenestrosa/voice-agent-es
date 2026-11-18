"""The two demo Customers, and the reset that puts the database back to them."""

# Standard library
from datetime import date
from decimal import Decimal

# Third-party
from sqlalchemy import Connection, delete, insert

# Local
from backend.models import CardKind, CardRow, CardStatus, CustomerRow, TransactionRow
from backend.tables import METADATA, cards_t, customers_t, transactions_t

DEMO_CUSTOMERS: list[CustomerRow] = [
    {"id": 1, "full_name": "Lucía Fernández", "dni": "30123456", "balance_ars": Decimal("1254300.50")},
    {"id": 2, "full_name": "Martín Gómez", "dni": "27987654", "balance_ars": Decimal("82000.00")},
]
DEMO_CARDS: list[CardRow] = [
    {"id": 1, "customer_id": 1, "last4": "4821", "kind": CardKind.DEBIT, "status": CardStatus.ACTIVE},
    {"id": 2, "customer_id": 2, "last4": "1190", "kind": CardKind.CREDIT, "status": CardStatus.ACTIVE},
]
DEMO_TRANSACTIONS: list[TransactionRow] = [
    {"id": 1, "customer_id": 1, "reference": "t_9001", "booked_on": date(2026, 11, 7), "merchant": "Supermercado Día", "amount_ars": Decimal("-38450.00")},
    {"id": 2, "customer_id": 1, "reference": "t_9002", "booked_on": date(2026, 11, 6), "merchant": "SUBE recarga", "amount_ars": Decimal("-10000.00")},
    {"id": 3, "customer_id": 1, "reference": "t_9003", "booked_on": date(2026, 11, 5), "merchant": "Transferencia recibida", "amount_ars": Decimal("450000.00")},
    {"id": 4, "customer_id": 1, "reference": "t_9004", "booked_on": date(2026, 11, 4), "merchant": "MercadoLibre", "amount_ars": Decimal("-129999.00")},
    {"id": 5, "customer_id": 2, "reference": "t_8001", "booked_on": date(2026, 11, 8), "merchant": "YPF", "amount_ars": Decimal("-45000.00")},
]


def reset_demo_data(conn: Connection) -> None:
    """Empty every table and load the demo Customers, their cards and their movements.

    How it works:
    1. Delete every row, children before parents, so no foreign key blocks a delete.
    2. Insert the customers first, then the rows that point at them.
    """
    # 1. reversed(sorted_tables) lists each table after the tables that reference it.
    for table in reversed(METADATA.sorted_tables):
        conn.execute(delete(table))

    # 2. Explicit IDs keep the demo data identical on every reset.
    conn.execute(insert(customers_t), DEMO_CUSTOMERS)
    conn.execute(insert(cards_t), DEMO_CARDS)
    conn.execute(insert(transactions_t), DEMO_TRANSACTIONS)
