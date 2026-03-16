#!/bin/bash
# =============================================================================
# Creality Cloud Klipper Plugin - Manager
# =============================================================================
# Interactive menu for managing the Creality Cloud Klipper Plugin.
# Run with: ./manage.sh
# =============================================================================

INSTALL_DIR="$HOME/creality-klipper-plugin"
VENV_DIR="$INSTALL_DIR/venv"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_ok()    { echo -e "  ${GREEN}[OK]${NC} $1"; }
print_warn()  { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "  ${RED}[ERROR]${NC} $1"; }
print_info()  { echo -e "  ${BLUE}[INFO]${NC} $1"; }

# =============================================================================
# HELPERS
# =============================================================================

get_printers() {
    PRINTERS=()
    for f in "$INSTALL_DIR"/config-*.json; do
        [ -f "$f" ] && PRINTERS+=("$(basename "$f" .json | sed 's/config-//')")
    done
}

get_moonraker_port() {
    local printer=$1
    local config="$INSTALL_DIR/config-${printer}.json"
    python3 -c "import json; d=json.load(open('$config')); u=d.get('moonraker_url','http://localhost:7125'); print(u.split(':')[-1])" 2>/dev/null || echo "7125"
}

printer_state() {
    local port=$1
    curl -s --max-time 3 "http://localhost:${port}/printer/objects/query?print_stats" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['status']['print_stats']['state'])" 2>/dev/null || echo "unknown"
}

press_enter() {
    echo ""
    read -p "  Press Enter to return to menu..."
}

# =============================================================================
# HEADER
# =============================================================================

print_header() {
    clear
    echo -e "${BLUE}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║       Creality Cloud Klipper Plugin Manager      ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# =============================================================================
# SHOW STATUS (inline, for menu display)
# =============================================================================

show_status_inline() {
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        echo -e "  ${YELLOW}No printers configured${NC}"
        return
    fi
    for name in "${PRINTERS[@]}"; do
        SERVICE="creality-klipper-${name}"
        port=$(get_moonraker_port "$name")
        state=$(printer_state "$port")
        if sudo systemctl is-active --quiet "$SERVICE"; then
            svc_status="${GREEN}running${NC}"
        else
            svc_status="${RED}stopped${NC}"
        fi
        echo -e "  ${CYAN}${name}${NC} — service: $(echo -e $svc_status), klipper: ${state}"
    done
}

# =============================================================================
# MENU 1: FULL STATUS
# =============================================================================

menu_status() {
    print_header
    echo -e "  ${BOLD}Printer Status${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    for name in "${PRINTERS[@]}"; do
        SERVICE="creality-klipper-${name}"
        port=$(get_moonraker_port "$name")
        state=$(printer_state "$port")
        if sudo systemctl is-active --quiet "$SERVICE"; then
            print_ok "${CYAN}${name}${NC} — RUNNING (klipper: ${state})"
        else
            print_error "${CYAN}${name}${NC} — STOPPED (klipper: ${state})"
        fi
    done
    press_enter
}

# =============================================================================
# MENU 2: RESTART ALL
# =============================================================================

menu_restart_all() {
    print_header
    echo -e "  ${BOLD}Restart All Services${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    for name in "${PRINTERS[@]}"; do
        SERVICE="creality-klipper-${name}"
        print_info "Restarting ${name}..."
        sudo systemctl restart "$SERVICE" && print_ok "$name restarted" || print_error "Failed to restart $name"
    done
    press_enter
}

# =============================================================================
# MENU 3: RESTART SINGLE
# =============================================================================

menu_restart_single() {
    print_header
    echo -e "  ${BOLD}Restart Single Printer${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    echo "  Select printer:"
    echo ""
    for i in "${!PRINTERS[@]}"; do
        echo "    $((i+1))) ${PRINTERS[$i]}"
    done
    echo ""
    read -p "  Enter number: " choice
    idx=$((choice-1))
    if [ -z "${PRINTERS[$idx]}" ]; then
        print_error "Invalid choice."
        press_enter; return
    fi
    name="${PRINTERS[$idx]}"
    SERVICE="creality-klipper-${name}"
    print_info "Restarting ${name}..."
    sudo systemctl restart "$SERVICE" && print_ok "$name restarted" || print_error "Failed to restart $name"
    press_enter
}

# =============================================================================
# MENU 4: VIEW LOGS
# =============================================================================

menu_logs() {
    print_header
    echo -e "  ${BOLD}View Logs${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    echo "  Select printer:"
    echo ""
    for i in "${!PRINTERS[@]}"; do
        echo "    $((i+1))) ${PRINTERS[$i]}"
    done
    echo ""
    read -p "  Enter number: " choice
    idx=$((choice-1))
    if [ -z "${PRINTERS[$idx]}" ]; then
        print_error "Invalid choice."
        press_enter; return
    fi
    name="${PRINTERS[$idx]}"
    echo ""
    print_info "Showing live logs for ${name} (Ctrl+C to stop)..."
    echo ""
    journalctl -u "creality-klipper-${name}" -f
}

# =============================================================================
# MENU 5: RESET STUCK JOB
# =============================================================================

menu_reset_stuck() {
    print_header
    echo -e "  ${BOLD}Reset Stuck Print Job${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    echo "  Use this when a print job is stuck on the download"
    echo "  screen in the Creality Cloud app."
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    echo "  Select printer:"
    echo ""
    for i in "${!PRINTERS[@]}"; do
        echo "    $((i+1))) ${PRINTERS[$i]}"
    done
    echo "    $((${#PRINTERS[@]}+1))) All printers"
    echo ""
    read -p "  Enter number: " choice

    reset_printer() {
        local name=$1
        local port=$(get_moonraker_port "$name")
        print_info "Resetting Klipper state for ${name}..."
        curl -s --max-time 5 -X POST "http://localhost:${port}/printer/gcode/script" \
            -H "Content-Type: application/json" \
            -d '{"script": "SDCARD_RESET_FILE"}' > /dev/null 2>&1 || true
        sleep 1
        print_info "Restarting service for ${name}..."
        sudo systemctl restart "creality-klipper-${name}"
        print_ok "${name} reset complete — try sending the job again"
    }

    if [ "$choice" -eq "$((${#PRINTERS[@]}+1))" ] 2>/dev/null; then
        for name in "${PRINTERS[@]}"; do
            reset_printer "$name"
        done
    else
        idx=$((choice-1))
        if [ -z "${PRINTERS[$idx]}" ]; then
            print_error "Invalid choice."
            press_enter; return
        fi
        reset_printer "${PRINTERS[$idx]}"
    fi
    press_enter
}

# =============================================================================
# MENU 6: ADD PRINTER
# =============================================================================

menu_add_printer() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash "$SCRIPT_DIR/install.sh" --add-printer
}

# =============================================================================
# MENU 7: REMOVE PRINTER
# =============================================================================

menu_remove_printer() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash "$SCRIPT_DIR/install.sh" --remove-printer
}

# =============================================================================
# MENU 8: UPDATE PLUGIN
# =============================================================================

menu_update() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash "$SCRIPT_DIR/install.sh" --update
    press_enter
}

# =============================================================================
# MENU 9: PRINTER SETTINGS
# =============================================================================

get_config_value() {
    local printer=$1
    local key=$2
    local default=$3
    python3 -c "
import json
try:
    d=json.load(open('$INSTALL_DIR/config-${printer}.json'))
    v=d.get('$key')
    if v is None:
        print('$default')
    elif v is True:
        print('true')
    elif v is False:
        print('false')
    else:
        print(str(v))
except:
    print('$default')
" 2>/dev/null
}

set_config_value() {
    local printer=$1
    local key=$2
    local value=$3
    python3 - <<PYEOF
import json
config_file = "$INSTALL_DIR/config-${printer}.json"
with open(config_file) as f:
    config = json.load(f)
if "$value" == "true":
    config["$key"] = True
elif "$value" == "false":
    config["$key"] = False
else:
    config["$key"] = "$value"
with open(config_file, "w") as f:
    json.dump(config, f, indent=2)
print("Saved")
PYEOF
}

menu_settings() {
    print_header
    echo -e "  ${BOLD}Printer Settings${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    get_printers
    if [ ${#PRINTERS[@]} -eq 0 ]; then
        print_warn "No printers configured."
        press_enter; return
    fi
    echo "  Select printer:"
    echo ""
    for i in "${!PRINTERS[@]}"; do
        echo "    $((i+1))) ${PRINTERS[$i]}"
    done
    echo ""
    read -p "  Enter number: " choice
    idx=$((choice-1))
    if [ -z "${PRINTERS[$idx]}" ]; then
        print_error "Invalid choice."
        press_enter; return
    fi
    name="${PRINTERS[$idx]}"

    while true; do
        print_header
        echo -e "  ${BOLD}Settings — ${CYAN}${name}${NC}"
        echo "  ─────────────────────────────────────────────────"
        echo ""

        # Read current values
        auto_bed=$(get_config_value "$name" "auto_bed_level" "false")
        port=$(get_moonraker_port "$name")

        # Display with toggle indicators
        if [ "$auto_bed" == "true" ]; then
            bed_display="${GREEN}ON${NC}"
        else
            bed_display="${RED}OFF${NC}"
        fi

        echo -e "    1) Auto bed leveling (BED_MESH_CALIBRATE before print) — $(echo -e $bed_display)"
        echo -e "    2) Moonraker port — ${CYAN}${port}${NC}"
        echo ""
        echo "    0) Back to main menu"
        echo ""
        echo "  ─────────────────────────────────────────────────"
        read -p "  Enter option: " sopt
        case $sopt in
            1)
                if [ "$auto_bed" == "true" ]; then
                    set_config_value "$name" "auto_bed_level" "false"
                    print_ok "Auto bed leveling disabled for $name"
                else
                    set_config_value "$name" "auto_bed_level" "true"
                    print_ok "Auto bed leveling enabled for $name"
                fi
                print_info "Restarting service to apply changes..."
                sudo systemctl restart "creality-klipper-${name}"
                sleep 1
                ;;
            2)
                echo ""
                read -p "  Enter new Moonraker port: " new_port
                if [[ "$new_port" =~ ^[0-9]+$ ]]; then
                    set_config_value "$name" "moonraker_url" "http://localhost:${new_port}"
                    print_ok "Moonraker port updated to $new_port"
                    print_info "Restarting service to apply changes..."
                    sudo systemctl restart "creality-klipper-${name}"
                    sleep 1
                else
                    print_error "Invalid port number"
                    sleep 1
                fi
                ;;
            0) return ;;
            *) print_warn "Invalid option" ; sleep 1 ;;
        esac
    done
}

# =============================================================================
# MAIN MENU LOOP
# =============================================================================

while true; do
    print_header
    echo -e "  ${BOLD}Printer Overview${NC}"
    echo "  ─────────────────────────────────────────────────"
    show_status_inline
    echo ""
    echo "  ─────────────────────────────────────────────────"
    echo -e "  ${BOLD}Actions${NC}"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    echo "    1) Show detailed status"
    echo "    2) Restart all services"
    echo "    3) Restart single printer"
    echo "    4) View live logs"
    echo "    5) Reset stuck print job"
    echo "    6) Printer settings"
    echo "    7) Add printer"
    echo "    8) Remove printer"
    echo "    9) Update plugin"
    echo "   10) Restart go2rtc (fixes camera stream delay)"
    echo "    0) Exit"
    echo ""
    echo "  ─────────────────────────────────────────────────"
    read -p "  Enter option: " opt
    case $opt in
        1) menu_status ;;
        2) menu_restart_all ;;
        3) menu_restart_single ;;
        4) menu_logs ;;
        5) menu_reset_stuck ;;
        6) menu_settings ;;
        7) menu_add_printer ;;
        8) menu_remove_printer ;;
        9) menu_update ;;
       10) sudo systemctl restart go2rtc && echo -e "\n  ${GREEN}[OK]${NC} go2rtc restarted" ; sleep 2 ;;
        0) echo ""; echo "  Bye! 👋"; echo ""; exit 0 ;;
        *) print_warn "Invalid option" ; sleep 1 ;;
    esac
done
