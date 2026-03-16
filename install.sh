#!/bin/bash
# =============================================================================
# Creality Cloud Klipper Plugin - Installer
# =============================================================================
# Connects Creality Cloud to Klipper/Moonraker printers without OctoPrint.
# Supports multiple printers, each with their own Moonraker instance.
#
# Usage:
#   ./install.sh                     - Install the plugin
#   ./install.sh --add-printer       - Add a new printer to an existing install
#   ./install.sh --remove-printer    - Remove a printer
#   ./install.sh --status            - Show status of all printer services
#   ./install.sh --update            - Update the plugin code
# =============================================================================

set -e

INSTALL_DIR="$HOME/creality-klipper-plugin"
VENV_DIR="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo -e "${BLUE}=========================================${NC}"
    echo -e "${BLUE}  Creality Cloud Klipper Plugin${NC}"
    echo -e "${BLUE}=========================================${NC}"
    echo ""
}

print_ok()      { echo -e "${GREEN}[OK]${NC} $1"; }
print_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
print_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }

# =============================================================================
# INSTALLATION
# =============================================================================
install_plugin() {
    print_header
    echo "Installing Creality Cloud Klipper Plugin..."
    echo ""

    # Check dependencies
    print_info "Checking dependencies..."
    if ! command -v python3 &>/dev/null; then
        print_error "python3 not found. Please install it first."
        exit 1
    fi
    if ! command -v pip3 &>/dev/null; then
        print_error "pip3 not found. Please install it first."
        exit 1
    fi
    print_ok "Dependencies OK"

    # Create install directory
    print_info "Creating install directory: $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"

    # Copy plugin files
    print_info "Copying plugin files..."
    cp "$SCRIPT_DIR/creality_klipper.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/setup_printer.py" "$INSTALL_DIR/"
    print_ok "Files copied"

    # Create virtual environment
    if [ ! -d "$VENV_DIR" ]; then
        print_info "Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
        print_ok "Virtual environment created"
    else
        print_ok "Virtual environment already exists"
    fi

    # Install Python dependencies
    print_info "Installing Python dependencies..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet requests tb-mqtt-client
    print_ok "Dependencies installed"

    echo ""
    print_ok "Plugin installed successfully!"
    echo ""
    echo "Next step: Add your first printer."
    echo ""
    add_printer
}

# =============================================================================
# ADD PRINTER
# =============================================================================
add_printer() {
    print_header
    echo "Add a Printer to Creality Cloud"
    echo ""
    echo "Before continuing, do the following in the Creality Cloud app:"
    echo "  1. Go to Printing → + → Raspberry Pi"
    echo "  2. Create a new device and give it a name"
    echo "  3. Tap 'Download Key File'"
    echo "  4. Open the key file and copy the token (the long eyJ... string)"
    echo "  5. After adding, go to device Settings → select and assign your printer model"
    echo ""

    # Printer name
    read -p "Enter a short name for this printer (e.g. CR10XPro, no spaces): " PRINTER_NAME
    if [ -z "$PRINTER_NAME" ]; then
        print_error "Printer name cannot be empty"
        exit 1
    fi

    # Check if already exists
    CONFIG_FILE="$INSTALL_DIR/config-${PRINTER_NAME}.json"
    if [ -f "$CONFIG_FILE" ]; then
        print_warn "Config for $PRINTER_NAME already exists: $CONFIG_FILE"
        read -p "Overwrite? (y/N): " OVERWRITE
        if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
            echo "Aborted."
            exit 0
        fi
    fi

    # Moonraker port
    echo ""
    echo "Moonraker port for this printer:"
    echo "  (default Moonraker is 7125, but multi-printer setups use different ports)"
    read -p "Moonraker port [7125]: " MOONRAKER_PORT
    MOONRAKER_PORT=${MOONRAKER_PORT:-7125}

    # JWT Token
    echo ""
    echo "Paste the JWT token from the Creality Cloud key file:"
    read -p "Token: " JWT_TOKEN
    if [ -z "$JWT_TOKEN" ]; then
        print_error "Token cannot be empty"
        exit 1
    fi

    # Exchange token for credentials
    print_info "Exchanging token for ThingsBoard credentials..."
    RESULT=$("$VENV_DIR/bin/python3" "$INSTALL_DIR/setup_printer.py" \
        --token "$JWT_TOKEN" \
        --moonraker-port "$MOONRAKER_PORT" \
        --output "$CONFIG_FILE")

    if [ $? -ne 0 ]; then
        print_error "Failed to get credentials from Creality Cloud."
        print_error "Check that your token is valid and not expired."
        exit 1
    fi

    print_ok "Credentials obtained and saved to $CONFIG_FILE"

    # Create systemd service
    SERVICE_NAME="creality-klipper-${PRINTER_NAME}"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    print_info "Creating systemd service: $SERVICE_NAME"

    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Creality Cloud Klipper Plugin - ${PRINTER_NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python3 creality_klipper.py --config ${CONFIG_FILE}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"

    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        print_ok "Service $SERVICE_NAME is running!"
    else
        print_warn "Service may not have started correctly. Check with:"
        echo "  journalctl -u $SERVICE_NAME -n 20"
    fi

    echo ""
    print_ok "Printer '$PRINTER_NAME' added successfully!"
    echo ""
    echo "Remember to go to the Creality Cloud app and:"
    echo "  → Device Settings → assign the correct printer model"
    echo ""
}

# =============================================================================
# REMOVE PRINTER
# =============================================================================
remove_printer() {
    print_header
    echo "Remove a Printer"
    echo ""

    # List existing printers
    CONFIGS=("$INSTALL_DIR"/config-*.json)
    if [ ${#CONFIGS[@]} -eq 0 ] || [ ! -f "${CONFIGS[0]}" ]; then
        print_warn "No printers found."
        exit 0
    fi

    echo "Installed printers:"
    for f in "${CONFIGS[@]}"; do
        name=$(basename "$f" .json | sed 's/config-//')
        status="stopped"
        if sudo systemctl is-active --quiet "creality-klipper-${name}"; then
            status="running"
        fi
        echo "  - $name ($status)"
    done
    echo ""

    read -p "Enter printer name to remove: " PRINTER_NAME
    if [ -z "$PRINTER_NAME" ]; then
        echo "Aborted."
        exit 0
    fi

    CONFIG_FILE="$INSTALL_DIR/config-${PRINTER_NAME}.json"
    SERVICE_NAME="creality-klipper-${PRINTER_NAME}"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "No config found for printer: $PRINTER_NAME"
        exit 1
    fi

    read -p "Are you sure you want to remove '$PRINTER_NAME'? (y/N): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi

    # Stop and disable service
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload

    # Remove config
    rm -f "$CONFIG_FILE"

    print_ok "Printer '$PRINTER_NAME' removed."
}

# =============================================================================
# STATUS
# =============================================================================
show_status() {
    print_header
    echo "Printer Service Status"
    echo ""

    CONFIGS=("$INSTALL_DIR"/config-*.json)
    if [ ${#CONFIGS[@]} -eq 0 ] || [ ! -f "${CONFIGS[0]}" ]; then
        print_warn "No printers configured."
        exit 0
    fi

    for f in "${CONFIGS[@]}"; do
        name=$(basename "$f" .json | sed 's/config-//')
        SERVICE_NAME="creality-klipper-${name}"
        if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
            print_ok "$name - RUNNING"
        else
            print_error "$name - STOPPED"
        fi
    done

    echo ""
    echo "Commands:"
    echo "  journalctl -u creality-klipper-<name> -f    # live logs"
    echo "  sudo systemctl restart creality-klipper-<name>"
    echo ""
}

# =============================================================================
# UPDATE
# =============================================================================
update_plugin() {
    print_header
    echo "Updating plugin files..."

    cp "$SCRIPT_DIR/creality_klipper.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/setup_printer.py" "$INSTALL_DIR/"
    print_ok "Plugin files updated"

    # Restart all services
    print_info "Restarting all printer services..."
    CONFIGS=("$INSTALL_DIR"/config-*.json)
    for f in "${CONFIGS[@]}"; do
        if [ -f "$f" ]; then
            name=$(basename "$f" .json | sed 's/config-//')
            SERVICE_NAME="creality-klipper-${name}"
            sudo systemctl restart "$SERVICE_NAME" 2>/dev/null && \
                print_ok "Restarted $SERVICE_NAME" || \
                print_warn "Could not restart $SERVICE_NAME"
        fi
    done

    echo ""
    print_ok "Update complete!"
}

# =============================================================================
# MAIN
# =============================================================================
case "${1:-}" in
    --add-printer)    add_printer ;;
    --remove-printer) remove_printer ;;
    --status)         show_status ;;
    --update)         update_plugin ;;
    "")               install_plugin ;;
    *)
        echo "Usage: $0 [--add-printer|--remove-printer|--status|--update]"
        exit 1
        ;;
esac
