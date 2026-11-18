"""Reset the demo database to the two test Customers, e.g. after a demo call blocked a card.

Usage: uv run python -m scripts.seed_db
"""

# Local
from backend.db import transaction
from backend.demo_data import reset_demo_data


def main() -> None:
    """Replace every row in the DATABASE_URL database with the demo data, in one transaction."""
    with transaction() as conn:
        reset_demo_data(conn)
    print("demo data loaded: Lucía (DNI 30123456, card 4821) and Martín (DNI 27987654, card 1190)")


if __name__ == "__main__":
    main()
