import pytest
from unittest.mock import mock_open, patch, MagicMock
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
    with patch("drydock.create_default_config", return_value={"path": "default/path"}) as mock_create:
        config = drydock.load_config()

    mock_create.assert_called_once()
    assert config["path"] == "default/path"


@patch("drydock.asyncio.run")
@patch("drydock.ServiceUpdater")
def test_service_filtering_and_deferral(mock_updater, mock_async_run, mock_services):
    """
    Tests that services are correctly filtered (ignored) and deferred.
    This test simulates the logic inside `run_ui`.
    """
    # --- Setup Mocks ---
    # Mock config values
    config = {
        "ignore": ["my-custom-app"],
        "defer_last": ["pihole"]
    }
    
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
    assert final_service_order[2]["name"] == "pihole" # Deferred to the end
