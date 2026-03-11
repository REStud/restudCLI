#!/bin/bash

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}REStud CLI Installation Script${NC}"
echo "=================================="

# Check if Python is installed
echo -e "\n${YELLOW}Checking Python installation...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is not installed.${NC}"
    echo "Please install Python 3.8 or higher and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Found Python ${PYTHON_VERSION}${NC}"

# Check if pip is installed
echo -e "\n${YELLOW}Checking pip installation...${NC}"
if ! python3 -m pip --version &> /dev/null; then
    echo -e "${RED}pip is not installed.${NC}"
    echo "Installing pip..."
    python3 -m ensurepip --upgrade
fi
echo -e "${GREEN}✓ pip is available${NC}"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo -e "\n${YELLOW}Installing REStud CLI from ${SCRIPT_DIR}...${NC}"

# Install the package in development mode
cd "$SCRIPT_DIR"
python3 -m pip install -e .

# Create .config/restud directory if it doesn't exist
echo -e "\n${YELLOW}Setting up configuration directory...${NC}"
mkdir -p ~/.config/restud
echo -e "${GREEN}✓ Configuration directory created at ~/.config/restud${NC}"

# Create config file if it doesn't exist
if [ ! -f ~/.config/restud/config.yaml ]; then
    cat > ~/.config/restud/config.yaml << 'EOF'
# REStud CLI Configuration
# Add your configuration settings here
EOF
    echo -e "${GREEN}✓ Created default config file at ~/.config/restud/config.yaml${NC}"
fi

# Add RESTUD environment variable to .bashrc if not already present
echo -e "\n${YELLOW}Configuring environment variables...${NC}"
RESTUD_EXPORT="export RESTUD=~/.config/restud"
BASHRC_FILE="$HOME/.bashrc"

if [ -f "$BASHRC_FILE" ]; then
    if ! grep -q "export RESTUD=" "$BASHRC_FILE"; then
        echo "" >> "$BASHRC_FILE"
        echo "# REStud CLI Configuration" >> "$BASHRC_FILE"
        echo "$RESTUD_EXPORT" >> "$BASHRC_FILE"
        echo -e "${GREEN}✓ Added RESTUD variable to ${BASHRC_FILE}${NC}"
    else
        echo -e "${GREEN}✓ RESTUD variable already configured in ${BASHRC_FILE}${NC}"
    fi
else
    echo -e "${RED}Warning: ${BASHRC_FILE} not found. Please manually add this line to your shell config:${NC}"
    echo "$RESTUD_EXPORT"
fi

# Verify installation
echo -e "\n${YELLOW}Verifying installation...${NC}"
if python3 -m restud.cli --version &> /dev/null; then
    echo -e "${GREEN}✓ REStud CLI installed successfully!${NC}"
else
    # Try with the command directly
    if command -v restud &> /dev/null; then
        echo -e "${GREEN}✓ REStud CLI installed successfully!${NC}"
    else
        echo -e "${YELLOW}Note: Please run 'source ~/.bashrc' or restart your terminal to use the 'restud' command.${NC}"
    fi
fi

echo -e "\n${GREEN}Installation complete!${NC}"
echo "To start using REStud CLI, run: source ~/.bashrc"
echo "Then test with: restud --help"
