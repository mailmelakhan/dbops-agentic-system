"""Print a bcrypt hash for a password, for config/users.yaml.

Usage: uv run python scripts/hash_password.py
"""

from getpass import getpass

import bcrypt


def main() -> None:
    password = getpass("Password: ").encode()
    print(bcrypt.hashpw(password, bcrypt.gensalt(12)).decode())


if __name__ == "__main__":
    main()
