#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Playerbots Server Installer
#  AzerothCore WotLK + Playerbots (compiled from source)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.2.8
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
#    1.2.8 — Shadow binary bug fix (SteamOS docker-buildx false-negative)
#      - Root cause: a corrupt user-level ~/.docker/cli-plugins/docker-buildx
#        (e.g. an HTML rate-limit response from a prior curl fallback attempt)
#        shadows the valid pacman-installed system binary because Docker CLI
#        scans ~/.docker/cli-plugins BEFORE /usr/lib/docker/cli-plugins.
#      - Fix 1 (install_buildx): after pacman succeeds, detect and remove any
#        stale user-level binary that is either <10 MB or smaller than the
#        system binary, preventing it from blocking plugin discovery.
#      - Fix 2 (install_buildx curl fallback): validate the downloaded file is
#        a real ELF binary (magic bytes 7f454c46) and ≥10 MB before installing;
#        reject HTML error pages and partial downloads.
#      - Fix 3 (diagnose_dep_failure): add "Shadow binary check" that compares
#        system vs user-level binary sizes and prints a clear fix command when
#        a corrupt shadow file is detected.
#    1.2.5 — Fix docker-buildx false-negative under sudo (SteamOS)
#      - Root cause: install_docker() sets DOCKER_CMD="sudo docker" when the
#        user isn't yet in the docker group. All subsequent preflight buildx
#        checks then ran `sudo docker buildx version`, which fails on SteamOS
#        because sudo resets $HOME to /root and the Docker CLI plugin path
#        diverges from the user-facing path where pacman installed the plugin.
#      - Fix: docker buildx is a client-side plugin — it has no dependency on
#        socket access and must never run via DOCKER_CMD/sudo. All three call
#        sites (install_buildx guard, preflight first-pass, preflight re-verify
#        and failed[] check) now use plain `docker buildx version`.
#    1.2.4 — Custom server files install location
#      - Added choose_install_dir(): prompts user for a custom SERVER_DIR
#        before the install begins (blank = keep default ~/wow-server-playerbots)
#      - Useful for installing server files to an external drive or SD card;
#        Docker containers still live on the main disk
#      - Validates the chosen path: creates parent dir, checks write access,
#        and verifies at least 15 GB free at the target location
#    1.2.3 — Fix missing docker-buildx dependency
#      - install_docker() now installs docker-buildx alongside docker and
#        docker-compose; previously the preflight buildx check would fail
#        with a missing-plugin error after a fresh Docker install
#    1.2.2 — Preflight dependency check
#      - Added preflight_check(): inspects docker daemon, docker compose,
#        docker buildx, git, and curl before the install begins
#      - Prints a visual status table (✅/❌) for each dependency
#      - Auto-installs any missing deps via pacman (respects steamos-readonly)
#      - Re-verifies all deps after install; exits with clear error if any fail
#    1.2.1 — DML staged restart hook (Windows/WSL)
#      - Ships dml-start.sh: restarts auth/world without re-running db-import
#      - Pins realm to 127.0.0.1; waits for DB healthy before starting servers
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

WIZARD_VERSION="1.2.9"

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
# CONFIGURATION
# ─────────────────────────────────────────
SERVER_DIR="$HOME/wow-server-playerbots"

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
    echo -e "  ${DIM}Example custom path: /run/media/deck/mysd/wow-server${NC}"
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
        print_error "This script requires Linux (SteamOS, CachyOS). Are you in Desktop Mode?"
        exit 1
    fi
    print_success "Linux detected"

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
# KEYRING HEALTH CHECK
# ─────────────────────────────────────────
check_pacman_keyring() {
    print_info "Checking pacman keyring health..."

    local keyring_broken=false

    # Test 1: Keyring directory and pubring exist?
    if [[ ! -d /etc/pacman.d/gnupg ]] || [[ ! -f /etc/pacman.d/gnupg/pubring.gpg ]]; then
        print_warning "Keyring directory missing or incomplete."
        keyring_broken=true
    fi

    # Test 2: Can pacman-key list keys without errors?
    if ! sudo pacman-key --list-keys &>/dev/null; then
        print_warning "pacman-key cannot list keys — keyring may be corrupted."
        keyring_broken=true
    fi

    # Test 3: Can pacman sync at all?
    if ! sudo pacman -Sy &>/dev/null; then
        print_warning "pacman sync failed — possible keyring or signature issue."
        keyring_broken=true
    fi

    if [[ "$keyring_broken" == false ]]; then
        print_success "Keyring healthy — no reset needed."
        return 0
    fi

    # ── Keyring is broken — warn user before doing anything ──
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║${WHITE}${BOLD}          ⚠️  KEYRING RESET REQUIRED              ${NC}${RED}║${NC}"
    echo -e "${RED}╠══════════════════════════════════════════════════╣${NC}"
    echo -e "${RED}║${NC}  Your pacman keyring appears broken or corrupt.  ${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${NC}  To fix it, the installer needs to:              ${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Delete /etc/pacman.d/gnupg               ${NC}${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Reinitialize the keyring                 ${NC}${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Repopulate Arch + Holo (SteamOS) keys   ${NC}${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${WHITE}  ⚠️  Any custom keys you added manually will   ${NC}${RED}║${NC}"
    echo -e "${RED}║${WHITE}  be removed. Re-add them after installation    ${NC}${RED}║${NC}"
    echo -e "${RED}║${WHITE}  if your system needs them.                    ${NC}${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${GREEN}  This is safe for most standard Steam Decks.  ${NC}${RED}║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${WHITE}Type ${GREEN}yes${WHITE} to reset the keyring, or anything else to cancel: ${NC}"
    read -r confirm
    echo ""

    if [[ "$confirm" != "yes" ]]; then
        print_error "Keyring reset cancelled."
        print_info "Fix your keyring manually and re-run the installer."
        print_info "Guide: https://wiki.archlinux.org/title/Pacman/Package_signing"
        echo ""
        exit 1
    fi

    print_info "Resetting keyring..."
    sudo rm -rf /etc/pacman.d/gnupg
    sudo pacman-key --init
    sudo pacman-key --populate archlinux
    sudo pacman-key --populate holo
    print_success "Keyring reset complete."
    echo ""
}

# ─────────────────────────────────────────
# INSTALL DOCKER
# ─────────────────────────────────────────
install_buildx() {
    # buildx version is a client-side check — no socket access needed, never use sudo here.
    if docker buildx version &>/dev/null 2>&1; then
        return 0
    fi
    print_info "Installing docker-buildx..."
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || true
        # RETURN trap ensures SteamOS filesystem is re-locked even if the shell
        # exits early due to set -euo pipefail before we reach the explicit enable.
        trap 'sudo steamos-readonly enable 2>/dev/null || true' RETURN
    fi
    if sudo pacman -Sy --noconfirm docker-buildx 2>/dev/null; then
        print_success "docker-buildx installed!"
        # Flush pacman's post-transaction hooks and re-check immediately.
        # On SteamOS the binary may land in /usr/lib/docker/cli-plugins/ which
        # Docker discovers dynamically — give the filesystem a moment to sync.
        hash -r 2>/dev/null || true
        sleep 1

        # Resolve the effective Docker config dir the same way Docker CLI does,
        # so the shadow check targets the path Docker would actually consult first.
        local _docker_cfg="${DOCKER_CONFIG:-$HOME/.docker}"
        local _sys_bin="/usr/lib/docker/cli-plugins/docker-buildx"
        local _user_bin="${_docker_cfg}/cli-plugins/docker-buildx"

        # If a user-level binary exists alongside the system one, check whether
        # it is actually functional. Docker CLI scans the user path first, so a
        # broken file there will silently block the valid system binary.
        if [[ -f "$_sys_bin" && -f "$_user_bin" ]]; then
            if ! "$_user_bin" docker-cli-plugin-metadata &>/dev/null 2>&1 && \
               ! "$_user_bin" version &>/dev/null 2>&1; then
                local _usr_sz
                _usr_sz=$(stat -c%s "$_user_bin" 2>/dev/null || echo 0)
                print_warning "User-level docker-buildx (${_usr_sz} bytes) at ${_user_bin} may be shadowing"
                print_warning "the system binary but fails to execute — removing it..."
                rm -f "$_user_bin" || true
                print_info "Removed non-functional ${_user_bin}."
            fi
        fi
    else
        print_warning "pacman install of docker-buildx failed — trying Docker CLI plugin fallback..."
        local arch
        arch=$(uname -m)
        [[ "$arch" == "x86_64" ]] && arch="amd64"
        [[ "$arch" == "aarch64" ]] && arch="arm64"
        local _docker_cfg="${DOCKER_CONFIG:-$HOME/.docker}"
        local plugin_dir="${_docker_cfg}/cli-plugins"
        mkdir -p "$plugin_dir" || true
        local _dl_tmp="${plugin_dir}/docker-buildx.tmp"
        print_info "Downloading docker-buildx binary to ${plugin_dir}/ ..."
        if curl -fsSL "https://github.com/docker/buildx/releases/download/v0.23.0/buildx-v0.23.0.linux-${arch}" \
                -o "$_dl_tmp" 2>/dev/null; then
            # Validate: must be an ELF binary and at least 10 MB.
            # This guards against GitHub rate-limit HTML responses and partial downloads.
            local _dl_sz
            _dl_sz=$(stat -c%s "$_dl_tmp" 2>/dev/null || echo 0)
            local _dl_magic
            _dl_magic=$(head -c 4 "$_dl_tmp" 2>/dev/null | od -A n -t x1 | tr -d ' \n' || echo "")
            if (( _dl_sz < 10485760 )) || [[ "$_dl_magic" != "7f454c46" ]]; then
                print_warning "Downloaded file is not a valid binary (${_dl_sz} bytes, magic=${_dl_magic})."
                print_warning "This is likely a GitHub rate-limit or network error."
                print_info "  Manual fix: sudo pacman -S docker-buildx  then re-run this script."
                rm -f "$_dl_tmp" || true
                return 1
            fi
            if mv "$_dl_tmp" "${plugin_dir}/docker-buildx" && \
               chmod +x "${plugin_dir}/docker-buildx"; then
                print_success "docker-buildx plugin installed to ${plugin_dir}/"
            else
                print_warning "Could not finalize docker-buildx installation."
                rm -f "$_dl_tmp" "${plugin_dir}/docker-buildx" 2>/dev/null || true
                return 1
            fi
        else
            rm -f "$_dl_tmp" 2>/dev/null || true
            print_warning "Could not auto-install docker-buildx — the installer cannot continue without it."
            print_info "Install manually with: sudo pacman -S docker-buildx  then re-run this script."
            return 1
        fi
    fi
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly enable 2>/dev/null || true
    fi
}

# ─────────────────────────────────────────
# DEPENDENCY DIAGNOSTIC
# ─────────────────────────────────────────
# Usage: diagnose_dep_failure "docker" "docker compose" "docker buildx" ...
# Runs targeted diagnostics for each failed dependency.
diagnose_dep_failure() {
    local _deps=("$@")
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} 🔍 Dependency Diagnostic Report${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${RED}Failed:${NC} ${_deps[*]}"

    # ── System info (always shown) ───────────────────────────────────
    echo ""
    echo -e "${WHITE}── System environment ─────────────────────────────${NC}"
    echo -e "  HOME=${HOME}   USER=${USER}"
    echo -e "  DOCKER_CONFIG=${DOCKER_CONFIG:-'(not set, defaults to ~/.docker)'}"
    echo -e "  DOCKER_CLI_PLUGIN_HOME=${DOCKER_CLI_PLUGIN_HOME:-'(not set)'}"
    echo -e "  XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-'(not set)'}"
    if command -v steamos-readonly &>/dev/null; then
        echo -e "  SteamOS read-only: $(sudo steamos-readonly status 2>/dev/null || echo 'unknown')"
    fi

    # ── Per-dependency diagnostics ───────────────────────────────────
    local _dep
    for _dep in "${_deps[@]}"; do
        echo ""
        echo -e "${WHITE}── Diagnosing: ${YELLOW}${_dep}${NC} ──────────────────────────────${NC}"

        case "$_dep" in

        "docker")
            echo -e "  ${WHITE}Binary location:${NC}"
            command -v docker 2>/dev/null && ls -la "$(command -v docker)" 2>/dev/null \
                || echo "  docker binary not found in PATH"
            echo -e "  ${WHITE}pacman package:${NC}"
            if pacman -Q docker 2>/dev/null; then
                pacman -Ql docker 2>/dev/null | grep "bin/" || true
                pacman -Qkk docker 2>/dev/null || echo "    (integrity check unavailable)"
            else
                echo "  docker package not in pacman database"
            fi
            echo -e "  ${WHITE}Docker daemon status:${NC}"
            sudo systemctl status docker 2>/dev/null | head -8 || echo "  (systemctl unavailable)"
            echo -e "  ${WHITE}Docker socket:${NC}"
            ls -la /var/run/docker.sock 2>/dev/null || echo "  /var/run/docker.sock not found"
            ;;

        "docker compose")
            echo -e "  ${WHITE}Raw command output:${NC}"
            docker compose version 2>&1 || true
            sudo docker compose version 2>&1 || true
            echo -e "  ${WHITE}Compose plugin locations:${NC}"
            local _effective_cfg="${DOCKER_CONFIG:-$HOME/.docker}"
            for _dir in \
                "/usr/lib/docker/cli-plugins" \
                "/usr/local/lib/docker/cli-plugins" \
                "/usr/libexec/docker/cli-plugins" \
                "/usr/local/libexec/docker/cli-plugins" \
                "${_effective_cfg}/cli-plugins" \
                "/root/.docker/cli-plugins"; do
                if [[ -f "$_dir/docker-compose" ]]; then
                    echo -e "  ${GREEN}FOUND:${NC} $(ls -la "$_dir/docker-compose" 2>/dev/null)"
                else
                    echo -e "  ${DIM}ABSENT: $_dir/docker-compose${NC}"
                fi
            done
            echo -e "  ${WHITE}pacman package:${NC}"
            pacman -Q docker-compose 2>/dev/null \
                && pacman -Ql docker-compose 2>/dev/null | grep "bin\|plugins" \
                || echo "  docker-compose package not in pacman database"
            ;;

        "docker buildx")
            echo -e "  ${WHITE}Raw command output:${NC}"
            docker buildx version 2>&1 || true
            echo -e "  ${WHITE}Plugin binary locations:${NC}"
            local _effective_cfg="${DOCKER_CONFIG:-$HOME/.docker}"
            for _dir in \
                "/usr/lib/docker/cli-plugins" \
                "/usr/local/lib/docker/cli-plugins" \
                "/usr/libexec/docker/cli-plugins" \
                "/usr/local/libexec/docker/cli-plugins" \
                "${_effective_cfg}/cli-plugins" \
                "/root/.docker/cli-plugins"; do
                if [[ -f "$_dir/docker-buildx" ]]; then
                    echo -e "  ${GREEN}FOUND:${NC} $(ls -la "$_dir/docker-buildx" 2>/dev/null)"
                else
                    echo -e "  ${DIM}ABSENT: $_dir/docker-buildx${NC}"
                fi
            done

            # ── Shadow binary check ──────────────────────────────────────
            # Docker CLI scans ~/.docker/cli-plugins BEFORE /usr/lib/docker/cli-plugins.
            # A corrupt/small file at the user path silently blocks the system binary.
            local _bx_sys="/usr/lib/docker/cli-plugins/docker-buildx"
            local _bx_usr="${_effective_cfg}/cli-plugins/docker-buildx"
            if [[ -f "$_bx_sys" && -f "$_bx_usr" ]]; then
                local _bx_sys_sz _bx_usr_sz
                _bx_sys_sz=$(stat -c%s "$_bx_sys" 2>/dev/null || echo 0)
                _bx_usr_sz=$(stat -c%s "$_bx_usr" 2>/dev/null || echo 0)
                echo -e "  ${WHITE}Shadow binary check:${NC}"
                echo -e "    System binary:    ${_bx_sys_sz} bytes  (${_bx_sys})"
                echo -e "    User-level copy:  ${_bx_usr_sz} bytes  (${_bx_usr})"
                if (( _bx_usr_sz < 10485760 )); then
                    # Also try to execute it — a corrupt file (HTML, partial) will fail with exec format error
                    local _bx_exec_ok=false
                    "$_bx_usr" docker-cli-plugin-metadata &>/dev/null 2>&1 && _bx_exec_ok=true
                    if [[ "$_bx_exec_ok" == "false" ]]; then
                        echo -e "  ${RED}⚠ LIKELY SHADOW BUG:${NC} user-level binary is only ${_bx_usr_sz} bytes and fails to execute"
                        echo -e "  ${RED}  It may be a corrupt download (HTML error page, partial file) that shadows the system binary.${NC}"
                        echo -e "  ${YELLOW}  Fix: rm -f \"${_bx_usr}\"  then re-run this script.${NC}"
                    else
                        echo -e "  ${YELLOW}⚠ User-level binary is small (${_bx_usr_sz}B) but executes; may shadow system binary.${NC}"
                        echo -e "  ${YELLOW}  If buildx is still broken, try: rm -f \"${_bx_usr}\"  then re-run.${NC}"
                    fi
                elif (( _bx_usr_sz < _bx_sys_sz )); then
                    echo -e "  ${YELLOW}⚠ User-level binary (${_bx_usr_sz}B) is smaller than system binary (${_bx_sys_sz}B) and may shadow it.${NC}"
                    echo -e "  ${YELLOW}  If buildx is still broken, try: rm -f \"${_bx_usr}\"  then re-run.${NC}"
                else
                    echo -e "  ${GREEN}OK:${NC} user-level and system binaries look comparable in size."
                fi
            fi

            echo -e "  ${WHITE}pacman package status:${NC}"
            if pacman -Q docker-buildx 2>/dev/null; then
                echo -e "  ${WHITE}Files owned by package:${NC}"
                pacman -Ql docker-buildx 2>/dev/null | while read -r _p _f; do echo "    $_f"; done
                echo -e "  ${WHITE}Integrity check:${NC}"
                pacman -Qkk docker-buildx 2>/dev/null || echo "    (integrity check unavailable)"
            else
                echo "  docker-buildx package not in pacman database"
            fi
            echo -e "  ${WHITE}docker info plugin entries:${NC}"
            docker info 2>/dev/null | grep -i "plugin\|buildx\|cli" \
                || echo "  (docker info unavailable or no plugin entries)"
            ;;

        "git")
            echo -e "  ${WHITE}Binary:${NC}"
            command -v git 2>/dev/null || echo "  git not found in PATH"
            echo -e "  ${WHITE}pacman package:${NC}"
            pacman -Q git 2>/dev/null \
                && pacman -Qkk git 2>/dev/null \
                || echo "  git not in pacman database"
            ;;

        "curl")
            echo -e "  ${WHITE}Binary:${NC}"
            command -v curl 2>/dev/null || echo "  curl not found in PATH"
            echo -e "  ${WHITE}pacman package:${NC}"
            pacman -Q curl 2>/dev/null \
                && pacman -Qkk curl 2>/dev/null \
                || echo "  curl not in pacman database"
            ;;

        *)
            echo -e "  ${WHITE}Binary search:${NC}"
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

install_docker() {
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        # Docker is running — still ensure buildx is present
        install_buildx
        print_success "Docker already installed and running"
        return 0
    fi

    print_info "Installing Docker..."

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable
    fi

    # Check keyring health — prompt before any reset
    check_pacman_keyring

    # Enable dev mode if available
    if command -v steamos-devmode &>/dev/null; then
        sudo steamos-devmode enable 2>/dev/null || \
            print_warning "steamos-devmode failed — continuing anyway"
    fi

    # Update keyring package before installing anything else
    print_info "Updating archlinux-keyring..."
    if ! sudo pacman -Sy --noconfirm archlinux-keyring; then
        print_warning "archlinux-keyring update failed — Docker install may fail."
    fi

    # Install Docker — this must succeed
    if ! sudo pacman -Sy --noconfirm docker docker-compose docker-buildx; then
        print_error "Failed to install Docker. Check your internet connection and keyring."
        sudo steamos-readonly enable 2>/dev/null || true
        exit 1
    fi

    sudo steamos-readonly enable 2>/dev/null || true
    sudo usermod -aG docker "$USER"
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

    # Add passwordless sudo for docker so it works immediately
    # without requiring logout — fixes "permission denied" on docker socket
    print_info "Setting up Docker permissions..."
    echo "deck ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose" | \
        sudo tee /etc/sudoers.d/docker-nopasswd > /dev/null 2>&1 || true
    sudo chmod 0440 /etc/sudoers.d/docker-nopasswd 2>/dev/null || true

    # Also fix docker socket permissions directly
    sudo chmod 666 /var/run/docker.sock 2>/dev/null || true

    # If docker still not accessible without sudo — wrap it
    if ! docker ps &>/dev/null 2>&1; then
        if sudo docker ps &>/dev/null 2>&1; then
            DOCKER_CMD="sudo docker"
            print_info "Using sudo for Docker — will work normally after next login"
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

    if sudo pacman -Sy --noconfirm git; then
        print_success "Git installed!"
    elif sudo apt-get install -y git; then
        print_success "Git installed!"
    else
        print_error "Git installation failed. Check your internet connection and try again."
        exit 1
    fi
}

# ─────────────────────────────────────────
# PREFLIGHT CHECK — SYSTEM DEPENDENCIES
# ─────────────────────────────────────────
preflight_check() {
    print_step "Preflight Check — System Dependencies"

    local docker_ok=false docker_compose_ok=false docker_buildx_ok=false
    local git_ok=false curl_ok=false all_ok=true

    # ── docker daemon ────────────────────────────────────────────────
    # Require unprivileged access — install_docker handles permission setup
    # when the daemon is running but the user isn't in the docker group yet.
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        docker_ok=true
    elif command -v docker &>/dev/null && sudo docker ps &>/dev/null 2>&1; then
        # Daemon is up but user lacks socket permission — set DOCKER_CMD now
        # so all subsequent checks in this preflight use sudo docker.
        DOCKER_CMD="sudo docker"
        docker_ok=true
    else
        all_ok=false
    fi

    # ── docker compose plugin ────────────────────────────────────────
    # Only accept the plugin subcommand (`docker compose`); the legacy
    # standalone `docker-compose` binary is never used by this script.
    if ${DOCKER_CMD:-docker} compose version &>/dev/null 2>&1; then
        docker_compose_ok=true
    else
        all_ok=false
    fi

    # ── docker buildx ────────────────────────────────────────────────
    # buildx is a client-side plugin — check without sudo regardless of DOCKER_CMD.
    if docker buildx version &>/dev/null 2>&1; then
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

    # ── Install curl if needed (pacman) ──────────────────────────────
    if [[ "$curl_ok" == "false" ]]; then
        print_info "Installing curl..."
        if command -v steamos-readonly &>/dev/null; then sudo steamos-readonly disable; fi
        local curl_installed=false
        if sudo pacman -Sy --noconfirm curl 2>/dev/null; then
            curl_installed=true
        elif sudo apt-get install -y curl 2>/dev/null; then
            curl_installed=true
        fi
        if command -v steamos-readonly &>/dev/null; then
            sudo steamos-readonly enable 2>/dev/null || true
        fi
        if [[ "$curl_installed" == "true" ]]; then
            print_success "curl installed!"
        else
            print_error "Failed to install curl. Check your internet connection and try again."
            exit 1
        fi
    fi

    # ── Re-verify after install ──────────────────────────────────────
    # buildx is a client-side plugin — its availability is independent of
    # socket permissions (DOCKER_CMD). Always check with plain `docker`.
    if ! docker buildx version &>/dev/null 2>&1; then
        print_info "buildx not yet visible — attempting targeted install..."
        install_buildx
        # Give the shell one more moment to see the newly installed binary
        hash -r 2>/dev/null || true
        sleep 1
    fi

    print_info "Verifying all dependencies are now available..."
    echo ""

    local failed=()

    # ── docker binary ────────────────────────────────────────────────
    if command -v docker &>/dev/null; then
        print_success "docker binary:    $(command -v docker)"
    else
        print_error  "docker binary:    NOT FOUND"
        failed+=("docker")
    fi

    # ── docker compose ───────────────────────────────────────────────
    local _compose_ver
    if _compose_ver=$(${DOCKER_CMD:-docker} compose version 2>&1); then
        print_success "docker compose:   $(echo "$_compose_ver" | head -1)"
    else
        print_error  "docker compose:   NOT AVAILABLE"
        print_info   "  Raw output: $_compose_ver"
        failed+=("docker compose")
    fi

    # ── docker buildx ────────────────────────────────────────────────
    # Client-side check — must never run via DOCKER_CMD/sudo.
    local _buildx_ver
    if _buildx_ver=$(docker buildx version 2>&1); then
        print_success "docker buildx:    $_buildx_ver"
    else
        print_error  "docker buildx:    NOT AVAILABLE"
        print_info   "  Raw output: $_buildx_ver"
        failed+=("docker buildx")
    fi

    # ── git ──────────────────────────────────────────────────────────
    if command -v git &>/dev/null; then
        print_success "git:              $(git --version 2>/dev/null)"
    else
        print_error  "git:              NOT FOUND"
        failed+=("git")
    fi

    # ── curl ─────────────────────────────────────────────────────────
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
    echo -e "  This will take 2-4 hours on your Steam Deck."
    echo -e "  Keep it plugged in and on a hard flat surface."
    echo -e "  The fan will be loud. That's normal."
    echo ""

    if ! ask_yes_no "Ready to build your Playerbots server?"; then
        echo ""
        echo -e "${WHITE}No problem! Run this script again when you're ready.${NC}"
        exit 0
    fi
}

# ─────────────────────────────────────────
# DML START/RESTART HOOK
# ─────────────────────────────────────────
install_dml_start_hook() {
    print_info "Installing DML staged start/restart hook..."

    local src dest="$SERVER_DIR/dml-start.sh"
    src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dml-start.sh"

    if [ -f "$src" ]; then
        cp "$src" "$dest"
    elif curl -fsSL \
        "https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/dml-start.sh" \
        -o "$dest"; then
        :
    else
        print_warning "Could not install dml-start.sh"
        print_info "Restarts via 'dml restart' may re-import the DB until this file is present."
        return 1
    fi

    chmod +x "$dest"
    print_success "DML restart hook installed: $dest"
    print_info "On DML Windows: dml restart wow-server-playerbots"
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
        print_info "  sudo rm -rf $SERVER_DIR"
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
    print_info "Keep your Steam Deck plugged in!"

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
        print_warning "Clone failed — retrying in 10 seconds..."
        sleep 10
        rm -rf "$SERVER_DIR/modules/mod-playerbots"
        if git clone --depth 1 \
            https://github.com/mod-playerbots/mod-playerbots.git \
            --branch=master \
            "$SERVER_DIR/modules/mod-playerbots"; then
            print_success "mod-playerbots module cloned!"
        else
            print_warning "mod-playerbots clone failed after retry. The server will still build but bots may be limited."
        fi
    fi

    # AzerothCore's compose file resolves ${DOCKER_DB_ROOT_PASSWORD:-password}
    # and ${DOCKER_DB_EXTERNAL_PORT:-3306}:3306. Without a .env both defaults
    # apply, so MySQL is published on every interface with the root password
    # "password". The port value is substituted into compose short syntax, so
    # "127.0.0.1:3306" becomes "127.0.0.1:3306:3306" and binds loopback only.
    #
    # This has to be written before the first `up`: the MySQL image only reads
    # MYSQL_ROOT_PASSWORD when it initialises the data volume.
    #
    # The game ports (3724 auth, 8085 world) are deliberately left alone.
    if [ ! -f "$SERVER_DIR/.env" ]; then
        umask 077
        cat > "$SERVER_DIR/.env" << ENVEOF
DOCKER_DB_ROOT_PASSWORD=$(openssl rand -hex 24)
DOCKER_DB_EXTERNAL_PORT=127.0.0.1:3306
DOCKER_SOAP_EXTERNAL_PORT=127.0.0.1:7878
ENVEOF
        umask 022
        chmod 600 "$SERVER_DIR/.env"
        print_success "Database secured (random root password, bound to localhost)"
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
            if docker logs "$WORLD_CONTAINER" \
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
        print_info "Check progress: docker logs -f $WORLD_CONTAINER"
        print_info "Continuing to account setup — wait for 'ready...' in the server logs before running account commands."
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
    echo -e "${WHITE}Now create your account. Open a NEW Konsole window${NC}"
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
    print_step "STEP 4/4 — Setting Up Gaming Mode"

    local launcher_path="$HOME/wow-playerbots-launcher.sh"
    local server_dir="$SERVER_DIR"

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
        if docker logs "\$WORLD_CONTAINER" 2>/dev/null | grep -q "ready\.\.\."; then
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
echo -e "  ${WHITE}${BOLD}Press STEAM button and launch WoW${NC}"
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
    print_success "Gaming Mode launcher created: ~/wow-playerbots-launcher.sh"

    # Save server info
    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — WoW Playerbots
  AzerothCore WotLK + Playerbots
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realmlist: 127.0.0.1
  Account:   create via mangosd console (see below)

LAUNCHER:
  Path: ~/wow-playerbots-launcher.sh
  Add to Steam:
    Target:  /usr/bin/konsole
    Options: --hold -e bash ~/wow-playerbots-launcher.sh
    Proton:  OFF (launcher needs no Proton)

REALMLIST (in your WoW client folder):
  Edit:  realmlist.wtf
  Set to: set realmlist 127.0.0.1

USEFUL COMMANDS (DML Windows/WSL):
  Start:   dml start wow-server-playerbots
  Restart: dml restart wow-server-playerbots
  Stop:    dml stop wow-server-playerbots
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

# ─────────────────────────────────────────
# COMPLETION
# ─────────────────────────────────────────
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
    echo -e "${WHITE}${BOLD} STEP B — Add to Steam Gaming Mode${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  Your Gaming Mode launcher was created here:"
    echo ""
    echo -e "  ${GREEN}${BOLD}~/wow-playerbots-launcher.sh${NC}"
    echo ""
    echo -e "  Add it to Steam:"
    echo -e "  1. Open Steam in Desktop Mode"
    echo -e "  2. Click ${CYAN}Games${NC} → ${CYAN}Add a Non-Steam Game${NC}"
    echo -e "  3. Click ${CYAN}Browse${NC} → navigate to ${CYAN}/usr/bin/${NC}"
    echo -e "  4. Select ${CYAN}konsole${NC} → click ${CYAN}Add Selected Programs${NC}"
    echo -e "  5. Find ${CYAN}konsole${NC} in your library"
    echo -e "  6. Right-click → ${CYAN}Properties${NC}"
    echo -e "  7. Rename it to: ${GREEN}WoW Playerbots Server${NC}"
    echo -e "  8. Set Launch Options to exactly:"
    echo ""
    echo -e "  ${GREEN}--hold -e bash ~/wow-playerbots-launcher.sh${NC}"
    echo ""
    echo -e "  9. Under Compatibility — ${RED}do NOT enable Proton${NC}"
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP C — Play!${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Switch to Gaming Mode"
    echo -e "  2. Launch ${CYAN}WoW Playerbots Server${NC} from your library"
    echo -e "  3. Watch the dots... wait for ${GREEN}AZEROTH IS READY!${NC}"
    echo -e "  4. Press Steam button → launch WoW"
    echo -e "  5. Login with the account you created"
    echo -e "  6. Play! Bots populate within 5-10 min — be patient!"
    echo -e "  7. Close WoW → server shuts down automatically ✅"
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
    echo -e "${YELLOW}  Or just use the Gaming Mode launcher next time.${NC}"
    echo ""
    if ask_yes_no "Would you like to stop the server now?"; then
        print_info "Stopping server..."
        cd "$SERVER_DIR" && docker compose down
        print_success "Server stopped! Use the Gaming Mode launcher to start it next time."
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
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null; exit" EXIT INT TERM HUP

preflight_check
choose_install_dir
show_summary
install_server
install_dml_start_hook
wait_for_server
create_accounts
setup_gaming_mode
show_completion
post_install_resources
