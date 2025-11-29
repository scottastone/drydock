import asyncio
import argparse
import yaml
from pathlib import Path
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.prompt import Confirm
import re
import sys
import os
import docker

console = Console()
CONFIG_PATH = Path.home() / ".config" / "drydock" / "config.yml"


def create_default_config():
    """Creates a default configuration file if one does not exist."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cpu_core_count = os.cpu_count() or 1

    # The default config will use half of your available cores.
    default_config_content = f"""
# Default path to your main docker compose directory.
# Tilde (~) is supported for your home directory.
path: "~/docker/compose"

# Default number of concurrent jobs to run.
max_concurrent_jobs: {cpu_core_count // 2}

# A list of service directory names to skip entirely.
ignore:
  - my-custom-app
  - another-local-service

# A list of services to update last.
# Useful for critical infrastructure like DNS servers (e.g., pihole).
defer_last:
  - pihole
"""
    with open(CONFIG_PATH, "w") as f:
        f.write(default_config_content.strip())

    console.print(f"📄 Created default config file at [bold cyan]{CONFIG_PATH}[/bold cyan]")
    console.print(f"Using {cpu_core_count // 2} cores by default (found a total of {cpu_core_count} cores). You can edit this in the config file.")
    # Load and return the newly created config
    return yaml.safe_load(default_config_content)


def load_config():
    """Loads configuration from the YAML file."""
    if not CONFIG_PATH.exists():
        return create_default_config()

    try:
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
            return config if isinstance(config, dict) else {}
    except (yaml.YAMLError, IOError) as e:
        console.print(
            f"[bold red]Error loading config file {CONFIG_PATH}:[/bold red] {e}"
        )
        return {}


class ServiceUpdater:
    def __init__(self, root_path, max_concurrent, dry_run=False, confirm=False, force_pull=False):
        self.root_path = Path(root_path)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.dry_run = dry_run
        self.confirm = confirm
        self.results = []
        self.failed_services = []

        self.force_pull = force_pull

        try:
            # Initialize the Docker client from the environment
            self.client = docker.from_env()
            self.client.ping()  # Check if the Docker daemon is responsive
        except docker.errors.DockerException as e:
            console.print(f"[bold red]Error connecting to Docker daemon:[/bold red] {e}")
            console.print("Please ensure Docker is running.")
            sys.exit(1)
    async def get_services(self):
        """Finds all folders containing a docker-compose.yml"""
        services = []
        # Sort alphabetically so the UI doesn't jump around
        files = sorted(list(self.root_path.rglob("docker-compose.yml")))

        for file_path in files:
            services.append(
                {
                    "name": file_path.parent.name,
                    "path": file_path.parent,
                    "status": "Waiting",
                    "color": "dim",
                    "update_info": "",
                    "old_digest": None,
                    "new_digest": None,
                }
            )
        return services

    async def run_command(self, cmd, cwd):
        """Runs a shell command asynchronously"""
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(), stderr.decode()

    async def is_image_up_to_date(self, service):
        """Checks if the docker image for a service is up-to-date using the Docker Python library."""
        compose_file_path = service["path"] / "docker-compose.yml"
        if not compose_file_path.exists():
            return False, "Compose file not found"

        try:
            with open(compose_file_path, "r") as f:
                compose_config = yaml.safe_load(f)

            if not compose_config or "services" not in compose_config:
                return False, "Invalid compose file"

            all_service_configs = compose_config.get("services", {})
            if not all_service_configs:
                return False, "No services found in compose file"

            # Iterate through all services defined in the file.
            # If any service is not up-to-date, we must pull for the whole stack.
            for service_name, service_config in all_service_configs.items():
                # If a service uses 'build' or has no 'image', we can't check it.
                # We must assume it might need an update.
                if "image" not in service_config:
                    return False, f"Service '{service_name}' uses build, not image"

                # Expand environment variables in the image name (e.g., ${TAG})
                image_name = os.path.expandvars(service_config["image"])

                # If no tag is specified, Docker defaults to 'latest'. We should do the same.
                if ":" not in Path(image_name).name:
                    image_name = f"{image_name}:latest"

                # --- Pinned Version Check ---
                tag = image_name.split(":")[-1]
                if tag != "latest":
                    try:
                        self.client.images.get(image_name)
                        # This pinned version exists locally, so we can continue to the next service.
                        continue
                    except docker.errors.ImageNotFound:
                        # A pinned version is missing, so we must pull.
                        return False, f"Pinned version for '{service_name}' not found locally"

                # --- Remote Digest Check for 'latest' tag ---
                loop = asyncio.get_running_loop()
                is_latest, reason = await loop.run_in_executor(
                    None, self._check_image_digests, image_name
                )

                if not is_latest:
                    # This image is not up-to-date, so the whole stack needs a pull.
                    return False, f"Image for '{service_name}' is not up-to-date"

            # If we get here, it means the loop completed without finding any
            # services that needed an update. The entire stack is up-to-date.
            return True, "All services in stack are up-to-date"

        except (yaml.YAMLError, KeyError, IndexError, docker.errors.APIError) as e:
            return False, f"Error during check: {e}"

    def _check_image_digests(self, image_name):
        """Synchronous helper to check image digests. Meant to be run in an executor."""
        try:
            # 1. Get the remote digest first. This is the source of truth.
            remote_data = self.client.images.get_registry_data(image_name)
            remote_digest = remote_data.attrs["Descriptor"]["digest"]

            # 2. Get the local image and check if its RepoDigests contains the remote digest.
            local_image = self.client.images.get(image_name)
            repo_digests = local_image.attrs.get("RepoDigests", [])
            if not repo_digests:
                # Local image exists but has no RepoDigests (e.g., it was only built, not pulled).
                # We can't be sure it's the same, so we should update.
                return False, "Local image has no RepoDigest"

            return any(remote_digest in d for d in repo_digests), "Checked"
        except docker.errors.APIError as e:
            return False, f"API Error checking registry: {e}"
        except docker.errors.ImageNotFound:
            return False, "Image not found locally"

    async def update_service(self, service, progress, task_id, live):
        async with self.semaphore:
            if self.dry_run:
                service["status"] = "Dry Run"
                service["color"] = "cyan"
                live.stop()
                console.print(
                    f"[cyan]DRY-RUN:[/] Would update [bold]{service['name']}[/bold] at {service['path']}"
                )
                live.start()

                await asyncio.sleep(0.2)  # Simulate work
                progress.advance(task_id)
                return

            if self.confirm:
                # Pause the live display to ask for confirmation
                live.stop()
                should_update = Confirm.ask(
                    f"Update service [bold cyan]{service['name']}[/bold cyan]?",
                    default=True,
                    console=console,
                )
                live.start()
                if not should_update:
                    service["status"] = "Skipped"
                    service["color"] = "yellow"
                    progress.advance(task_id)
                    return

            # Check if image is already up-to-date
            if not self.force_pull:
                service["status"] = "Checking..."
                service["color"] = "blue"
                is_latest, reason = await self.is_image_up_to_date(service)
                if is_latest:
                    service["status"] = "Up-to-date"
                    service["color"] = "dim"
                    progress.advance(task_id)
                    return  # This was the missing return statement

            # --- Capture pre-update state for final report ---
            try:
                # We need to find the specific image name from the compose file again
                with open(service["path"] / "docker-compose.yml", "r") as f:
                    compose_config = yaml.safe_load(f)
                if compose_config and "services" in compose_config:
                    # This logic assumes single-service-per-file or that the first service is representative
                    first_service_config = list(compose_config["services"].values())[0]
                    if "image" in first_service_config:
                        image_name = os.path.expandvars(first_service_config["image"])
                        if ":" not in Path(image_name).name:
                            image_name += ":latest"
                        local_image = self.client.images.get(image_name)
                        service["old_digest"] = local_image.short_id
            except (docker.errors.ImageNotFound, FileNotFoundError, KeyError, IndexError):
                service["old_digest"] = "Not present"

            service["status"] = "Pulling..."
            service["color"] = "blue"

            # Step 1: Pull with retries for transient network errors
            max_retries = 3
            for attempt in range(max_retries):
                code, out, err = await self.run_command(
                    "docker compose pull", service["path"]
                )
                if code == 0:
                    # Success, break the loop
                    break

                service["status"] = f"Pull Failed ({attempt + 1}/{max_retries})"
                service["color"] = "yellow"
                await asyncio.sleep(
                    5 * (attempt + 1)
                )  # Wait 5, 10 seconds before retrying
            else:  # This 'else' belongs to the 'for' loop, it runs if the loop completes without a 'break'
                # All retries failed
                service["status"] = "Pull Failed"
                service["color"] = "red"
                self.failed_services.append({"service": service, "error": err})
                progress.advance(task_id)
                return

            # Store pull output for final report
            service["update_info"] = out

            # Step 2: Down (to remove old containers)
            # This helps prevent container name conflicts.
            code, out, err = await self.run_command(
                "docker compose down", service["path"]
            )
            if code != 0:
                # This is not always a fatal error, so we'll just log it and continue.
                # For example, it fails if no containers were running, which is fine.
                pass

            # Step 3: Up -d
            service["status"] = "Deploying..."
            service["color"] = "yellow"
            code, out, err = await self.run_command(
                "docker compose up -d", service["path"]
            )

            if code == 0:
                service["status"] = "Updated"
                service["color"] = "green"
            else:
                # --- Capture post-update state for final report ---
                try:
                    # Re-fetch image name as before
                    if "image" in first_service_config:
                        image_name = os.path.expandvars(first_service_config["image"])
                        if ":" not in Path(image_name).name:
                            image_name += ":latest"
                        latest_image = self.client.images.get(image_name)
                        service["new_digest"] = latest_image.short_id
                except (docker.errors.ImageNotFound, NameError):
                    pass # Couldn't get new digest, but that's ok
                service["status"] = "Up Failed"
                service["color"] = "red"
                self.failed_services.append({"service": service, "error": err})

            progress.advance(task_id)


async def run_ui(args):
    """Main function to set up and run the UI and update process."""
    config = load_config()

    # Determine configuration with precedence: CLI > config file > default
    compose_root = args.path or config.get("path") or "/home/scott/docker/compose/"
    # Ensure compose_root is a Path object
    compose_root = Path(compose_root).expanduser()

    max_jobs = args.jobs or config.get("max_concurrent_jobs") or 10

    updater = ServiceUpdater(
        root_path=compose_root,
        max_concurrent=int(max_jobs),
        dry_run=args.dry_run,
        confirm=args.confirm,
        force_pull=args.force_pull,
    )
    all_services_found = await updater.get_services()

    if not all_services_found:
        console.print("[red]No docker-compose.yml files found![/red]")
        return

    # Filter out ignored services from config file
    ignore_list = set(config.get("ignore", []))

    # Augment with ignored services from environment variable
    ignore_from_env = os.environ.get("DRYDOCK_IGNORE", "")
    if ignore_from_env:
        ignore_list_from_env = [item.strip() for item in ignore_from_env.split(",")]
        ignore_list.update(ignore_list_from_env)

    # Get services to defer from config
    defer_list = set(config.get("defer_last", []))

    services = []
    deferred_services = []
    ignored_services = []
    for s in all_services_found:
        if s["name"] in ignore_list:
            ignored_services.append(s)
        elif s["name"] in defer_list:
            deferred_services.append(s)
        else:
            services.append(s)

    # Add deferred services to the end of the list
    services.extend(deferred_services)

    if ignored_services:
        console.print(
            f"[yellow]Ignoring {len(ignored_services)} services based on config: [dim]{', '.join(s['name'] for s in ignored_services)}[/dim][/yellow]"
        )
    if deferred_services:
        console.print(
            f"[cyan]INFO:[/] The following services will be updated last: [bold]{', '.join(s['name'] for s in deferred_services)}[/bold]"
        )

    # Create the layout
    layout = Layout()
    layout.split(
        Layout(name="header", size=4),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )

    total_services = len(services)

    # Header
    header_text = f"🚀 Docker Fleet Update: {total_services} Services found"
    if args.dry_run:
        header_text += " ([bold yellow]DRY RUN[/bold yellow])"

    layout["header"].update(
        Panel(
            Text(header_text, justify="center", style="bold cyan"),
            title="Drydock",
            border_style="blue",
        )
    )

    # Progress Bar
    job_progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        expand=True,
    )
    overall_task = job_progress.add_task(
        "[cyan]Updating Fleet...", total=total_services
    )

    # Live Table function
    def generate_table():
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Service", style="cyan", no_wrap=True, min_width=25)
        table.add_column("Status", justify="center", width=20)

        # Show services that are actively being worked on.
        active_statuses = ["Pulling...", "Deploying...", "Checking..."]
        active = [s for s in services if s["status"] in active_statuses]

        # Add active rows
        for s in sorted(active, key=lambda x: x["name"]):
            table.add_row(s["name"], f"[{s['color']}]{s['status']}[/{s['color']}]")

        # Fill rest with dots if empty so layout doesn't collapse
        if not active:
            table.add_row("[dim]Worker pool idle...[/dim]", "")

        return Panel(table, title="Active Workers")

    # Footer (Summary)
    def generate_footer():
        completed = sum(1 for s in services if s["status"] == "Updated")
        failed = len(updater.failed_services)
        waiting = sum(1 for s in services if s["status"] == "Waiting")
        skipped = sum(1 for s in services if s["status"] in ["Skipped", "Up-to-date"])
        return Panel(
            f"[green]Success: {completed}[/green]  |  [red]Failed: {failed}[/red]  |  [yellow]Skipped: {skipped}[/yellow]  |  [dim]Waiting: {waiting}[/dim]",
            title="Status",
        )

    # Main Execution Loop
    with Live(layout, refresh_per_second=4, console=console) as live:

        async def update_view():
            while not job_progress.finished:
                layout["main"].update(generate_table())
                layout["footer"].update(generate_footer())
                title = f"[bold yellow]⚓ drydock | processing {max_jobs} at a time[/bold yellow]"
                if args.dry_run:
                    title += " ([bold yellow]DRY RUN[/bold yellow])"
                layout["header"].update(
                    Panel(job_progress, title=title, border_style="blue")
                )
                await asyncio.sleep(0.1)

        update_tasks = [
            updater.update_service(s, job_progress, overall_task, live)
            for s in services
        ]

        # Run view updater and service update tasks concurrently
        await asyncio.gather(*update_tasks, update_view())

        # Perform one final refresh to ensure the footer is up-to-date
        layout["main"].update(generate_table())
        layout["footer"].update(generate_footer())
        live.refresh()

    # Final Report, printed after the Live display has stopped.
    console.print("\n[bold]✨ Update Complete ✨[/bold]")

    updated_services = [s for s in services if s["status"] == "Updated"]
    if updated_services:
        console.print(
            "\n[bold green]The following services were successfully updated:[/bold green]"
        )
        for service in updated_services:
            console.print(f"✅ [bold]{service['name']}[/bold]")
            # Show version change if available
            old_digest = service.get("old_digest")
            new_digest = service.get("new_digest")
            if old_digest and new_digest and old_digest != new_digest:
                console.print(
                    f"   [dim]Updated from {old_digest} →[/dim] [bold cyan]{new_digest}[/bold cyan]"
                )

    up_to_date_services = [s["name"] for s in services if s["status"] == "Up-to-date"]
    if up_to_date_services:
        console.print(
            f"\n[dim]Skipped {len(up_to_date_services)} services that were already up-to-date: {', '.join(up_to_date_services)}[/dim]"
        )

    skipped_services = [s["name"] for s in services if s["status"] == "Skipped"]
    if skipped_services:
        console.print(
            "\n[bold yellow]The following services were skipped by user confirmation:[/bold yellow]"
        )
        console.print(f"[dim]{', '.join(skipped_services)}[/dim]")

    if updater.failed_services:
        console.print("\n[bold red]The following services failed:[/bold red]")
        for failure in updater.failed_services:
            console.print(
                f"❌ [bold]{failure['service']['name']}[/bold] (Status: {failure['service']['status']})"
            )
            console.print(f"[dim]{failure['error'].strip()}[/dim]")
            console.print("---")
    else:
        # Only show this if there were no failures and something was actually updated.
        if updated_services and not skipped_services:
            console.print(
                "[bold green]All attempted updates were successful.[/bold green]"
            )
        elif not updated_services and not skipped_services:
            console.print("\n[bold]No services required an update.[/bold]")


def parse_pulled_images(docker_output: str) -> list[str]:
    """
    Parses the output of 'docker compose pull' to find which images were pulled.
    Looks for lines like '✔ service Pulled'.
    """
    # Regex to find lines like '✔ service Pulled' and capture 'service'
    pattern = re.compile(r"✔\s+([\w-]+)\s+Pulled")
    return pattern.findall(docker_output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Update multiple docker-compose services concurrently."
    )
    parser.add_argument(
        "-p", "--path", type=Path, help="Path to your root docker-compose folder."
    )
    parser.add_argument(
        "-j", "--jobs", type=int, help="Number of concurrent updates to run."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate updates without making any changes.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ask for confirmation before updating each service.",
    )
    parser.add_argument(
        "--force-pull",
        action="store_true",
        help="Force a pull of all images, skipping the up-to-date check.",
    )
    parser.add_argument(
        "--show-config-path",
        action="store_true",
        help="Show the path to the config file and exit.",
    )
    args = parser.parse_args()

    if args.show_config_path:
        console.print(f"Config file path is: [bold cyan]{CONFIG_PATH}[/bold cyan]")
        sys.exit(0)

    asyncio.run(run_ui(args))
