# ⚓ drydock

> A handsome, concurrent fleet updater for your Docker Compose services.

![Python](https://img.shields.io/badge/python-3.13+-blue.svg?style=for-the-badge&logo=python)

`drydock` is a command-line tool that scans a directory for `docker-compose.yml` files and updates them concurrently, displaying the progress in a rich, interactive terminal dashboard. It's designed to make managing a large number of self-hosted services fast, easy, and informative.

---

## ✨ Features

- **Concurrent Updates:** Updates multiple services at once for maximum speed.
- **Rich Live Dashboard:** A beautiful terminal UI powered by `rich` shows live progress, status, and active workers.
- **Flexible Configuration:** Use command-line arguments or a central YAML config file (`~/.config/drydock/config.yml`).
- **Smart Image Checking:** Intelligently compares local and remote image digests to update only what's necessary, correctly handling `latest` and pinned version tags.
- **Detailed Update Summary:** Reports exactly what changed by showing the old and new image digests for each updated service.
- **Authenticated Pulls:** Automatically uses your Docker credentials to avoid anonymous pull rate limits from registries like Docker Hub.
- **Dry Run Mode:** See what would be updated without making any actual changes.
- **Interactive Confirmation:** Confirm each service update one by one.
- **Smart Service Handling:**
  - **Ignore List:** Prevent updates for specific local or custom services.
  - **Deferred Updates:** Automatically updates critical services like `pihole` last to maintain DNS for other updates.
  - **Multi-Service File Support:** Correctly checks all services within a single `docker-compose.yml` file.
- **Resilient Networking:** Automatically retries failed image pulls to handle transient network errors.

---

## 📦 Installation

There are two primary ways to run `drydock`: directly on your host machine or within a Docker container.

### Method 1: Running with Docker (Recommended)

This is the easiest and most portable way to run `drydock`. It ensures the environment is consistent and handles dependencies automatically.

1.  **Log in to Docker:** To avoid rate limits from Docker Hub, first log in on your host machine. `drydock` will use these credentials.
    ```sh
    docker login
    ```

2.  **Use the provided `docker-compose.yml`:** The repository includes a `docker-compose.yml` file specifically for running `drydock`. It's pre-configured to mount the necessary Docker socket and your compose directory.

3.  **Run it!**
    ```sh
    docker compose run --rm drydock
    ```
    You can also pass any of the standard command-line arguments:
    ```sh
    docker compose run --rm drydock --confirm --jobs 5
    ```

### Method 2: Local Installation

If you prefer to run it directly on your host, you can install it from source.

1.  **Prerequisites:** You need Python 3.13+.
2.  **Install Dependencies:**
    ```sh
    pip install -e .
    ```
3.  **Run the script:**
    ```sh
    python -m drydock
    ```

---

## ⚙️ Configuration

`drydock` can be configured via a YAML file located at `~/.config/drydock/config.yml`. You can find this path easily by running `drydock --show-config-path`.
Command-line arguments will always override settings from the config file.

#### Example `config.yml`:

```yaml
# ~/.config/drydock/config.yml

# Default path to your main Docker Compose directory.
# Tilde (~) is supported for your home directory.
# When running in Docker, this should be the path inside the container.
path: "~/docker/compose"

# Default number of concurrent jobs to run.
max_concurrent_jobs: 10

# A list of service directory names to skip entirely.
# This list can be extended by setting the DRYDOCK_IGNORE environment variable
# in your docker-compose.yml (as a comma-separated string).
ignore:
  - drydock # Ignore the updater itself!
  - my-custom-app # A service you build locally

# A list of services to update last.
# Useful for critical infrastructure like DNS servers.
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