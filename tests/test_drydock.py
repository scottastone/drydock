import pytest
from unittest.mock import mock_open, patch, MagicMock, AsyncMock
from pathlib import Path

# Add project root to the Python path to allow importing 'drydock'
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drydock
import yaml

@pytest.fixture
def mock_services():
    """Provides a standard list of mock services for testing."""
    return [
        {"name": "service-a", "path": Path("/fake/service-a")},
        {"name": "pihole", "path": Path("/fake/pihole")},
        {"name": "service-b", "path": Path("/fake/service-b")},
        {"name": "my-custom-app", "path": Path("/fake/my-custom-app")},
    ]


def test_parse_pulled_images():
    """
    Tests the parsing of 'docker compose pull' output to extract updated services.
    """
    output = """
    [+] Running 3/3
    ✔ web Pulled
    ✔ db Pulled
    ✔ cache Image is up to date
    """
    expected = ["web", "db"]
    assert drydock.parse_pulled_images(output) == expected


def test_parse_pulled_images_no_updates():
    """
    Tests the output parser when no images were pulled.
    """
    output = """
    [+] Running 2/2
    ✔ nginx Image is up to date
    ✔ api Image is up to date
    """
    expected = []
    assert drydock.parse_pulled_images(output) == expected


@patch("drydock.CONFIG_PATH")
def test_load_config_exists(mock_config_path):
    """
    Tests loading an existing and valid config file.
    """
    mock_config_path.exists.return_value = True
    config_content = """
    path: "~/docker"
    ignore:
      - service-a
    """
    with patch("builtins.open", mock_open(read_data=config_content)):
        config = drydock.load_config()

    assert config["path"] == "~/docker"
    assert "service-a" in config["ignore"]


@patch("drydock.CONFIG_PATH")
def test_load_config_creates_new(mock_config_path):
    """
    Tests that a default config is created when one doesn't exist.
    """
    mock_config_path.exists.return_value = False
    # Mock the create_default_config function to avoid actual file IO
    with patch(
        "drydock.create_default_config", return_value={"path": "default/path"}
    ) as mock_create:
        config = drydock.load_config()

    mock_create.assert_called_once()
    assert config["path"] == "default/path"


@patch("drydock.CONFIG_PATH")
@patch("drydock.console.print")
def test_load_config_yaml_error(mock_print, mock_config_path):
    """
    Tests that a YAML error in the config file is handled gracefully.
    """
    mock_config_path.exists.return_value = True
    config_content = "path: '~/docker\ninvalid-yaml"  # Malformed YAML
    with patch("builtins.open", mock_open(read_data=config_content)):
        config = drydock.load_config()

    assert config == {}  # Should return an empty dict on error
    
    # Check that print was called, and inspect the call's arguments.
    # This is more robust than matching the exact error string from PyYAML.
    mock_print.assert_called_once()
    call_args, _ = mock_print.call_args
    assert "[bold red]Error loading config file" in call_args[0]
    assert "found unexpected end of stream" in call_args[0]

@patch("drydock.asyncio.run")
@patch("drydock.ServiceUpdater")
def test_service_filtering_and_deferral(mock_updater, mock_async_run, mock_services):
    """
    Tests that services are correctly filtered (ignored) and deferred.
    This test simulates the logic inside `run_ui`.
    """
    # --- Setup Mocks ---
    # Mock config values
    config = {"ignore": ["my-custom-app"], "defer_last": ["pihole"]}

    # Mock the ServiceUpdater to return our predefined list of services
    mock_updater_instance = mock_updater.return_value
    mock_updater_instance.get_services.return_value = mock_services

    # --- Logic from run_ui() ---
    all_services_found = mock_updater_instance.get_services()

    # Filter out ignored services
    ignore_list = config.get("ignore", [])
    services = []
    ignored_services = []
    for s in all_services_found:
        if s["name"] in ignore_list:
            ignored_services.append(s)
        else:
            services.append(s)

    # Defer services
    defer_list = config.get("defer_last", [])
    deferred_services = []
    remaining_services = []
    for s in services:
        if s["name"] in defer_list:
            deferred_services.append(s)
        else:
            remaining_services.append(s)

    final_service_order = remaining_services + deferred_services

    # --- Assertions ---
    # Check ignored services
    assert len(ignored_services) == 1
    assert ignored_services[0]["name"] == "my-custom-app"

    # Check final service order
    assert len(final_service_order) == 3
    assert final_service_order[0]["name"] == "service-a"
    assert final_service_order[1]["name"] == "service-b"
    assert final_service_order[2]["name"] == "pihole"  # Deferred to the end


@patch("drydock.os.environ", {"DRYDOCK_IGNORE": "service-b, my-custom-app"})
@patch("drydock.asyncio.run")
@patch("drydock.ServiceUpdater")
def test_service_filtering_with_env_var(mock_updater, mock_async_run, mock_services):
    """
    Tests that services are correctly ignored based on the DRYDOCK_IGNORE environment variable.
    """
    # Mock config with no ignores, so we only test the environment variable
    config = {"ignore": [], "defer_last": []}
    mock_updater_instance = mock_updater.return_value
    mock_updater_instance.get_services.return_value = mock_services

    # Simplified logic from run_ui()
    all_services_found = mock_updater_instance.get_services()
    ignore_list = set(config.get("ignore", []))
    ignore_from_env = drydock.os.environ.get("DRYDOCK_IGNORE", "")
    ignore_list.update([item.strip() for item in ignore_from_env.split(",")])

    services = [s for s in all_services_found if s["name"] not in ignore_list]
    service_names = {s["name"] for s in services}

    assert "service-b" not in service_names
    assert "my-custom-app" not in service_names # from mock_services, but not in ignore list

@pytest.fixture
def mock_updater():
    """Provides a ServiceUpdater instance with a mocked Docker client."""
    with patch("drydock.docker.from_env") as mock_from_env:
        mock_docker_client = MagicMock()
        mock_from_env.return_value = mock_docker_client
        updater = drydock.ServiceUpdater(
            root_path="/fake/path", max_concurrent=1, dry_run=False
        )
        updater.client = mock_docker_client
        return updater


def test_service_updater_init_docker_error():
    """
    Tests that the application exits if the Docker daemon is not available.
    """
    with patch("drydock.docker.from_env") as mock_from_env, \
         patch("drydock.sys.exit") as mock_exit:
        # Simulate a DockerException on client creation
        mock_from_env.side_effect = drydock.docker.errors.DockerException("Docker not found")

        drydock.ServiceUpdater(root_path="/fake", max_concurrent=1)

        # Assert that sys.exit(1) was called
        mock_exit.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_update_service_dry_run(mock_updater):
    """
    Tests that --dry-run correctly skips actual updates.
    """
    mock_updater.dry_run = True
    service = {"name": "test-service", "path": "/fake/path", "status": "Waiting"}
    mock_progress = MagicMock()
    mock_live = MagicMock()

    # Mock run_command to ensure it's not called
    mock_updater.run_command = AsyncMock()

    await mock_updater.update_service(service, mock_progress, MagicMock(), mock_live)

    assert service["status"] == "Dry Run"
    assert service["color"] == "cyan"
    mock_updater.run_command.assert_not_called()
    mock_progress.advance.assert_called_once()


@pytest.mark.asyncio
async def test_update_service_already_up_to_date(mock_updater):
    """
    Tests that a service is correctly identified as 'Up-to-date' and skipped.
    """
    service = {"name": "test-service", "path": "/fake/path", "status": "Waiting"}
    mock_progress = MagicMock()
    mock_live = MagicMock()

    # Mock the up-to-date check to return True
    mock_updater.is_image_up_to_date = AsyncMock(return_value=(True, "Is latest"))
    # Mock run_command to ensure it's not called for an update
    mock_updater.run_command = AsyncMock()

    await mock_updater.update_service(service, mock_progress, MagicMock(), mock_live)

    assert service["status"] == "Up-to-date"
    assert service["color"] == "dim"
    mock_updater.is_image_up_to_date.assert_called_once_with(service)
    mock_updater.run_command.assert_not_called()
    mock_progress.advance.assert_called_once()

@pytest.mark.asyncio
async def test_update_service_confirm_no(mock_updater):
    """
    Tests that a service update is skipped if the user declines confirmation.
    """
    mock_updater.confirm = True
    service = {"name": "test-service", "path": "/fake/path", "status": "Waiting"}
    mock_progress = MagicMock()
    mock_live = MagicMock()

    # Mock run_command to ensure it's not called
    mock_updater.run_command = AsyncMock()

    # Mock the user input to be 'n' (no)
    with patch("drydock.Confirm.ask", return_value=False) as mock_ask:
        await mock_updater.update_service(service, mock_progress, MagicMock(), mock_live)

    assert service["status"] == "Skipped"
    assert service["color"] == "yellow"
    mock_ask.assert_called_once()
    mock_updater.run_command.assert_not_called()
    mock_progress.advance.assert_called_once()




@pytest.mark.asyncio
@patch("builtins.open", new_callable=mock_open, read_data="services:\n  app:\n    image: my-image:latest")
async def test_update_service_successful_update(mock_open_file, mock_updater):
    """
    Tests the full, successful update flow for a single service.
    """
    service = {"name": "test-service", "path": Path("/fake/path"), "status": "Waiting"}
    mock_progress = MagicMock()
    mock_live = MagicMock()

    # Mock the up-to-date check to return False, triggering an update
    mock_updater.is_image_up_to_date = AsyncMock(return_value=(False, "Not latest"))

    # Mock the command runner to simulate successful docker-compose commands
    mock_updater.run_command = AsyncMock(
        side_effect=[
            (0, "Pull successful", ""),  # docker compose pull
            (0, "Down successful", ""),  # docker compose down
            (0, "Up successful", ""),    # docker compose up -d
        ]
    )

    # Mock Docker client for digest checking
    mock_image = MagicMock()
    mock_image.short_id = "sha256:12345"
    mock_updater.client.images.get.return_value = mock_image

    await mock_updater.update_service(service, mock_progress, MagicMock(), mock_live)

    assert service["status"] == "Updated"
    assert service["color"] == "green"
    assert mock_updater.run_command.call_count == 3
    mock_updater.run_command.assert_any_call("docker compose pull", service["path"])
    mock_updater.run_command.assert_any_call("docker compose down", service["path"])
    mock_updater.run_command.assert_any_call("docker compose up -d", service["path"])
    mock_progress.advance.assert_called_once()
    assert not mock_updater.failed_services


@pytest.mark.asyncio
@patch("builtins.open")
@patch("pathlib.Path.exists", return_value=True)
async def test_is_image_up_to_date_with_build(mock_path_exists, mock_open_file, mock_updater):
    """
    Tests that a service with a 'build' directive is correctly identified as needing an update,
    as its state cannot be verified against a remote registry.
    """
    compose_content = """
services:
  webapp:
    build: .
"""
    mock_open_file.return_value = mock_open(read_data=compose_content).return_value
    service = {"name": "test-service", "path": Path("/fake/path")}

    is_latest, reason = await mock_updater.is_image_up_to_date(service)

    assert not is_latest
    assert "uses build, not image" in reason
    mock_path_exists.assert_called_once()


@pytest.mark.asyncio
@patch("builtins.open")
@patch("pathlib.Path.exists", return_value=True)
async def test_is_image_up_to_date_invalid_yaml(mock_path_exists, mock_open_file, mock_updater):
    """
    Tests that an invalid docker-compose.yml file is handled gracefully.
    """
    compose_content = "services: webapp: image: my-image:latest"  # Invalid YAML
    mock_open_file.return_value = mock_open(read_data=compose_content).return_value
    service = {"name": "test-service", "path": Path("/fake/path")}

    is_latest, reason = await mock_updater.is_image_up_to_date(service)

    assert not is_latest
    assert "Error during check" in reason
    mock_path_exists.assert_called_once()


@pytest.mark.asyncio
@patch("builtins.open")
@patch("drydock.os.path.expandvars", return_value="my-image:1.2.3")
@patch("pathlib.Path.exists", return_value=True)
async def test_is_image_up_to_date_with_env_var(
    mock_path_exists, mock_expandvars, mock_open_file, mock_updater
):
    """
    Tests that environment variables in image names are correctly expanded.
    """
    compose_content = """
services:
  webapp:
    image: my-image:${TAG}
"""
    mock_open_file.return_value = mock_open(read_data=compose_content).return_value
    service = {"name": "test-service", "path": Path("/fake/path")}

    # Mock the Docker client to simulate that the pinned image exists locally
    mock_updater.client.images.get.return_value = MagicMock()

    is_latest, reason = await mock_updater.is_image_up_to_date(service)

    # The check should succeed because the pinned version exists
    assert is_latest
    assert reason == "All services in stack are up-to-date"
    mock_updater.client.images.get.assert_called_once_with("my-image:1.2.3")
    mock_path_exists.assert_called_once()


@pytest.mark.asyncio
@patch("builtins.open")
@patch("pathlib.Path.exists", return_value=True)
async def test_is_image_up_to_date_pinned_version_missing(
    mock_path_exists, mock_open_file, mock_updater
):
    """
    Tests that if a pinned image version is not found locally, it triggers an update.
    """
    compose_content = """
services:
  webapp:
    image: my-image:1.5.0
"""
    mock_open_file.return_value = mock_open(read_data=compose_content).return_value
    service = {"name": "test-service", "path": Path("/fake/path")}

    # Mock the Docker client to raise ImageNotFound
    mock_updater.client.images.get.side_effect = drydock.docker.errors.ImageNotFound("Image not found")

    is_latest, reason = await mock_updater.is_image_up_to_date(service)

    assert not is_latest
    assert "not found locally" in reason
    mock_updater.client.images.get.assert_called_once_with("my-image:1.5.0")
    mock_path_exists.assert_called_once()


@pytest.mark.asyncio
async def test_update_service_failed_up(mock_updater):
    """Tests the behavior when 'docker compose up -d' fails."""
    service = {"name": "test-service", "path": Path("/fake/path"), "status": "Waiting"}
    mock_updater.is_image_up_to_date = AsyncMock(return_value=(False, "Not latest"))
    mock_updater.run_command = AsyncMock(side_effect=[
        (0, "Pull success", ""),  # pull
        (0, "Down success", ""),  # down
        (1, "", "Error: container failed to start"),  # up
    ])

    await mock_updater.update_service(service, MagicMock(), MagicMock(), MagicMock())

    assert service["status"] == "Up Failed"
    assert service["color"] == "red"
    assert len(mock_updater.failed_services) == 1
    assert mock_updater.failed_services[0]["service"]["name"] == "test-service"
    assert "container failed to start" in mock_updater.failed_services[0]["error"]


@pytest.mark.asyncio
@patch("pathlib.Path.rglob")
async def test_get_services(mock_rglob, mock_updater):
    """
    Tests that get_services correctly finds and sorts docker-compose.yml files.
    """
    # Mock the return value of rglob to simulate found files
    mock_paths = [
        Path("/fake/path/service-b/docker-compose.yml"),
        Path("/fake/path/service-c/docker-compose.yml"),
        Path("/fake/path/service-a/docker-compose.yml"),
    ]
    mock_rglob.return_value = mock_paths

    services = await mock_updater.get_services()

    assert len(services) == 3
    # Check that the services are sorted alphabetically by name
    assert services[0]["name"] == "service-a"
    assert services[1]["name"] == "service-b"
    assert services[2]["name"] == "service-c"
    assert services[0]["path"] == Path("/fake/path/service-a")
    mock_rglob.assert_called_once_with("docker-compose.yml")
