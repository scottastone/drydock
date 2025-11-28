#!/bin/bash

# This script automates the process of building the 'drydock' executable
# using PyInstaller and installing it into the user's local bin directory.

# Exit immediately if a command exits with a non-zero status.
set -e

echo "🚀 Starting the build process for drydock..."

# 1. Build the executable using PyInstaller
#    --onefile: Bundles everything into a single executable.
#    --name: Sets the output filename.
echo "   -> Running PyInstaller..."
pyinstaller --onefile --name drydock drydock.py

echo "✅ Build complete."

# 2. Define paths and install the executable
INSTALL_DIR="$HOME/.local/bin"
EXECUTABLE_NAME="drydock"
SOURCE_FILE="dist/$EXECUTABLE_NAME"

echo "🚀 Installing '$EXECUTABLE_NAME' to '$INSTALL_DIR'..."

# Create the installation directory if it doesn't exist
mkdir -p "$INSTALL_DIR"

# Move the executable to the installation directory, overwriting if it exists
mv "$SOURCE_FILE" "$INSTALL_DIR/"

echo "✅ Installation complete."

# 3. Clean up build artifacts
echo "🚀 Cleaning up build files..."
rm -rf build/ dist/ drydock.spec

echo "✨ All done! You can now run 'drydock' from anywhere in your terminal."
