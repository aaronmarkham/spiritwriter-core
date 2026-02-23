"""
CLI commands for secure API key management.

Usable standalone or mounted into a parent CLI (e.g. claude-studio).

Standalone:
    spiritwriter secrets list
    spiritwriter secrets set DISCORD_WEBHOOK_URL
    spiritwriter secrets delete DISCORD_WEBHOOK_URL
    spiritwriter secrets import .env
    spiritwriter secrets check

Mounted (from a parent Click group):
    from spiritwriter.secrets.cli import secrets_cli
    parent_group.add_command(secrets_cli, name="secrets")
"""

import click
from typing import Optional

# Lazy-import rich so the CLI dep is optional at import time.
# If rich isn't installed, fall back to plain print.
def _console():
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None

def _print(msg: str):
    c = _console()
    if c:
        c.print(msg)
    else:
        # Strip rich markup for plain output
        import re
        click.echo(re.sub(r'\[/?[a-z]+\]', '', msg))


@click.group(name="secrets")
@click.option("--service", "-s", default=None,
              help="Keychain service name (default: spiritwriter)")
@click.pass_context
def secrets_cli(ctx, service: Optional[str]):
    """Manage API keys securely using OS keychain."""
    ctx.ensure_object(dict)
    if service:
        from spiritwriter.secrets import configure
        configure(service_name=service)
        ctx.obj["service"] = service


@secrets_cli.command(name="list")
def list_keys():
    """List all API keys and their status."""
    from spiritwriter.secrets import list_api_keys, KNOWN_KEYS, is_keyring_available

    if not is_keyring_available():
        _print(
            "[yellow]Warning:[/yellow] keyring not available. "
            "Install with: pip install keyring"
        )
        _print("Falling back to environment variable check only.\n")

    status = list_api_keys()

    try:
        from rich.table import Table
        console = _console()
        table = Table(title="API Key Status")
        table.add_column("Key", style="cyan")
        table.add_column("Description", style="dim")
        table.add_column("Status", style="bold")

        for key_name, description in KNOWN_KEYS.items():
            key_status = status.get(key_name, "not_set")
            if key_status == "keychain":
                status_display = "[green]Keychain[/green]"
            elif key_status == "env":
                status_display = "[yellow]Env var[/yellow]"
            else:
                status_display = "[red]Not set[/red]"
            table.add_row(key_name, description, status_display)

        console.print(table)
        console.print()
    except ImportError:
        # No rich — plain text fallback
        for key_name, description in KNOWN_KEYS.items():
            key_status = status.get(key_name, "not_set")
            click.echo(f"  {key_name}: {key_status} — {description}")
        click.echo()

    _print("[green]Keychain[/green] = Stored securely in OS credential manager")
    _print("[yellow]Env var[/yellow] = Available via environment variable (less secure)")
    _print("[red]Not set[/red] = Not configured")


@secrets_cli.command(name="set")
@click.argument("key_name")
@click.option("--value", "-v", help="API key value (will prompt if not provided)")
def set_key(key_name: str, value: str = None):
    """Store an API key in the secure keychain."""
    from spiritwriter.secrets import set_api_key, KNOWN_KEYS, is_keyring_available

    if not is_keyring_available():
        _print(
            "[red]Error:[/red] keyring not available. "
            "Install with: pip install keyring"
        )
        return

    key_name = key_name.upper()

    if key_name not in KNOWN_KEYS:
        _print(f"[yellow]Warning:[/yellow] {key_name} is not a recognized key name.")
        if not click.confirm("Store anyway?"):
            return

    if not value:
        value = click.prompt(f"Enter value for {key_name}", hide_input=True)

    if not value:
        _print("[red]Error:[/red] No value provided")
        return

    if set_api_key(key_name, value):
        _print(f"[green]Success:[/green] Stored {key_name} in secure keychain")

        import platform
        system = platform.system()
        if system == "Windows":
            _print("  Location: Windows Credential Manager")
        elif system == "Darwin":
            _print("  Location: macOS Keychain")
        else:
            _print("  Location: Secret Service (GNOME Keyring/KWallet)")
    else:
        _print(f"[red]Error:[/red] Failed to store {key_name}")


@secrets_cli.command(name="get")
@click.argument("key_name")
def get_key(key_name: str):
    """Retrieve an API key value (prints masked by default)."""
    from spiritwriter.secrets import get_api_key

    key_name = key_name.upper()
    value = get_api_key(key_name)
    if value:
        # Show first 8 and last 4 chars, mask the rest
        if len(value) > 16:
            masked = value[:8] + "…" + value[-4:]
        else:
            masked = value[:4] + "…"
        _print(f"[green]{key_name}[/green] = {masked}")
    else:
        _print(f"[red]{key_name}[/red] not found (keychain or env)")


@secrets_cli.command(name="delete")
@click.argument("key_name")
@click.option("--force", "-f", is_flag=True, help="Don't ask for confirmation")
def delete_key(key_name: str, force: bool = False):
    """Delete an API key from the keychain."""
    from spiritwriter.secrets import delete_api_key, is_keyring_available

    if not is_keyring_available():
        _print(
            "[red]Error:[/red] keyring not available. "
            "Install with: pip install keyring"
        )
        return

    key_name = key_name.upper()

    if not force:
        if not click.confirm(f"Delete {key_name} from keychain?"):
            return

    if delete_api_key(key_name):
        _print(f"[green]Success:[/green] Deleted {key_name} from keychain")
    else:
        _print(f"[yellow]Warning:[/yellow] {key_name} not found in keychain")


@secrets_cli.command(name="import")
@click.argument("env_file", type=click.Path(exists=True))
@click.option("--delete-after", is_flag=True, help="Delete .env file after import")
def import_keys(env_file: str, delete_after: bool = False):
    """Import API keys from a .env file into the secure keychain."""
    from spiritwriter.secrets import import_from_env_file, is_keyring_available
    import os

    if not is_keyring_available():
        _print(
            "[red]Error:[/red] keyring not available. "
            "Install with: pip install keyring"
        )
        return

    _print(f"Importing keys from {env_file}...")

    try:
        results = import_from_env_file(env_file)
    except FileNotFoundError:
        _print(f"[red]Error:[/red] File not found: {env_file}")
        return

    if not results:
        _print("[yellow]No API keys found in file[/yellow]")
        return

    success_count = sum(1 for v in results.values() if v)
    fail_count = sum(1 for v in results.values() if not v)

    for key_name, success in results.items():
        if success:
            _print(f"  [green]+[/green] {key_name}")
        else:
            _print(f"  [red]x[/red] {key_name}")

    click.echo()
    _print(f"Imported: {success_count} keys")
    if fail_count:
        _print(f"Failed: {fail_count} keys")

    if delete_after and success_count > 0:
        if click.confirm(f"\nDelete {env_file}? (keys are now in secure storage)"):
            os.remove(env_file)
            _print(f"[green]Deleted[/green] {env_file}")
    elif success_count > 0:
        _print(
            f"\n[yellow]Tip:[/yellow] You can now delete {env_file} — "
            "keys are stored securely in your OS keychain."
        )


@secrets_cli.command(name="check")
def check_backend():
    """Check keyring backend status and capabilities."""
    from spiritwriter.secrets import is_keyring_available

    import platform
    system = platform.system()

    _print(f"Platform: {system}")

    if not is_keyring_available():
        _print("[red]Keyring not available[/red]")
        _print("\nInstall with: pip install keyring")
        return

    try:
        import keyring
        backend = keyring.get_keyring()
        _print(f"Backend: [green]{backend.__class__.__name__}[/green]")

        if system == "Windows":
            _print("Storage: Windows Credential Manager")
            _print("Access: Control Panel > Credential Manager")
        elif system == "Darwin":
            _print("Storage: macOS Keychain")
            _print("Access: Keychain Access.app")
        else:
            _print("Storage: Secret Service API")
            _print("Access: Seahorse or KWallet")

        _print("\n[green]Keyring is working correctly![/green]")

    except Exception as e:
        _print(f"[red]Error:[/red] {e}")


# === Top-level CLI (for `spiritwriter secrets ...`) ===

@click.group()
@click.version_option(package_name="spiritwriter-core")
def main():
    """spiritwriter — shared foundation for AI content pipelines."""
    pass

main.add_command(secrets_cli, name="secrets")
