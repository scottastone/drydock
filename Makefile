# Makefile for building and installing the 'drydock' utility.

# --- Configuration ---
# Use the shell's default python, but prefer one from an active venv.
PYTHON := $(shell command -v python)

# Main source file for the application.
SRC_FILE := drydock.py

# Name of the final executable.
EXECUTABLE_NAME := drydock

# Log level warning for PyInstaller
PYINSTALLER_LOG_LEVEL := WARN

# Directory for PyInstaller's output.
DIST_DIR := dist

# The final path of the built executable.
SOURCE_EXECUTABLE := $(DIST_DIR)/$(EXECUTABLE_NAME)

# Where to install the executable.
INSTALL_DIR := $(HOME)/.local/bin

# --- Targets ---

# Phony targets don't represent actual files.
.PHONY: all build install clean

# Default target: running 'make' will build and install.
all: build

# Build the executable using PyInstaller.
build: $(SOURCE_EXECUTABLE)

$(SOURCE_EXECUTABLE): $(SRC_FILE)
	@echo "--> Building executable..."
	$(PYTHON) -m PyInstaller --log-level=$(PYINSTALLER_LOG_LEVEL) --onefile --name $(EXECUTABLE_NAME) $(SRC_FILE)
	@echo "✅ Build complete."

# Install the executable to the user's local bin directory.
install: build
	@echo "--> Installing '$(EXECUTABLE_NAME)' to '$(INSTALL_DIR)'..."
	@mkdir -p $(INSTALL_DIR)
	install -m 755 $(SOURCE_EXECUTABLE) $(INSTALL_DIR)/
	@echo "✅ Installation complete. You can now run '$(EXECUTABLE_NAME)'."

# Clean up build artifacts.
clean:
	@echo "--> Cleaning up build files..."
	@rm -rf build/ $(DIST_DIR)/ *.spec
	@echo "✅ Cleanup complete."