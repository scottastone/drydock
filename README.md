# ⚓ drydock

> A handsome, concurrent fleet updater for your docker-compose services.

![Python](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python)

`drydock` is a command-line tool that scans a directory for `docker-compose.yml` files and updates them concurrently, displaying the progress in a rich, interactive terminal dashboard. It's designed to make managing a large number of self-hosted services fast and easy.

---

## ✨ Features

- **Concurrent Updates:** Updates multiple services at once for maximum speed.
- **Rich Live Dashboard:** A beautiful terminal UI powered by `rich` shows live progress, status, and active workers.
- **Flexible Configuration:** Use command-line arguments or a central YAML config file (`~/.config/drydock/config.yml`).
- **Dry Run Mode:** See what would be updated without making any actual changes.
- **Interactive Confirmation:** Confirm each service update one by one.
- **Smart Service Handling:**
  - **Ignore List:** Prevent updates for specific local or custom services.
  - **Deferred Updates:** Automatically updates critical services like `pihole` last to maintain DNS for other updates.
- **Resilient Networking:** Automatically retries failed image pulls to handle transient network errors.
- **Single Executable:** Compiles down to a single, portable binary that you can place in your `PATH`.

---

## 📦 Installation

The easiest way to build and install `drydock` is to use the provided installation script.

1.  **Prerequisites:**
    You need Python 3.10+ and `uv` (or `pip`). You'll also need to install the project dependencies:
    ```bash
    uv pip install rich pyyaml pyinstaller
    ```

2.  **Run the Installer:**
    The `install.sh` script compiles the Python code into a single executable and moves it to `~/.local/bin`, which should be in your system's `PATH`.

    ```bash
    # Make the script executable
    chmod +x install.sh

    # Run the installer
    ./install.sh
    ```

After the script completes, you can run `drydock` from anywhere!

---

## ⚙️ Configuration

`drydock` can be configured via a YAML file located at `~/.config/drydock/config.yml`. You can find this path easily by running `drydock --show-config-path`.

Command-line arguments will always override settings from the config file.

#### Example `config.yml`:

```yaml
# ~/.config/drydock/config.yml

# Default path to your main docker compose directory.
# Tilde (~) is supported for your home directory.
path: "~/docker/compose"

# Default number of concurrent jobs to run.
max_concurrent_jobs: 10

# A list of service directory names to skip entirely.
ignore:
  - my-custom-app
  - another-local-service
  - dev-environment

# A list of services to update last.
# Useful for critical infrastructure like DNS servers.
# Note: The current script hardcodes 'pihole', but this is how you'd make it configurable.
defer_last:
  - pihole
```

---

## 🚀 Usage

Simply run the command to start the update process.

```bash
drydock
```

### Command-Line Arguments

| Argument               | Short | Description                                          |
| ---------------------- | ----- | ---------------------------------------------------- |
| `--path [PATH]`        | `-p`  | Path to your root docker-compose folder.             |
| `--jobs [N]`           | `-j`  | Number of concurrent updates to run.                 |
| `--confirm`            |       | Ask for confirmation before updating each service.   |
| `--dry-run`            |       | Simulate updates without making any changes.         |
| `--show-config-path`   |       | Show the path to the config file and exit.           |
| `--help`               | `-h`  | Show the help message and exit.                      |

#### Examples:

```bash
# Perform a dry run on a specific directory
drydock --path /mnt/docker/services --dry-run

# Update all services, but ask for confirmation on each one
drydock --confirm

# Update using 15 concurrent workers
drydock --jobs 15
```

---

## 🛠️ Building from Source

If you prefer to build the executable manually without using the `install.sh` script, you can run `pyinstaller` directly:

```bash
pyinstaller --onefile --name drydock drydock.py
```

The final executable will be located at `dist/drydock`.

---

## 📄 License

This project is open-source and available under the MIT License.