#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Playerbots Server Installer
#  AzerothCore WotLK + Playerbots (compiled from source)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.4.4 - Debian
#
#  Usage:
#    chmod +x install-wow.sh
#    ./install-wow.sh
#
#  What this does:
#    1. Installs Docker and Git if needed
#    2. Shows a summary before building
#    3. Compiles AzerothCore + Playerbots (~2-4 hours)
#    4. Waits for the world server to initialize
#    5. Guides you through account creation
#    6. Sets up the Gaming Mode launcher
#
#  Changelog:
#    1.4.3 — Remove passwordless sudoers rule; add docker group consent
#      - Removed the /etc/sudoers.d/docker-nopasswd NOPASSWD write. Membership
#        in the docker group already grants root-equivalent access, so the
#        sudoers rule only added attack surface with no benefit.
#      - Added docker_group_consent(): warns that docker group membership is
#        effectively root-equivalent and asks for explicit consent before
#        joining. The installer no longer makes any privilege change silently.
#    1.4.2 — Custom server files install location
#      - Added choose_install_dir(): prompts user for a custom SERVER_DIR
#        before the install begins (blank = keep default ~/wow-server-playerbots)
#      - Useful for installing server files to an external drive or SD card;
#        Docker containers still live on the main disk
#      - Validates the chosen path: creates parent dir, checks write access,
#        and verifies at least 15 GB free at the target location
#    1.4.1 — Preflight dependency check
#      - Added preflight_check(): inspects docker daemon, docker compose,
#        docker buildx, git, and curl before the install begins
#      - Prints a visual status table (✅/❌) for each dependency
#      - Auto-installs any missing deps via apt-get / Docker CE repo
#      - Re-verifies all deps after install; exits with clear error if any fail
#    1.4.0 — Debian / Ubuntu port
#      - Replaced Fedora/dnf/rpm-ostree with apt + Docker CE (Debian)
#      - Distro detection now targets Ubuntu, Debian, Mint, Pop!_OS
#      - Removed immutable/rpm-ostree split — not applicable on Debian family
#      - Docker CE installed via official apt repo with GPG keyring
#      - Detects ubuntu vs debian Docker repo automatically
#      - install_git() uses apt-get
#      - Removed SELinux :Z volume label — not applicable on Debian family
#      - Updated confirmation box to show apt as package manager
#    1.3.0 — Fedora / Bazzite port
#      - Replaced pacman/Arch package management with dnf (Fedora)
#      - Removed check_pacman_keyring() — not applicable on Fedora
#      - Removed steamos-readonly / steamos-devmode calls
#      - Docker installed via official Docker CE repo for Fedora
#      - install_git() now uses dnf
#      - Removed hardcoded "deck" sudoers entry; uses $USER
#      - Removed Steam Deck hardware-specific messaging
#    1.2.0 — Playerbots-only focus
#      - Removed Base WoW and NPCBots options
#      - Single clear install path: Playerbots, compiled from source
#      - Fixed DB container name discovery (was hardcoded, broke on
#        non-default install dirs)
#      - Replaced sleep 15 DB wait with real connection polling
#    1.1.0 — Error handling overhaul
#      - Keyring reset now checks health first and requires confirmation
#      - install_docker() surfaces real errors instead of silencing them
#      - install_git() no longer reports success on failure
#      - SQL apply loops track and report failures
#      - systemctl start docker exits cleanly on failure
#      - Heredoc launcher synced with standalone launcher scripts
# ============================================================

WIZARD_VERSION="1.4.4 - Debian"

set -euo pipefail

# ─────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────
RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
MAGENTA='\033[0;35m'; NC='\033[0m'
GOLD='\033[38;5;220m'; DIM='\033[2m'

print_header() {
    clear
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${WHITE}${BOLD}         ⚙️  DAD'S MMO LAB                        ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${WHITE}         WoW Playerbots Installer                 ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${BLUE}         github.com/DadsMmoLab/dads-mmo-lab       ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${YELLOW}         Version ${WIZARD_VERSION}                              ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }

ask_yes_no() {
    while true; do
        echo -e "${WHITE}$1 (y/n): ${NC}"
        read -r answer
        case $answer in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Please answer y or n.";;
        esac
    done
}

press_enter() {
    echo ""
    echo -e "${WHITE}Press ENTER to continue...${NC}"
    read -r
}

# ─────────────────────────────────────────
# DOCKER GROUP CONSENT
# ─────────────────────────────────────────
# Membership in the docker group is effectively root-equivalent: a member can
# mount the host filesystem into a container and modify any file. We never
# make this change silently — warn the user and ask once before joining.
DOCKER_GROUP_CONSENT_DONE=0

docker_group_consent() {
    # Ask at most once per run.
    if [[ "$DOCKER_GROUP_CONSENT_DONE" == "1" ]]; then
        return 0
    fi

    # Already an active member of the docker group — nothing to change.
    if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        DOCKER_GROUP_CONSENT_DONE=1
        return 0
    fi

    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} ⚠️  Docker Group Membership — Please Read${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  To let you run ${CYAN}docker${NC} without typing ${CYAN}sudo${NC} each time,"
    echo -e "  the installer can add your user to the ${WHITE}docker${NC} group."
    echo ""
    echo -e "  ${RED}${BOLD}Heads up:${NC} membership in the docker group is effectively"
    echo -e "  equivalent to full root access on this machine. A docker"
    echo -e "  user can, for example, mount your entire disk inside a"
    echo -e "  container and modify any file."
    echo ""
    echo -e "  For a personal, offline game server on your own device this"
    echo -e "  is the standard, documented approach — but if you keep this"
    echo -e "  machine locked down, you can skip it and run docker with"
    echo -e "  ${CYAN}sudo${NC} instead."
    echo ""
    echo -e "  ${WHITE}This installer does NOT create any passwordless sudo rules.${NC}"
    echo ""
    if ! ask_yes_no "Add '$USER' to the docker group (grants root-equivalent access)?"; then
        echo ""
        print_warning "Skipped docker group membership."
        print_info "You'll need to prefix docker commands with 'sudo'."
        DOCKER_GROUP_CONSENT_DONE=1
        return 1
    fi
    DOCKER_GROUP_CONSENT_DONE=1
    return 0
}

enable_docker_sudo_wrapper() {
    if [[ -n "${USER:-}" ]]; then
        docker_group_consent && sudo usermod -aG docker "$USER" 2>/dev/null || true
    fi
    function docker() { sudo docker "$@"; }
    export -f docker 2>/dev/null || true
    print_info "Using sudo for Docker this session — works normally after next login"
}

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
SERVER_DIR="$HOME/wow-server-playerbots"
# Terminal detection — set globally so setup_gaming_mode and show_completion share state
TERM_BIN=""
TERM_ARGS=""

# ─────────────────────────────────────────
# CHOOSE INSTALL LOCATION
# ─────────────────────────────────────────
choose_install_dir() {
    print_step "Choose Server Files Location"

    local default_dir="$HOME/wow-server-playerbots"

    echo ""
    echo -e "  ${WHITE}${BOLD}Where should the server files be installed?${NC}"
    echo ""
    echo -e "  ${DIM}Default:${NC} ${CYAN}${default_dir}${NC}"
    echo ""
    echo -e "  ${WHITE}You can install the server files to a different location,${NC}"
    echo -e "  ${WHITE}such as an external drive or SD card, to save space on${NC}"
    echo -e "  ${WHITE}your main disk.${NC}"
    echo ""
    echo -e "  ${YELLOW}⚠️  Note: Docker containers (the compiled server images)${NC}"
    echo -e "  ${YELLOW}always live on your main disk regardless of this choice.${NC}"
    echo -e "  ${YELLOW}Only the source code, configs, and data files go here.${NC}"
    echo ""
    echo -e "  ${DIM}Leave blank and press ENTER to use the default location.${NC}"
    echo -e "  ${DIM}Example custom path: /media/user/external/wow-server${NC}"
    echo ""
    echo -ne "  ${WHITE}Install path: ${NC}"
    read -r user_input

    if [[ -z "$user_input" ]]; then
        SERVER_DIR="$default_dir"
        print_info "Using default location: ${SERVER_DIR}"
    else
        # Expand ~ and ensure the path is absolute
        user_input="${user_input/#\~/$HOME}"
        if [[ "$user_input" != /* ]]; then
            user_input="$(pwd)/$user_input"
        fi
        SERVER_DIR="$user_input"
        print_info "Using custom location: ${SERVER_DIR}"
    fi

    # Canonicalize (resolves .., repeated slashes, trailing slash) so the safety
    # checks below correctly handle /tmp/.., /, /tmp/ etc.
    SERVER_DIR=$(realpath -m -- "$SERVER_DIR")
    local _canon_default
    _canon_default=$(realpath -m -- "$default_dir")

    # Reject dangerous / well-known system roots
    case "$SERVER_DIR" in
        /|"$HOME"|/root|/tmp|/var|/etc|/usr|/boot|/proc|/sys|/dev)
            print_error "Cannot use '${SERVER_DIR}' as the install location."
            print_info "Choose a dedicated subdirectory (e.g. ${default_dir})."
            exit 1
            ;;
    esac

    # Reject if the destination already exists and is not a directory (incl. dangling symlinks)
    if [[ ( -e "$SERVER_DIR" || -L "$SERVER_DIR" ) && ! -d "$SERVER_DIR" ]]; then
        print_error "Install path exists but is not a directory: $SERVER_DIR"
        exit 1
    fi

    # For custom (non-default) paths: require the parent to already exist.
    # Don't silently mkdir deep paths — if a drive isn't mounted, that would
    # install onto the main disk instead.
    local parent_dir
    parent_dir="$(dirname "$SERVER_DIR")"

    if [[ "$SERVER_DIR" == "$_canon_default" ]]; then
        mkdir -p "$parent_dir" 2>/dev/null || {
            print_error "Cannot create directory: $parent_dir"
            exit 1
        }
    elif [[ ! -d "$parent_dir" ]]; then
        print_error "Parent directory does not exist: $parent_dir"
        print_info "Make sure your external drive or SD card is mounted first, then re-run."
        exit 1
    fi

    # Write probe using mktemp to avoid clobbering any existing user files
    local _write_probe
    _write_probe=$(mktemp "$parent_dir/.dml_probe_XXXXXX" 2>/dev/null) || {
        print_error "Cannot write to: $parent_dir"
        print_info "Check permissions or ensure the drive is mounted before running the installer."
        exit 1
    }
    rm -f "$_write_probe"

    # Check free space — probe SERVER_DIR if it already exists (may be a separate
    # mount point with a different filesystem than its parent); otherwise probe parent.
    local _space_probe
    [[ -d "$SERVER_DIR" ]] && _space_probe="$SERVER_DIR" || _space_probe="$parent_dir"

    local avail_gb
    avail_gb=$(df -BG "$_space_probe" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ') || true
    if [[ -z "$avail_gb" ]]; then
        print_error "Could not determine free space at ${_space_probe}. Cannot verify the 15 GB requirement."
        exit 1
    fi
    if [[ "$avail_gb" -lt 15 ]]; then
        print_error "Not enough space at ${_space_probe}. Need at least 15 GB, found ${avail_gb} GB."
        exit 1
    fi
    print_success "Install location OK — ${avail_gb} GB available at ${_space_probe}"

    echo ""
    echo -e "  ${WHITE}${BOLD}Server files will be installed to:${NC}"
    echo -e "  ${CYAN}${SERVER_DIR}${NC}"
    echo ""
    if ! ask_yes_no "Confirm this install location?"; then
        echo ""
        print_info "Re-run the installer to choose a different location."
        exit 0
    fi
}

# ─────────────────────────────────────────
# SYSTEM CHECKS
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System Requirements"

    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script supports Debian-based Linux only (Ubuntu, Mint, Pop!_OS, Debian)."
        exit 1
    fi
    print_success "Linux detected"

    # Verify this is a supported Debian-family distro
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        case "$ID" in
            ubuntu|debian|linuxmint|pop) ;;
            *)
                print_error "Unsupported distro: ${PRETTY_NAME:-$ID}"
                print_info "This script supports: Ubuntu, Debian, Linux Mint, Pop!_OS."
                print_info "If you're on a derivative, try adapting the script manually."
                exit 1
                ;;
        esac
        print_success "Supported distro detected: ${PRETTY_NAME:-$ID}"

        # Mint uses its own VERSION_CODENAME — Docker needs the upstream Ubuntu one
        if [[ "$ID" == "linuxmint" ]] && [[ -z "$UBUNTU_CODENAME" ]]; then
            print_error "Linux Mint detected but UBUNTU_CODENAME is not set in /etc/os-release."
            print_info "Cannot safely resolve the Ubuntu codename needed for Docker's apt repo."
            print_info "Make sure your /etc/os-release includes UBUNTU_CODENAME (standard on Mint 21+)."
            exit 1
        fi
    else
        print_warning "Could not read /etc/os-release — proceeding at your own risk."
    fi

    # ── Confirm detected package manager path with user ───────────────
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} Detected System Type${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${GREEN}✅ Debian-family Linux (${PRETTY_NAME:-$ID})${NC}"
    echo -e "  ${WHITE}Package manager: ${CYAN}apt${NC}"
    echo -e "  ${DIM}Docker will be installed via apt + Docker CE repo${NC}"
    echo ""
    echo -e "  ${YELLOW}Is this correct?${NC}"
    echo -e "  ${DIM}(If wrong, press Ctrl+C to exit and check your distro)${NC}"
    echo ""
    if ! ask_yes_no "Continue with the detected system type?"; then
        echo ""
        print_error "Aborted. Re-run once you've confirmed your distro."
        print_info "Expected: Ubuntu, Debian, Linux Mint, or Pop!_OS"
        exit 1
    fi

    AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 15 ] 2>/dev/null; then
        print_error "Not enough disk space. You have ${AVAILABLE_GB}GB free, need at least 15GB."
        exit 1
    fi
    print_success "Disk space OK (${AVAILABLE_GB:-unknown}GB available)"

    if ! ping -c 1 github.com &>/dev/null; then
        print_error "No internet connection. Please connect and try again."
        exit 1
    fi
    print_success "Internet connection OK"
}

# ─────────────────────────────────────────
# DEPENDENCY DIAGNOSTIC
# ─────────────────────────────────────────
# Usage: diagnose_dep_failure "docker" "docker compose" ...
diagnose_dep_failure() {
    local _deps=("$@")
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} 🔍 Dependency Diagnostic Report${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${RED}Failed:${NC} ${_deps[*]}"

    echo ""
    echo -e "${WHITE}── System environment ─────────────────────────────${NC}"
    echo -e "  HOME=${HOME}   USER=${USER}"
    echo -e "  OS: $(lsb_release -ds 2>/dev/null || grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 || echo 'unknown')"
    echo -e "  DOCKER_CONFIG=${DOCKER_CONFIG:-'(not set, defaults to ~/.docker)'}"
    echo -e "  DOCKER_CLI_PLUGIN_HOME=${DOCKER_CLI_PLUGIN_HOME:-'(not set)'}"
    echo -e "  XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-'(not set)'}"

    local _dep
    for _dep in "${_deps[@]}"; do
        echo ""
        echo -e "${WHITE}── Diagnosing: ${YELLOW}${_dep}${NC} ──────────────────────────────${NC}"

        case "$_dep" in
        "docker")
            echo -e "  ${WHITE}Binary:${NC}"
            command -v docker 2>/dev/null && ls -la "$(command -v docker)" 2>/dev/null \
                || echo "  docker binary not found in PATH"
            echo -e "  ${WHITE}dpkg status (docker-ce / docker.io):${NC}"
            dpkg -l docker-ce docker.io 2>/dev/null | grep -E "^[uihr]" \
                || echo "  (neither docker-ce nor docker.io installed)"
            echo -e "  ${WHITE}apt-cache policy docker-ce:${NC}"
            apt-cache policy docker-ce 2>/dev/null | head -6 || echo "  (apt-cache unavailable)"
            echo -e "  ${WHITE}Docker daemon status:${NC}"
            sudo systemctl status docker 2>/dev/null | head -8 || echo "  (systemctl unavailable)"
            echo -e "  ${WHITE}Docker socket:${NC}"
            ls -la /var/run/docker.sock 2>/dev/null || echo "  /var/run/docker.sock not found"
            ;;
        "docker compose")
            echo -e "  ${WHITE}Raw command output:${NC}"
            docker compose version 2>&1 || true
            echo -e "  ${WHITE}Plugin locations:${NC}"
            local _ecfg="${DOCKER_CONFIG:-$HOME/.docker}"
            for _dir in "/usr/lib/docker/cli-plugins" "/usr/local/lib/docker/cli-plugins" \
                        "/usr/libexec/docker/cli-plugins" "${_ecfg}/cli-plugins"; do
                [[ -f "$_dir/docker-compose" ]] \
                    && echo -e "  ${GREEN}FOUND:${NC} $(ls -la "$_dir/docker-compose" 2>/dev/null)" \
                    || echo -e "  ${DIM}ABSENT: $_dir/docker-compose${NC}"
            done
            echo -e "  ${WHITE}dpkg package:${NC}"
            dpkg -l docker-compose-plugin 2>/dev/null | grep -E "^[uihr]" \
                || echo "  (docker-compose-plugin not installed)"
            ;;
        "docker buildx")
            echo -e "  ${WHITE}Raw command output:${NC}"
            command docker buildx version 2>&1 || true
            echo -e "  ${WHITE}Plugin locations:${NC}"
            local _ecfg="${DOCKER_CONFIG:-$HOME/.docker}"
            for _dir in "/usr/lib/docker/cli-plugins" "/usr/local/lib/docker/cli-plugins" \
                        "/usr/libexec/docker/cli-plugins" "${_ecfg}/cli-plugins"; do
                [[ -f "$_dir/docker-buildx" ]] \
                    && echo -e "  ${GREEN}FOUND:${NC} $(ls -la "$_dir/docker-buildx" 2>/dev/null)" \
                    || echo -e "  ${DIM}ABSENT: $_dir/docker-buildx${NC}"
            done
            echo -e "  ${WHITE}dpkg package:${NC}"
            dpkg -l docker-buildx-plugin 2>/dev/null | grep -E "^[uihr]" \
                || echo "  (docker-buildx-plugin not installed)"
            echo -e "  ${WHITE}dpkg file integrity:${NC}"
            local _dpkg_verify
            _dpkg_verify=$(dpkg --verify docker-buildx-plugin 2>/dev/null)
            local _dpkg_rc=$?
            if [[ $_dpkg_rc -ne 0 ]]; then
                echo "  Package missing or dpkg verify unavailable"
            elif [[ -z "$_dpkg_verify" ]]; then
                echo "  OK (all files intact)"
            else
                echo "  Altered files detected:"
                echo "  $_dpkg_verify"
            fi
            ;;
        "git")
            command -v git 2>/dev/null || echo "  git not found in PATH"
            dpkg -l git 2>/dev/null | grep -E "^[uihr]" || echo "  git not in dpkg database"
            ;;
        "curl")
            command -v curl 2>/dev/null || echo "  curl not found in PATH"
            dpkg -l curl 2>/dev/null | grep -E "^[uihr]" || echo "  curl not in dpkg database"
            ;;
        *)
            command -v "$_dep" 2>/dev/null || echo "  '$_dep' not found in PATH"
            ;;
        esac
    done

    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD}  📋 Install log saved to:${NC}"
    echo -e "  ${CYAN}${INSTALL_LOG}${NC}"
    echo ""
    echo -e "${WHITE}  To report this issue, upload the log file to:${NC}"
    echo -e "  ${CYAN}https://github.com/DadsMmoLab/dads-mmo-lab/issues${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ─────────────────────────────────────────
# INSTALL DOCKER
# ─────────────────────────────────────────
install_buildx() {
    if command docker buildx version &>/dev/null 2>&1; then
        return 0
    fi
    print_info "Installing docker-buildx-plugin..."
    sudo apt-get update -qq 2>/dev/null || true
    if sudo apt-get install -y docker-buildx-plugin 2>/dev/null; then
        print_success "docker-buildx-plugin installed!"
    else
        print_warning "apt install of docker-buildx-plugin failed — trying Docker CLI plugin fallback..."
        local arch
        arch=$(uname -m)
        [[ "$arch" == "x86_64" ]] && arch="amd64"
        [[ "$arch" == "aarch64" ]] && arch="arm64"
        local plugin_dir="$HOME/.docker/cli-plugins"
        mkdir -p "$plugin_dir"
        if curl -fsSL "https://github.com/docker/buildx/releases/download/v0.23.0/buildx-v0.23.0.linux-${arch}" \
                -o "$plugin_dir/docker-buildx" 2>/dev/null; then
            chmod +x "$plugin_dir/docker-buildx"
            print_success "docker-buildx plugin installed to ~/.docker/cli-plugins/"
        else
            print_warning "Could not auto-install docker-buildx — the installer cannot continue without it."
            print_info "Install manually with: sudo apt-get install docker-buildx-plugin  then re-run this script."
        fi
    fi
}

install_docker() {
    # Check for working Docker CE with Compose plugin
    if command -v docker &>/dev/null; then
        if docker ps &>/dev/null 2>&1; then
            if docker compose version &>/dev/null 2>&1; then
                install_buildx
                print_success "Docker (with Compose plugin) already installed and running"
                return 0
            fi
            print_warning "Docker is running but the Compose plugin is missing."
            print_info "Attempting to install docker-compose-plugin..."
            sudo apt-get update -qq 2>/dev/null || true
            if sudo apt-get install -y docker-compose-plugin; then
                print_success "docker-compose-plugin installed!"
                return 0
            else
                print_error "Could not install docker-compose-plugin. Check your Docker CE repo setup."
                exit 1
            fi
        elif sudo docker ps &>/dev/null 2>&1; then
            if sudo docker compose version &>/dev/null 2>&1; then
                install_buildx
                enable_docker_sudo_wrapper
                print_success "Docker (with Compose plugin) already installed and running"
                return 0
            fi
            print_warning "Docker is running but the Compose plugin is missing."
            print_info "Attempting to install docker-compose-plugin..."
            sudo apt-get update -qq 2>/dev/null || true
            if sudo apt-get install -y docker-compose-plugin; then
                enable_docker_sudo_wrapper
                print_success "docker-compose-plugin installed!"
                return 0
            else
                print_error "Could not install docker-compose-plugin. Check your Docker CE repo setup."
                exit 1
            fi
        fi
    fi

    # Detect snap-installed Docker and warn — snap Docker is not compatible with this script
    if snap list docker &>/dev/null 2>&1; then
        echo ""
        print_warning "snap-installed Docker detected."
        echo -e "${YELLOW}  Snap Docker is not compatible with this installer.${NC}"
        echo -e "${YELLOW}  It must be removed before Docker CE can be installed.${NC}"
        echo ""
        if ask_yes_no "Remove snap Docker and install Docker CE instead?"; then
            sudo snap remove docker
            sleep 2
        else
            print_error "Cannot continue with snap Docker. Remove it manually and re-run."
            exit 1
        fi
    fi

    print_info "Installing Docker CE..."

    # Remove conflicting distro-packaged Docker before installing CE
    print_info "Removing any conflicting Docker packages..."
    for pkg in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
        sudo apt-get remove -y "$pkg" 2>/dev/null || true
    done

    # Install prerequisites
    print_info "Installing prerequisites..."
    if ! sudo apt-get update -qq; then
        print_warning "apt-get update failed — attempting to continue."
    fi
    if ! sudo apt-get install -y ca-certificates curl; then
        print_error "Failed to install prerequisites (ca-certificates, curl)."
        exit 1
    fi

    # Add Docker's official GPG key
    print_info "Adding Docker GPG key..."
    sudo install -m 0755 -d /etc/apt/keyrings

    # Determine correct Docker repo: ubuntu or debian
    # Mint and Pop!_OS are Ubuntu-based; pure Debian uses its own repo
    local DOCKER_REPO_DISTRO="ubuntu"
    if [[ "$ID" == "debian" ]]; then
        DOCKER_REPO_DISTRO="debian"
    fi

    if ! sudo curl -fsSL \
            "https://download.docker.com/linux/${DOCKER_REPO_DISTRO}/gpg" \
            -o /etc/apt/keyrings/docker.asc; then
        print_error "Failed to download Docker GPG key."
        exit 1
    fi
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    # Resolve the correct codename:
    # Mint sets UBUNTU_CODENAME (validated above); Ubuntu/Pop set VERSION_CODENAME
    local CODENAME
    if [[ "$ID" == "linuxmint" ]]; then
        CODENAME="$UBUNTU_CODENAME"
    else
        CODENAME="${VERSION_CODENAME}"
    fi
    if [[ -z "$CODENAME" ]]; then
        CODENAME=$(lsb_release -cs 2>/dev/null || true)
    fi
    if [[ -z "$CODENAME" ]]; then
        print_error "Could not determine OS codename. Cannot add Docker repo."
        exit 1
    fi

    print_info "Adding Docker CE repository (${DOCKER_REPO_DISTRO} / ${CODENAME})..."
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${DOCKER_REPO_DISTRO} ${CODENAME} stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    print_info "Updating package index with Docker CE repo..."
    if ! sudo apt-get update -qq; then
        print_error "apt-get update failed after adding Docker repo."
        print_info "  Repo:     ${DOCKER_REPO_DISTRO}"
        print_info "  Codename: ${CODENAME}"
        print_info "Check that this distro/codename is supported at: https://download.docker.com/linux/${DOCKER_REPO_DISTRO}/dists/"
        exit 1
    fi

    print_info "Installing Docker CE packages..."
    if ! sudo apt-get install -y \
            docker-ce docker-ce-cli containerd.io \
            docker-buildx-plugin docker-compose-plugin; then
        print_error "Failed to install Docker. Check your internet connection and repo setup."
        exit 1
    fi

    docker_group_consent && sudo usermod -aG docker "$USER" 2>/dev/null || true
    sleep 2

    sudo systemctl daemon-reload 2>/dev/null || \
        print_warning "systemctl daemon-reload failed — may need reboot"
    sudo systemctl enable docker 2>/dev/null || \
        print_warning "Could not enable Docker on boot — start manually if needed"

    if ! sudo systemctl start docker 2>/dev/null; then
        print_error "Docker failed to start. Try rebooting and running the installer again."
        exit 1
    fi

    sleep 3

    # docker group membership is enough for a passwordless 'docker' once the
    # group activates at next login. We intentionally do NOT write any
    # /etc/sudoers.d NOPASSWD rule — the docker group already grants
    # root-equivalent access, so a sudoers rule only adds attack surface.
    print_info "Docker permissions configured (docker group membership only)."

    # If docker still not accessible without sudo — wrap it
    if ! docker ps &>/dev/null 2>&1; then
        if sudo docker ps &>/dev/null 2>&1; then
            enable_docker_sudo_wrapper
        else
            print_error "Docker failed to start. Try rebooting and running again."
            exit 1
        fi
    fi

    print_success "Docker installed and permissions configured!"
}

# ─────────────────────────────────────────
# CHECK DOCKER HUB CONNECTIVITY
# ─────────────────────────────────────────
check_docker_hub() {
    print_info "Checking Docker Hub connectivity..."
    local registry="registry-1.docker.io"
    local ok=false

    if curl --silent --max-time 10 --head "https://${registry}/v2/" &>/dev/null; then
        ok=true
    elif wget --quiet --timeout=10 --spider "https://${registry}/v2/" &>/dev/null; then
        ok=true
    fi

    if ! $ok; then
        echo ""
        print_error "Cannot reach Docker Hub (${registry})"
        echo ""
        echo -e "  ${YELLOW}This is a network issue — not a code compilation error.${NC}"
        echo -e "  ${YELLOW}Docker cannot pull required images (e.g. mysql:8.4) without internet access.${NC}"
        echo ""
        echo -e "  ${CYAN}Troubleshooting steps:${NC}"
        echo -e "    1. Check your internet connection: ${CYAN}curl -I https://registry-1.docker.io/v2/${NC}"
        echo -e "    2. Check DNS:                      ${CYAN}nslookup registry-1.docker.io${NC}"
        echo -e "    3. If behind a firewall, ensure outbound HTTPS (port 443) to Docker Hub is allowed"
        echo -e "    4. If on a VPS, your provider may rate-limit Docker Hub — try a registry mirror:"
        echo -e "       Add to /etc/docker/daemon.json:  ${CYAN}{ \"registry-mirrors\": [\"https://mirror.gcr.io\"] }${NC}"
        echo -e "       Then restart Docker:              ${CYAN}sudo systemctl restart docker${NC}"
        echo ""
        exit 1
    fi

    print_success "Docker Hub is reachable"
}

install_git() {
    if command -v git &>/dev/null; then
        print_success "Git already installed"
        return 0
    fi
    print_info "Installing Git..."

    if sudo apt-get install -y git; then
        print_success "Git installed!"
    else
        print_warning "Git installation failed — some features may not work."
        print_info "Try manually: sudo apt-get install -y git"
    fi
}

# ─────────────────────────────────────────
# PREFLIGHT CHECK — SYSTEM DEPENDENCIES
# ─────────────────────────────────────────
preflight_check() {
    print_step "Preflight Check — System Dependencies"

    local docker_ok=false docker_compose_ok=false docker_buildx_ok=false
    local git_ok=false curl_ok=false all_ok=true docker_via_sudo=false

    # ── docker daemon ────────────────────────────────────────────────
    # Require unprivileged access — install_docker handles permission setup
    # when the daemon is running but the user isn't in the docker group yet.
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        docker_ok=true
    elif command -v docker &>/dev/null && sudo docker ps &>/dev/null 2>&1; then
        docker_ok=true
        docker_via_sudo=true
    else
        all_ok=false
    fi

    # ── docker compose plugin ────────────────────────────────────────
    # Only accept the plugin subcommand (`docker compose`); the legacy
    # standalone `docker-compose` binary is never used by this script.
    if [[ "$docker_via_sudo" == "true" ]]; then
        if sudo docker compose version &>/dev/null 2>&1; then
            docker_compose_ok=true
        else
            all_ok=false
        fi
    elif docker compose version &>/dev/null 2>&1; then
        docker_compose_ok=true
    else
        all_ok=false
    fi

    # ── docker buildx ────────────────────────────────────────────────
    if command docker buildx version &>/dev/null 2>&1; then
        docker_buildx_ok=true
    else
        all_ok=false
    fi

    # ── git ──────────────────────────────────────────────────────────
    if command -v git &>/dev/null; then
        git_ok=true
    else
        all_ok=false
    fi

    # ── curl ─────────────────────────────────────────────────────────
    if command -v curl &>/dev/null; then
        curl_ok=true
    else
        all_ok=false
    fi

    # ── Print status table ───────────────────────────────────────────
    echo ""
    printf "  ${WHITE}${BOLD}%-28s %s${NC}\n" "Dependency" "Status"
    echo -e "  ${DIM}──────────────────────────────────────${NC}"
    local _label _status _entry
    for _entry in \
        "docker (daemon):$docker_ok" \
        "docker compose:$docker_compose_ok" \
        "docker buildx:$docker_buildx_ok" \
        "git:$git_ok" \
        "curl:$curl_ok"; do
        _label="${_entry%%:*}"
        _status="${_entry##*:}"
        if [[ "$_status" == "true" ]]; then
            printf "  ${GREEN}✅${NC}  %-26s ${GREEN}OK${NC}\n" "$_label"
        else
            printf "  ${RED}❌${NC}  %-26s ${RED}MISSING${NC}\n" "$_label"
        fi
    done
    echo ""

    if [[ "$all_ok" == "true" ]]; then
        if [[ "$docker_via_sudo" == "true" ]]; then
            enable_docker_sudo_wrapper
        fi
        print_success "All dependencies satisfied — ready to build!"
        return 0
    fi

    print_info "Some dependencies are missing — installing now..."
    echo ""

    # ── Install Docker + Compose + Buildx if needed ──────────────────
    if [[ "$docker_ok" == "false" || "$docker_compose_ok" == "false" || \
          "$docker_buildx_ok" == "false" ]]; then
        install_docker
    fi

    # ── Install Git if needed ────────────────────────────────────────
    if [[ "$git_ok" == "false" ]]; then
        install_git
    fi

    # ── Install curl if needed (apt-get) ─────────────────────────────
    if [[ "$curl_ok" == "false" ]]; then
        print_info "Installing curl..."
        if ! sudo apt-get install -y curl; then
            print_error "Failed to install curl. Run manually: sudo apt-get install -y curl"
            exit 1
        fi
        print_success "curl installed!"
    fi

    # ── Re-verify after install ──────────────────────────────────────
    if ! command docker buildx version &>/dev/null 2>&1; then
        install_buildx
    fi

    print_info "Verifying all dependencies are now available..."
    echo ""

    local failed=()

    if command -v docker &>/dev/null; then
        print_success "docker binary:    $(command -v docker)"
    else
        print_error  "docker binary:    NOT FOUND"
        failed+=("docker")
    fi

    local _compose_ver
    if [[ "$docker_via_sudo" == "true" ]]; then
        _compose_ver=$(sudo docker compose version 2>&1) && _compose_ok=true || _compose_ok=false
    else
        _compose_ver=$(docker compose version 2>&1) && _compose_ok=true || _compose_ok=false
    fi
    if [[ "${_compose_ok}" == "true" ]]; then
        print_success "docker compose:   $(echo "$_compose_ver" | head -1)"
    else
        print_error  "docker compose:   NOT AVAILABLE"
        print_info   "  Raw output: $_compose_ver"
        failed+=("docker compose")
    fi

    local _buildx_ver
    if _buildx_ver=$(command docker buildx version 2>&1); then
        print_success "docker buildx:    $_buildx_ver"
    else
        print_error  "docker buildx:    NOT AVAILABLE"
        print_info   "  Raw output: $_buildx_ver"
        failed+=("docker buildx")
    fi

    if command -v git &>/dev/null; then
        print_success "git:              $(git --version 2>/dev/null)"
    else
        print_error  "git:              NOT FOUND"
        failed+=("git")
    fi

    if command -v curl &>/dev/null; then
        print_success "curl:             $(curl --version 2>/dev/null | head -1)"
    else
        print_error  "curl:             NOT FOUND"
        failed+=("curl")
    fi

    echo ""

    if [[ ${#failed[@]} -gt 0 ]]; then
        print_error "The following dependencies could not be installed: ${failed[*]}"
        echo ""
        print_warning "Running diagnostics for failed dependencies..."
        diagnose_dep_failure "${failed[@]}"
        print_error "Automatic installation failed. Check the output above for errors, then re-run this script."
        exit 1
    fi

    print_success "All dependencies installed and verified!"
}

# ─────────────────────────────────────────
# STEP 1 — SUMMARY AND CONFIRM
# ─────────────────────────────────────────
show_summary() {
    print_header
    print_step "STEP 1/4 — What We're Building"

    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${CYAN}WoW Playerbots (AzerothCore WotLK)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CYAN}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Install:${NC}  ${YELLOW}Compile from source (2-4 hours)${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}What you get:${NC}"
    echo -e "    ${GREEN}✅${NC} Hundreds of AI players roaming the world"
    echo -e "    ${GREEN}✅${NC} Bots quest, dungeon, raid alongside you"
    echo -e "    ${GREEN}✅${NC} Azeroth feels truly alive — solo or co-op"
    echo ""
    echo -e "${YELLOW}  ⚠️  COMPILATION WARNING:${NC}"
    echo -e "  This will take 2-4 hours on your machine."
    echo -e "  Keep it cool and connected to power."
    echo -e "  The fan will be loud. That's normal."
    echo ""

    if ! ask_yes_no "Ready to build your Playerbots server?"; then
        echo ""
        echo -e "${WHITE}No problem! Run this script again when you're ready.${NC}"
        exit 0
    fi
}

# ─────────────────────────────────────────
# STEP 2 — INSTALL SERVER
# ─────────────────────────────────────────
install_server() {
    print_header
    print_step "STEP 2/4 — Building Playerbots Server (2-4 hours)"

    # Install dependencies
    print_info "Checking dependencies..."
    install_docker
    install_git

    # ── Skip clone+compile if images already built ───────────────────
    # AzerothCore's compose setup builds and manages its own images.
    # If they already exist in $SERVER_DIR, skip the 2-4 hour compile
    # and just start the server — the rest of the install continues
    # normally (account creation, launcher setup, etc.).
    if [ -d "$SERVER_DIR" ] && \
       (cd "$SERVER_DIR" && docker compose images 2>/dev/null | grep -qi "worldserver"); then
        print_success "Compiled images already found in $SERVER_DIR"
        print_info "Skipping compile — reusing your existing build."
        print_info "To force a fresh compile, remove the server folder:"
        print_info "  sudo rm -rf \"$SERVER_DIR\""
        cd "$SERVER_DIR" || exit 1
        docker compose up -d 2>&1 | tail -5
        return 0
    fi

    # Images not found — handle existing folder before cloning
    if [ -d "$SERVER_DIR" ]; then
        print_warning "Existing folder found at $SERVER_DIR (no compiled images present)"
        if ask_yes_no "Remove it and start fresh?"; then
            docker compose -f "$SERVER_DIR/docker-compose.yml" down -v 2>/dev/null || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Old install removed"
        else
            print_info "Keeping existing install — exiting."
            exit 0
        fi
    fi

    print_info "Cloning Playerbots source..."
    print_info "Using official mod-playerbots fork"
    print_warning "This will take 2-4 hours to compile!"
    print_info "Keep your computer plugged in during the build!"

    git clone \
        https://github.com/mod-playerbots/azerothcore-wotlk.git \
        --branch=Playerbot \
        "$SERVER_DIR"

    if [ ! -d "$SERVER_DIR" ]; then
        print_error "Clone failed. Check your internet connection."
        exit 1
    fi

    mkdir -p "$SERVER_DIR/modules"

    print_info "Cloning mod-playerbots module..."
    if git clone --depth 1 \
        https://github.com/mod-playerbots/mod-playerbots.git \
        --branch=master \
        "$SERVER_DIR/modules/mod-playerbots"; then
        print_success "mod-playerbots module cloned!"
    else
        print_warning "mod-playerbots clone failed — check your connection."
        print_info "You can add it manually later: git clone ... $SERVER_DIR/modules/mod-playerbots"
    fi

    cat > "$SERVER_DIR/docker-compose.override.yml" << 'OVERRIDE'
services:
  ac-worldserver:
    build:
      context: .
      target: worldserver
    volumes:
      - ./modules:/azerothcore/modules
    environment:
      AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"
      AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "1600"
      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "2000"
  ac-authserver:
    build:
      context: .
      target: authserver
  ac-db-import:
    build:
      context: .
      target: db-import
  ac-client-data-init:
    build:
      context: .
      target: client-data
OVERRIDE

    check_docker_hub

    print_info "Compiling Playerbots server (2-4 hours)..."
    print_info "Progress saved to: ~/playerbots-build.log"
    print_info "Go make a coffee — this will take a while! ☕"

    cd "$SERVER_DIR"
    docker compose up -d --build 2>&1 | tee ~/playerbots-build.log

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        print_error "Compilation failed. Check ~/playerbots-build.log"
        exit 1
    fi

    print_success "Playerbots server compiled!"
}

# ─────────────────────────────────────────
# WAIT FOR SERVER READY
# ─────────────────────────────────────────
wait_for_server() {
    print_info "Waiting for world server to initialize..."
    print_info "First launch after compilation may take 10-15 minutes."
    echo ""

    TIMEOUT=1800
    ELAPSED=0
    READY=0
    WORLD_CONTAINER=""

    while [ $ELAPSED -lt $TIMEOUT ]; do
        WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
            2>/dev/null | grep -i "worldserver" | head -1)

        if [ -n "$WORLD_CONTAINER" ]; then
            if docker logs --tail 100 "$WORLD_CONTAINER" \
                2>/dev/null | grep -q "ready\.\.\."; then
                READY=1
                break
            fi
        fi

        printf "."
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done

    echo ""
    echo ""

    if [ $READY -eq 1 ]; then
        print_success "Server is READY! ⚔️"
    else
        print_warning "Server is taking longer than expected."
        print_info "Check progress: docker logs -f \"$WORLD_CONTAINER\""
        print_info "Wait for 'ready...' then create accounts manually."
    fi
}

# ─────────────────────────────────────────
# STEP 3 — CREATE ACCOUNTS
# ─────────────────────────────────────────
create_accounts() {
    print_header
    print_step "STEP 3/4 — Create Your Accounts"

    echo ""
    echo -e "${GREEN}${BOLD}Your server is running!${NC}"
    echo ""
    echo -e "${WHITE}Now create your account. Open a new terminal window${NC}"
    echo -e "${WHITE}and run these three steps:${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}1. Open the GM Console:${NC}"
    echo -e "   ${CYAN}docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}2. Create your account (replace USERNAME and PASSWORD):${NC}"
    echo -e "   ${GREEN}account create USERNAME PASSWORD${NC}"
    echo -e "   ${GREEN}account set gmlevel USERNAME 3 -1${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}3. Exit the console safely:${NC}"
    echo -e "   ${YELLOW}Ctrl+P then Ctrl+Q${NC}"
    echo -e "   ${RED}Never press Ctrl+C — that stops the server!${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${WHITE}Press ENTER when done creating accounts...${NC}"
    read -r
}

# ─────────────────────────────────────────
# STEP 4 — GAMING MODE SETUP
# ─────────────────────────────────────────
setup_gaming_mode() {
    print_step "STEP 4/4 — Setting Up Steam / Gaming Launcher"

    local launcher_path="$HOME/wow-playerbots-launcher.sh"
    local server_dir="$SERVER_DIR"

    # Detect available terminal emulator (global — also used by show_completion)
    TERM_BIN=""
    TERM_ARGS=""
    if command -v gnome-terminal &>/dev/null; then
        TERM_BIN="/usr/bin/gnome-terminal"
        TERM_ARGS="-- bash -c 'bash ~/wow-playerbots-launcher.sh; read -r'"
    elif command -v konsole &>/dev/null; then
        TERM_BIN="/usr/bin/konsole"
        TERM_ARGS="--hold -e bash ~/wow-playerbots-launcher.sh"
    elif command -v xterm &>/dev/null; then
        TERM_BIN="/usr/bin/xterm"
        TERM_ARGS="-hold -e bash ~/wow-playerbots-launcher.sh"
    fi

    cat > "$launcher_path" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — WoW Playerbots Launcher v${WIZARD_VERSION}
export PATH="/usr/bin:/usr/local/bin:/bin:\$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

LOGFILE="/tmp/wow-launch.log"
exec 2>"\$LOGFILE"

clear
echo ""
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
printf "   ${DIM}Dad's MMO Lab${NC}  ✦  ${DIM}WoW Playerbots${NC}\n"
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
echo ""
echo -e "  ${WHITE}${BOLD}Starting server...${NC}"
echo ""

# Stop any other running WoW servers first
# Only stops AzerothCore containers — never touches other Docker services
WOW_CONTAINERS=\$(docker ps --format '{{.Names}}' 2>/dev/null | \
    grep -iE "worldserver|authserver|ac-database|ac-eluna|ac-client|ac-db-import" || true)

if [ -n "\$WOW_CONTAINERS" ]; then
    echo -e "  ${YELLOW}⚠️  Stopping any running WoW servers first...${NC}"
    echo "\$WOW_CONTAINERS" | xargs docker stop >> "\$LOGFILE" 2>&1 || true
    sleep 5
    echo -e "  ${GREEN}✅ All clear!${NC}"
    echo ""
fi

cd "${server_dir}" || exit 1

if docker compose up -d --scale phpmyadmin=0 >> "\$LOGFILE" 2>&1; then
    echo -e "  ${GREEN}✅ Containers started!${NC}"
elif docker compose up -d >> "\$LOGFILE" 2>&1; then
    echo -e "  ${GREEN}✅ Containers started (phpmyadmin fallback used)${NC}"
else
    echo -e "  ${RED}❌ Failed to start server.${NC}"
    echo -e "  ${DIM}Check: \$LOGFILE${NC}"
    sleep 10
    exit 1
fi

echo ""
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo -e "${WHITE}${BOLD} Waiting for Azeroth to wake up...${NC}"
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo ""
echo -e "  ${DIM}First launch: 5-15 minutes${NC}"
echo -e "  ${DIM}After first launch: ~30 seconds${NC}"
echo ""

TIMEOUT=900
ELAPSED=0
READY=0
WORLD_CONTAINER=""

while [ \$ELAPSED -lt \$TIMEOUT ]; do
    WORLD_CONTAINER=\$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "worldserver" | head -1)
    if [ -n "\$WORLD_CONTAINER" ]; then
        if docker logs --tail 100 "\$WORLD_CONTAINER" 2>/dev/null | grep -q "ready\.\.\."; then
            READY=1
            break
        fi
    fi
    printf "  ${GOLD}.${NC}"
    sleep 5
    ELAPSED=\$((ELAPSED + 5))
done

echo ""
echo ""

if [ \$READY -eq 1 ]; then
    printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    echo -e "${GREEN}${BOLD}  ✅ AZEROTH IS READY!${NC}"
    printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
else
    echo -e "  ${YELLOW}⏳ Still initializing — launch WoW soon${NC}"
fi

echo ""
echo -e "  ${WHITE}${BOLD}Launch WoW from Steam or your desktop${NC}"
echo -e "  ${DIM}Server AUTO-SHUTS DOWN when WoW closes${NC}"
echo -e "  ${DIM}── or press ENTER to shut down manually ──${NC}"
echo ""

MANUAL_SHUTDOWN=0
WOW_STARTED=0
for i in \$(seq 1 60); do
    if pgrep -fi "Wow\\.exe|wine.*[Ww]o[Ww]" > /dev/null 2>&1; then
        WOW_STARTED=1
        break
    fi
    if read -r -t 5 2>/dev/null; then
        MANUAL_SHUTDOWN=1
        break
    fi
done

if [ \$MANUAL_SHUTDOWN -eq 0 ]; then
    if [ \$WOW_STARTED -eq 1 ]; then
        echo -e "  ${GREEN}⚔️  WoW detected! Enjoy Azeroth!${NC}"
        while pgrep -fi "Wow\\.exe|wine.*[Ww]o[Ww]" > /dev/null 2>&1; do
            if read -r -t 3 2>/dev/null; then
                MANUAL_SHUTDOWN=1
                break
            fi
        done
        if [ \$MANUAL_SHUTDOWN -eq 0 ]; then
            sleep 5
            echo -e "  ${YELLOW}WoW closed — shutting down...${NC}"
        fi
    else
        echo -e "  ${DIM}WoW not detected — press ENTER to shut down.${NC}"
        read -r
    fi
fi

if [ \$MANUAL_SHUTDOWN -eq 1 ]; then
    echo -e "  ${YELLOW}Manual shutdown — shutting down...${NC}"
fi

cd "${server_dir}" && docker compose down >> "\$LOGFILE" 2>&1

echo ""
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo -e "${GREEN}${BOLD}  ✅ Server stopped! Safe to close.${NC}"
echo -e "  ${DIM}Thanks for playing! youtube.com/@DadsMmoLab${NC}"
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo ""
sleep 5
LAUNCHER

    chmod +x "$launcher_path"
    print_success "Steam / Gaming Mode launcher created: ~/wow-playerbots-launcher.sh"

    # Save server info — build the Steam launcher line dynamically
    local steam_target_line=""
    if [[ -n "$TERM_BIN" ]]; then
        steam_target_line="    Target:  ${TERM_BIN}
    Options: ${TERM_ARGS}
    Proton:  OFF (launcher needs no Proton)"
    else
        steam_target_line="    Run directly: bash ~/wow-playerbots-launcher.sh"
    fi

    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — WoW Playerbots
  AzerothCore WotLK + Playerbots
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realmlist: 127.0.0.1
  Account:   create via worldserver console (see below)

LAUNCHER:
  Path: ~/wow-playerbots-launcher.sh
  Add to Steam (optional):
${steam_target_line}

REALMLIST (in your WoW client folder):
  Edit:  realmlist.wtf
  Set to: set realmlist 127.0.0.1

USEFUL COMMANDS:
  Start:   cd "${SERVER_DIR}" && docker compose up -d
  Stop:    cd "${SERVER_DIR}" && docker compose down
  Logs:    cd "${SERVER_DIR}" && docker compose logs -f
  Console: docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)
    (Exit safely: Ctrl+P then Ctrl+Q. NOT Ctrl+C.)

CREATE ACCOUNTS:
  docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)
  account create USERNAME PASSWORD
  account set gmlevel USERNAME 3 -1   (optional: makes GM)
  [Ctrl+P then Ctrl+Q to exit safely]
INFO

    print_success "Server info saved to: $SERVER_DIR/MY_SERVER.txt"
}

# ─────────────────────────────────────────
# DONE
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# POST-INSTALL RESOURCES
# ─────────────────────────────────────────
post_install_resources() {
    echo ""
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP D — Resources & Server Management${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}The README covers everything you need next:${NC}"
    echo -e "    • Networking (LAN / online play / port forwarding)"
    echo -e "    • Server commands and GM tools"
    echo -e "    • Playerbot configuration"
    echo -e "    • Troubleshooting and FAQ"
    echo ""
    echo -e "  ${CYAN}${BOLD}https://github.com/DadsMmoLab/dads-mmo-lab${NC}"
    echo ""
    if ask_yes_no "Open the GitHub README in your browser now?"; then
        if command -v xdg-open &>/dev/null; then
            xdg-open "https://github.com/DadsMmoLab/dads-mmo-lab" &>/dev/null &
            print_success "Opening browser..."
        else
            print_info "Open this URL in your browser:"
            echo -e "  ${CYAN}https://github.com/DadsMmoLab/dads-mmo-lab${NC}"
        fi
    fi
    echo ""
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}wow-manage.sh${NC} is a post-install management tool:"
    echo -e "    • Start / stop / restart the server"
    echo -e "    • View live server logs"
    echo -e "    • Add or remove modules (AH Bot, Solocraft, Transmog…)"
    echo -e "    • Attach to the worldserver console"
    echo ""
    echo -e "  After downloading, run it any time with:"
    echo -e "  ${GREEN}bash ~/wow-manage.sh${NC}"
    echo ""
    if ask_yes_no "Download wow-manage.sh to your home folder now?"; then
        local manage_url="https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/wow-manage.sh"
        if curl -fsSL "$manage_url" -o "$HOME/wow-manage.sh"; then
            chmod +x "$HOME/wow-manage.sh"
            print_success "Downloaded to ~/wow-manage.sh"
            print_info "Run it any time with: bash ~/wow-manage.sh"
        else
            print_error "Download failed. Get it manually from:"
            echo -e "  ${CYAN}https://github.com/DadsMmoLab/dads-mmo-lab${NC}"
        fi
    fi
    echo ""
}

show_completion() {
    echo ""
    echo -e "${GOLD}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${GOLD}${BOLD}║   🎉 YOUR PLAYERBOTS SERVER IS READY!            ║${NC}"
    echo -e "${GOLD}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${CYAN}WoW Playerbots (AzerothCore WotLK)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CYAN}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Launcher:${NC} ${CYAN}~/wow-playerbots-launcher.sh${NC}"
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP A — Set Your WoW Realmlist${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Open your WoW client folder in the file manager"
    echo -e "  2. Find and open: ${CYAN}realmlist.wtf${NC}"
    echo -e "  3. Make sure it says exactly: ${GREEN}set realmlist 127.0.0.1${NC}"
    echo -e "  4. Save the file"
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP B — Add to Steam / Gaming Mode${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  Your launcher was created here:"
    echo ""
    echo -e "  ${GREEN}${BOLD}~/wow-playerbots-launcher.sh${NC}"
    echo ""
    if [[ -n "$TERM_BIN" ]]; then
        local term_name
        term_name=$(basename "$TERM_BIN")
        echo -e "  Add it to Steam (optional, for Gaming Mode):"
        echo -e "  1. Open Steam in Desktop Mode"
        echo -e "  2. Click ${CYAN}Games${NC} → ${CYAN}Add a Non-Steam Game${NC}"
        echo -e "  3. Click ${CYAN}Browse${NC} → navigate to ${CYAN}/usr/bin/${NC}"
        echo -e "  4. Select ${CYAN}${term_name}${NC} → click ${CYAN}Add Selected Programs${NC}"
        echo -e "  5. Find ${CYAN}${term_name}${NC} in your library → right-click → ${CYAN}Properties${NC}"
        echo -e "  6. Rename it to: ${GREEN}WoW Playerbots Server${NC}"
        echo -e "  7. Set Launch Options to exactly:"
        echo ""
        echo -e "  ${GREEN}${TERM_ARGS}${NC}"
        echo ""
        echo -e "  8. Under Compatibility — ${RED}do NOT enable Proton${NC}"
    else
        echo -e "  No supported terminal found. Run the launcher directly:"
        echo -e "  ${GREEN}bash ~/wow-playerbots-launcher.sh${NC}"
    fi
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP C — Play!${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Launch ${CYAN}WoW Playerbots Server${NC} from Steam or your terminal"
    echo -e "  2. Watch the dots... wait for ${GREEN}AZEROTH IS READY!${NC}"
    echo -e "  3. Launch WoW"
    echo -e "  4. Login with the account you created"
    echo -e "  5. Play! Bots populate within 5-10 min — be patient!"
    echo -e "  6. Close WoW → server shuts down automatically ✅"
    echo ""
    echo -e "  ${YELLOW}Server info saved at: $SERVER_DIR/MY_SERVER.txt${NC}"
    echo ""
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${NC}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${NC}"
    echo -e "${WHITE}  ☕ ko-fi.com/dadsmmolab${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${GREEN}${BOLD}Welcome to Azeroth. It's yours now. Forever. ⚔️${NC}"
    echo ""
    echo -e "${YELLOW}  ℹ️  Your server is still running right now!${NC}"
    echo -e "${YELLOW}  To stop it: ${CYAN}cd $SERVER_DIR && docker compose down${NC}"
    echo -e "${YELLOW}  Or just use the Steam / Gaming Mode launcher next time.${NC}"
    echo ""
    if ask_yes_no "Would you like to stop the server now?"; then
        print_info "Stopping server..."
        cd "$SERVER_DIR" && docker compose down
        print_success "Server stopped! Use the Steam / Gaming Mode launcher to start it next time."
    else
        print_info "Server left running — enjoy Azeroth! ⚔️"
    fi
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
print_header

echo -e "${WHITE}Welcome to the WoW Playerbots installer!${NC}"
echo -e "${WHITE}Hundreds of AI players will roam your Azeroth,${NC}"
echo -e "${WHITE}quest, run dungeons, and make the world feel alive.${NC}"
echo ""
echo -e "${BLUE}This takes about 5 minutes to set up, then${NC}"
echo -e "${BLUE}compiles itself over 2-4 hours. Plug in and walk away.${NC}"
echo ""

if ! ask_yes_no "Ready to begin?"; then
    echo "No problem — run this script when you're ready!"
    exit 0
fi

check_system

# ─────────────────────────────────────────
# SESSION LOGGING
# ─────────────────────────────────────────
INSTALL_LOG="${HOME}/dads-mmo-lab-install-$(date +%Y%m%d-%H%M%S).log"
if ! : > "$INSTALL_LOG" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Could not create log file at ${INSTALL_LOG} — continuing without logging.${NC}"
    INSTALL_LOG="/dev/null"
fi
exec > >(tee -a "$INSTALL_LOG") 2>&1
[[ "$INSTALL_LOG" != "/dev/null" ]] && \
    echo -e "${DIM}📋 Logging this session to: ${INSTALL_LOG}${NC}" && \
    echo -e "${DIM}   Upload this file if you need help at: github.com/DadsMmoLab/dads-mmo-lab/issues${NC}"

echo ""
echo -e "\033[1;33m⚠️  This installer needs sudo access for:\033[0m"
echo -e "\033[1;33m   • Installing Docker (if not present)\033[0m"
echo -e "\033[1;33m   • Fixing file ownership after build\033[0m"
echo ""
echo -e "\033[1;37mPlease enter your password if prompted:\033[0m"
if ! sudo -v; then
    echo -e "\033[0;31m❌ Could not cache sudo credentials. Aborting.\033[0m"
    exit 1
fi
( while true; do sudo -n true; sleep 60; done ) 2>/dev/null &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null; exit" EXIT INT TERM

preflight_check
choose_install_dir
show_summary
install_server
wait_for_server
create_accounts
setup_gaming_mode
show_completion
post_install_resources
