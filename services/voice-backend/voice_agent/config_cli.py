"""Explicit configuration bootstrap; never loads models."""
from .config_store import ConfigStore


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bootstrap"])
    parser.parse_args()
    store = ConfigStore.bootstrap()
    print(f"Configuration: {store.root}")
    print(f"Revision: {store.public_snapshot()['settings']['revision']}")


if __name__ == "__main__":
    main()
