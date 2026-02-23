"""Allow `python -m spiritwriter.secrets` to run the secrets CLI."""
from spiritwriter.secrets.cli import secrets_cli

if __name__ == "__main__":
    secrets_cli()
