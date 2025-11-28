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

console = Console()
CONFIG_PATH = Path.home() / ".config" / "drydock" / "config.yml"

def load_config():
    """Loads configuration from the YAML file."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
            return config if isinstance(config, dict) else {}
    except (yaml.YAMLError, IOError) as e:
        console.print(f"[bold red]Error loading config file {CONFIG_PATH}:[/bold red] {e}")
        return {}


class ServiceUpdater:
    def __init__(self, root_path, max_concurrent, dry_run=False, confirm=False):
        self.root_path = Path(root_path)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.dry_run = dry_run
        self.confirm = confirm
        self.results = []
        self.failed = []

    async def get_services(self):
        """Finds all folders containing a docker-compose.yml"""
        services = []
        # Sort alphabetically so the UI doesn't jump around
        files = sorted(list(self.root_path.rglob("docker-compose.yml")))
        
        for file_path in files:
            services.append({
                "name": file_path.parent.name,
                "path": file_path.parent,
                "status": "Waiting",
                "color": "dim"
            })
        return services

    async def run_command(self, cmd, cwd):
        """Runs a shell command asynchronously"""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(), stderr.decode()

    async def update_service(self, service, progress, task_id, live):
        async with self.semaphore:
            if self.dry_run:
                service["status"] = "Dry Run"
                service["color"] = "cyan"
                live.console.print(f"[cyan]DRY-RUN:[/] Would update [bold]{service['name']}[/bold] at {service['path']}")
                await asyncio.sleep(0.2) # Simulate work
                progress.advance(task_id)
                return

            if self.confirm:
                # Pause the live display to ask for confirmation
                with live.console.capture():
                    live.stop()
                    should_update = Confirm.ask(f"Update service [bold cyan]{service['name']}[/bold cyan]?", default=True, console=console)
                    live.start()
                if not should_update:
                    service["status"] = "Skipped"
                    service["color"] = "yellow"
                    progress.advance(task_id)
                    return

            service["status"] = "Pulling..."
            service["color"] = "blue"
            
            # Step 1: Pull with retries for transient network errors
            max_retries = 3
            for attempt in range(max_retries):
                code, out, err = await self.run_command("docker compose pull -q", service["path"])
                if code == 0:
                    # Success, break the loop
                    break
                
                service["status"] = f"Pull Failed ({attempt + 1}/{max_retries})"
                service["color"] = "yellow"
                await asyncio.sleep(5 * (attempt + 1)) # Wait 5, 10 seconds before retrying
            else: # This 'else' belongs to the 'for' loop, it runs if the loop completes without a 'break'
                # All retries failed
                service["status"] = "Pull Failed"
                service["color"] = "red"
                self.failed.append((service["name"], err))
                progress.advance(task_id)
                return
            
            # Step 2: Down (to remove old containers)
            # This helps prevent container name conflicts.
            code, out, err = await self.run_command("docker compose down", service["path"])
            if code != 0:
                # This is not always a fatal error, so we'll just log it and continue.
                # For example, it fails if no containers were running, which is fine.
                pass

            # Step 3: Up -d
            service["status"] = "Deploying..."
            service["color"] = "yellow"
            code, out, err = await self.run_command("docker compose up -d", service["path"])

            if code == 0:
                service["status"] = "Updated"
                service["color"] = "green"
            else:
                service["status"] = "Up Failed"
                service["color"] = "red"
                self.failed.append((service["name"], err))
            
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
        confirm=args.confirm
    )
    all_services_found = await updater.get_services()
    
    if not all_services_found:
        console.print("[red]No docker-compose.yml files found![/red]")
        return

    # Filter out ignored services
    ignore_list = config.get("ignore", [])
    services = []
    ignored_services = []
    for s in all_services_found:
        if s["name"] in ignore_list:
            ignored_services.append(s)
        else:
            services.append(s)

    if ignored_services:
        console.print(f"[yellow]Ignoring {len(ignored_services)} services based on config: [dim]{', '.join(s['name'] for s in ignored_services)}[/dim][/yellow]")

    # Defer pihole to be last to avoid DNS issues for other containers
    pihole_service = None
    for i, s in enumerate(services):
        if s["name"] == "pihole":
            pihole_service = services.pop(i)
            break
    
    if pihole_service:
        services.append(pihole_service)
        console.print("[cyan]INFO:[/] Service [bold]pihole[/bold] will be updated last to preserve DNS resolution.")

    # Create the layout
    layout = Layout()
    layout.split(
        Layout(name="header", size=4),
        Layout(name="main"),
        Layout(name="footer", size=3)
    )

    total_services = len(services)

    # Header
    header_text = f"🚀 Docker Fleet Update: {total_services} Services found"
    if args.dry_run:
        header_text += " ([bold yellow]DRY RUN[/bold yellow])"
    
    layout["header"].update(Panel(Text(header_text, justify="center", style="bold cyan"), title="Drydock", border_style="blue"))

    # Progress Bar
    job_progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        expand=True
    )
    overall_task = job_progress.add_task("[cyan]Updating Fleet...", total=total_services)

    # Live Table function
    def generate_table():
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Service", style="cyan", width=30)
        table.add_column("Status", justify="center")
        
        # We can't show all 67 rows comfortably, so we show:
        # 1. Currently Active
        # 2. Recently Failed
        # 3. A summary of success
        
        active = [s for s in services if s["status"] in ["Pulling...", "Deploying..."]]
        
        # Add active rows
        for s in active:
            table.add_row(s["name"], f"[{s['color']}]{s['status']}[/{s['color']}]")
            
        # Fill rest with dots if empty so layout doesn't collapse
        if not active:
             table.add_row("[dim]Worker pool idle...[/dim]", "")

        return Panel(table, title="Active Workers")

    # Footer (Summary)
    def generate_footer():
        completed = sum(1 for s in services if s["status"] == "Updated")
        failed = len(updater.failed)
        waiting = sum(1 for s in services if s["status"] == "Waiting")
        return Panel(f"[green]Success: {completed}[/green]  |  [red]Failed: {failed}[/red]  |  [dim]Waiting: {waiting}[/dim]", title="Status")

    # Main Execution Loop
    with Live(layout, refresh_per_second=4, console=console) as live:
        async def update_view():
            while not job_progress.finished:
                layout["main"].update(generate_table())
                layout["footer"].update(generate_footer())
                # Render the progress bar into the layout? 
                # Ideally we mix them, but for simplicity, we'll print progress above.
                # Actually, let's put progress IN the footer or header.
                # Rich Live allows updating renderables.
                title = f"[bold yellow]⚓ drydock | processing {max_jobs} at a time[/bold yellow]"
                if args.dry_run:
                    title += " ([bold yellow]DRY RUN[/bold yellow])"
                layout["header"].update(Panel(job_progress, title=title, border_style="blue"))
                await asyncio.sleep(0.1)

        update_tasks = [updater.update_service(s, job_progress, overall_task, live) for s in services]
        
        # Run view updater and tasks concurrently
        await asyncio.gather(
            asyncio.gather(*update_tasks),
            update_view()
        )

    # Final Report
    console.print("\n[bold]✨ Update Complete ✨[/bold]")

    skipped_services = [s["name"] for s in services if s["status"] == "Skipped"]
    if skipped_services:
        console.print("[bold yellow]The following services were skipped by user confirmation:[/bold yellow]")
        console.print(f"[dim]{', '.join(skipped_services)}[/dim]")

    if updater.failed:
        console.print("[bold red]The following services failed:[/bold red]")
        for name, error in updater.failed:
            console.print(f"❌ [bold]{name}[/bold]")
            console.print(f"[dim]{error.strip()}[/dim]")
            console.print("---")
    else:
        updated_count = sum(1 for s in services if s["status"] == "Updated")
        if updated_count > 0:
            console.print("[bold green]All attempted updates were successful.[/bold green]")
        elif not skipped_services:
            console.print("[bold green]No services required an update.[/bold green]")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update multiple docker-compose services concurrently.")
    parser.add_argument(
        "-p", "--path", type=Path,
        help="Path to your root docker-compose folder."
    )
    parser.add_argument(
        "-j", "--jobs", type=int,
        help="Number of concurrent updates to run."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate updates without making any changes."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Ask for confirmation before updating each service."
    )
    parser.add_argument(
        "--show-config-path", action="store_true", help="Show the path to the config file and exit."
    )
    args = parser.parse_args()

    if args.show_config_path:
        console.print(f"Config file path is: [bold cyan]{CONFIG_PATH}[/bold cyan]")
        exit(0)

    asyncio.run(run_ui(args))