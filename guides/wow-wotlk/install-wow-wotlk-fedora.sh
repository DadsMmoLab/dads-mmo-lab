#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Playerbots Server Installer
#  AzerothCore WotLK + Playerbots (compiled from source)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.3.6 - Fedora
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
#    1.3.6 — Preflight dependency check
#      - Added preflight_check(): inspects docker daemon, docker compose,
#        docker buildx, git, and curl before the install begins
#      - Prints a visual status table (✅/❌) for each dependency
#      - Auto-installs missing deps via dnf (standard Fedora) or
#        rpm-ostree (immutable/Bazzite); respects FEDORA_IMMUTABLE
#      - Re-verifies all deps after install; exits with clear error if any fail
#    1.3.5 — docker.socket failed-state recovery (Bazzite)
#      - Root cause: on Bazzite, docker.socket can be left in a failed state
#        from a prior run; docker.service then fails with "dependency failed"
#        even after restart because the socket unit is still stuck.
#      - Fix: call `systemctl reset-failed containerd docker docker.socket`
#        before the enable attempts so stale failed state is cleared first.
#      - Start docker.socket explicitly (enable --now docker.socket) before
#        docker.service — the socket unit must be active for the service to start.
#      - Improved diagnostics in the failure path: show docker.socket status,
#        containerd status, and combined journal for all three units.
#      - Updated recommended fix commands to the correct 3-step sequence
#        (reset-failed → containerd → socket → service) instead of restart.
#    1.3.4 — containerd dependency + service failure diagnosis
#      - Start containerd.service before docker.service — docker CE's unit file
#        has Requires=containerd.service; starting docker without containerd
#        caused silent startup failure (|| true swallowed the error).
#      - When docker binary exists and packages are layered but daemon won't
#        start, the script was falling through to rpm-ostree install which
#        failed with "No packages in transaction" (already layered). Now:
#        show `systemctl status docker` + `journalctl -u docker` and exit
#        with a clear actionable error message instead.
#      - Added --idempotent to the first-time rpm-ostree CE install.
#      - Separated docker-ps check from compose check in the immutable block.
#      - Extended readiness polling from 10 to 15 iterations (30 seconds).
#    1.3.3 — Session permissions + first-install fixes (from Bazzite doc review)
#      - CRITICAL: After the Bazzite early-return path, all docker compose calls
#        in the rest of the script failed with permission denied because the
#        sudoers entry and function wrapper were never set up. Fixed by moving
#        the same sudoers+wrapper block (used in the plain Fedora path) into
#        the immutable early-return block before return 0.
#      - Replace sleep 3 with a polling loop using `sudo docker info` — the
#        canonical daemon readiness probe (tests the API, not just the socket).
#      - Add podman-docker shim detection: if /usr/bin/docker is a symlink to
#        podman, exit with a clear error rather than silently failing later.
#      - Fix rpm-ostree first-time install fallback: add Docker CE repo first,
#        use correct package names (docker-ce, docker-ce-cli, containerd.io,
#        docker-buildx-plugin, docker-compose-plugin) instead of the generic
#        `docker docker-compose` which resolves to moby-engine and conflicts.
#      - Add sudo fallback to the secondary docker ps check so immutable systems
#        with a running daemon don't fall through to the install path just
#        because the user isn't in the docker group yet.
#      - Fix misleading comment: docker-ce in @System is a layered package from
#        a prior run, not a Bazzite base image package.
#    1.3.2 — Bazzite docker group / sudo fix
#      - Bazzite ships docker-ce (not moby-engine) in @System. After
#        systemctl enable --now docker, the daemon is running but the user
#        is not in the docker group yet, so unprivileged `docker ps` returns
#        permission denied. Script fell through to rpm-ostree install which
#        conflicted with @System packages. Fix: use `sudo docker ps` and
#        `sudo docker compose version` for the immutable early-out check.
#        If both pass, add user to docker group and return 0 — no
#        rpm-ostree install attempted.
#    1.3.1 — Bazzite pre-bundled Docker fix
#      - On immutable systems, Docker is part of the Bazzite base image and
#        is not a layered package. The daemon just isn't started yet. The
#        previous check (docker ps) required the daemon to be running, so it
#        fell through to rpm-ostree install, which fatally conflicted with
#        @System. Fix: on immutable systems, try systemctl enable --now docker
#        first if the binary exists, then re-check before attempting any
#        rpm-ostree install.
#      - Added --idempotent flag to rpm-ostree install calls to avoid
#        conflicts when packages are already provided by the base image.
#      - Compose plugin missing fallback now correctly uses rpm-ostree on
#        immutable systems instead of dnf.
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

WIZARD_VERSION="1.3.6 - Fedora"

set -o pipefail

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
# Terminal detection — set globally so setup_gaming_mode and show_completion share state
TERM_BIN=""
TERM_ARGS=""

# ─────────────────────────────────────────
# SYSTEM CHECKS
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System Requirements"

    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script supports Fedora-based Linux only (Fedora, Bazzite)."
        exit 1
    fi
    print_success "Linux detected"

    # Verify this is a Fedora-family distro
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        if [[ "$ID" != "fedora" && "$ID_LIKE" != *"fedora"* && "$ID" != "bazzite" ]]; then
            print_error "Unsupported distro: $PRETTY_NAME"
            print_info "This script is for Fedora or Fedora-based distros (e.g., Bazzite)."
            exit 1
        fi
        print_success "Fedora-family distro detected: ${PRETTY_NAME:-$ID}"
    else
        print_warning "Could not read /etc/os-release — proceeding at your own risk."
    fi

    # Detect immutable/Bazzite (rpm-ostree)
    if command -v rpm-ostree &>/dev/null; then
        FEDORA_IMMUTABLE=true
        print_info "Immutable Fedora (Bazzite / rpm-ostree) detected."
    else
        FEDORA_IMMUTABLE=false
    fi

    # ── Confirm detected package manager path with user ───────────────
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} Detected System Type${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    if [[ "$FEDORA_IMMUTABLE" == "true" ]]; then
        echo -e "  ${GREEN}✅ Bazzite / Immutable Fedora${NC}"
        echo -e "  ${WHITE}Package manager: ${CYAN}rpm-ostree${NC}"
        echo -e "  ${DIM}Docker will be started if already present, or layered via rpm-ostree${NC}"
    else
        echo -e "  ${GREEN}✅ Standard Fedora${NC}"
        echo -e "  ${WHITE}Package manager: ${CYAN}dnf${NC}"
        echo -e "  ${DIM}Docker will be installed via dnf + Docker CE repo${NC}"
    fi
    echo ""
    echo -e "  ${YELLOW}Is this correct?${NC}"
    echo -e "  ${DIM}(If wrong, press Ctrl+C to exit and check your distro)${NC}"
    echo ""
    if ! ask_yes_no "Continue with the detected system type?"; then
        echo ""
        print_error "Aborted. Re-run once you've confirmed your distro."
        print_info "Expected: Fedora (dnf) or Bazzite/Immutable Fedora (rpm-ostree)"
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
# INSTALL DOCKER
# ─────────────────────────────────────────
install_docker() {
    # ── On immutable systems (Bazzite), Docker may already be present as a
    #    layered package from a prior install attempt. If the binary exists,
    #    try to start the daemon before attempting any rpm-ostree install —
    #    reinstalling packages that are already in @System will fail.
    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]] && command -v docker &>/dev/null; then
        # Guard against the podman-docker shim
        if [[ -L /usr/bin/docker ]] && readlink /usr/bin/docker 2>/dev/null | grep -q podman; then
            print_error "podman-docker shim detected at /usr/bin/docker. This script requires real Docker CE."
            print_info "Remove podman-docker and install Docker CE first, then re-run."
            exit 1
        fi

        print_info "Docker binary found on immutable system — enabling and starting service..."
        # Reset any stale failed state — a failed docker.socket will block a fresh
        # start even after the root cause is resolved (shows as "dependency failed").
        sudo systemctl reset-failed containerd docker docker.socket 2>/dev/null || true
        # containerd must start first — docker.service Requires=containerd.service
        sudo systemctl enable --now containerd 2>/dev/null || true
        sleep 2
        # Verify containerd is actually active before attempting docker
        if ! sudo systemctl is-active --quiet containerd 2>/dev/null; then
            print_warning "containerd did not start — will diagnose below if docker also fails."
        fi
        # Start docker.socket explicitly before docker.service — on Bazzite the
        # socket unit can be stuck in a failed state, which cascades to the service.
        sudo systemctl enable --now docker.socket 2>/dev/null || true
        sleep 1
        sudo systemctl enable --now docker 2>/dev/null || true

        # Poll for daemon readiness — docker info tests the API, not just the socket
        for i in {1..15}; do sudo docker info &>/dev/null && break; sleep 2; done

        if sudo docker ps &>/dev/null 2>&1; then
            # Daemon is up — check compose separately so we can handle each case
            if sudo docker compose version &>/dev/null 2>&1; then
                print_success "Docker is running on this immutable system."
                sudo usermod -aG docker "$USER" 2>/dev/null || true

                # ── Session fix: group change won't take effect until next login.
                #    Set up passwordless sudo for docker so the rest of this install
                #    session works transparently. User can remove the file after
                #    their first logout:  sudo rm /etc/sudoers.d/docker-nopasswd
                print_info "Setting up Docker permissions for this session..."
                echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/docker" | \
                    sudo tee /etc/sudoers.d/docker-nopasswd > /dev/null 2>&1 || true
                sudo chmod 0440 /etc/sudoers.d/docker-nopasswd 2>/dev/null || true
                if ! docker ps &>/dev/null 2>&1; then
                    function docker() { sudo docker "$@"; }
                    export -f docker 2>/dev/null || true
                    print_info "Using sudo for Docker this session — works normally after next login"
                fi

                print_success "Docker permissions configured!"
                return 0
            else
                # Daemon runs but compose plugin is missing — layer it and reboot
                print_warning "Docker is running but the Compose plugin is missing."
                print_info "Layering docker-compose-plugin via rpm-ostree..."
                sudo rpm-ostree install -y --idempotent docker-compose-plugin 2>/dev/null || \
                sudo rpm-ostree install -y --idempotent docker-compose 2>/dev/null || {
                    print_error "Could not install docker-compose-plugin via rpm-ostree."
                    exit 1
                }
                print_success "docker-compose-plugin layered. Rebooting in 10 seconds — re-run this script after reboot."
                sleep 10
                sudo systemctl reboot
                exit 0
            fi
        else
            # Docker binary and packages are present but daemon won't start.
            # Show the actual error — do NOT fall through to reinstall, it will
            # fail with "No packages in transaction" since everything is already layered.
            print_error "Docker is installed but the service failed to start."
            echo ""
            echo -e "${YELLOW}  Docker service status:${NC}"
            sudo systemctl status docker --no-pager -l 2>&1 | head -20
            echo ""
            echo -e "${YELLOW}  docker.socket status:${NC}"
            sudo systemctl status docker.socket --no-pager -l 2>&1 | head -10
            echo ""
            echo -e "${YELLOW}  containerd status:${NC}"
            sudo systemctl status containerd --no-pager -l 2>&1 | head -10
            echo ""
            echo -e "${YELLOW}  Recent Docker logs:${NC}"
            sudo journalctl -u docker -u docker.socket -u containerd --no-pager -n 30 2>&1
            echo ""
            print_info "Common fixes:"
            print_info "  Step 1 — reset stale failed-unit state:"
            print_info "    sudo systemctl reset-failed containerd docker docker.socket"
            print_info "  Step 2 — start in order (containerd → socket → service):"
            print_info "    sudo systemctl enable --now containerd"
            print_info "    sudo systemctl enable --now docker.socket"
            print_info "    sudo systemctl start docker"
            print_info "  If SELinux is blocking: sudo setenforce 0 (temporary) or check audit log"
            print_info "  Then re-run this script."
            print_error "Fix the Docker service issue above, then re-run this script."
            exit 1
        fi
    fi

    # ── Check for a Docker + Compose setup that is already running ────────
    # On immutable systems also try sudo in case user isn't in docker group yet.
    if command -v docker &>/dev/null && \
       (docker ps &>/dev/null 2>&1 || { [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]] && sudo docker ps &>/dev/null 2>&1; }); then
        if docker compose version &>/dev/null 2>&1; then
            print_success "Docker (with Compose plugin) already installed and running"
            return 0
        else
            print_warning "Docker is running but the Compose plugin is missing."
            print_info "Attempting to install docker-compose-plugin..."
            if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
                if sudo rpm-ostree install -y --idempotent docker-compose-plugin 2>/dev/null || \
                   sudo rpm-ostree install -y --idempotent docker-compose 2>/dev/null; then
                    print_success "docker-compose-plugin layered. Rebooting in 10 seconds — re-run this script after reboot."
                    sleep 10
                    sudo systemctl reboot
                    exit 0
                else
                    print_error "Could not install docker-compose-plugin via rpm-ostree."
                    exit 1
                fi
            else
                if sudo dnf -y install docker-compose-plugin; then
                    print_success "docker-compose-plugin installed!"
                    return 0
                else
                    print_error "Could not install docker-compose-plugin. Check your Docker CE repo setup."
                    exit 1
                fi
            fi
        fi
    fi

    # ── First-time install (no docker binary found) ───────────────────────
    print_info "Installing Docker..."

    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
        # ── Bazzite / immutable Fedora path (rpm-ostree) ─────────────────
        # Only reached if docker binary was not found at all (truly first install).
        # Must add the Docker CE repo first, then layer the correct packages.
        print_info "Immutable system detected — installing Docker CE via rpm-ostree..."
        print_warning "This will require a REBOOT to take effect."
        echo ""
        echo -e "${YELLOW}  rpm-ostree will layer Docker onto your system image.${NC}"
        echo -e "${YELLOW}  After installation you MUST reboot, then re-run this script.${NC}"
        echo ""
        if ! ask_yes_no "Install Docker via rpm-ostree and reboot now?"; then
            print_info "Skipped. Re-run after manually installing Docker."
            exit 0
        fi

        print_info "Adding Docker CE repository..."
        if ! sudo bash -c 'curl -fsSL https://download.docker.com/linux/fedora/docker-ce.repo \
                -o /etc/yum.repos.d/docker-ce.repo'; then
            print_error "Failed to download Docker CE repo. Check your internet connection."
            exit 1
        fi

        # --idempotent: succeeds cleanly if packages are already layered
        if ! sudo rpm-ostree install -y --idempotent \
                docker-ce docker-ce-cli containerd.io \
                docker-buildx-plugin docker-compose-plugin; then
            print_error "rpm-ostree Docker install failed. Check your connection and try again."
            exit 1
        fi

        print_success "Docker layered. Rebooting in 10 seconds — re-run this script after reboot."
        sleep 10
        sudo systemctl reboot
        exit 0
    else
        # ── Plain Fedora path (dnf) ───────────────────────────────────────
        # Remove conflicting packages (e.g. podman-docker, moby-engine) before installing CE
        print_info "Removing any conflicting Docker packages..."
        for pkg in docker docker-client docker-client-latest docker-common \
                   docker-latest docker-latest-logrotate docker-logrotate \
                   docker-selinux docker-engine-selinux docker-engine moby-engine; do
            sudo dnf -y remove "$pkg" 2>/dev/null || true
        done

        print_info "Installing dnf-plugins-core..."
        if ! sudo dnf -y install dnf-plugins-core; then
            print_error "Failed to install dnf-plugins-core."
            exit 1
        fi

        # Add Docker CE repo — use direct curl download so it works on both
        # dnf4 (Fedora ≤40) and dnf5 (Fedora 41+, where config-manager syntax changed)
        print_info "Adding Docker CE repository..."
        if ! sudo curl -fsSL \
                https://download.docker.com/linux/fedora/docker-ce.repo \
                -o /etc/yum.repos.d/docker-ce.repo; then
            print_error "Failed to download Docker CE repo file. Check your internet connection."
            exit 1
        fi

        print_info "Installing Docker CE..."
        if ! sudo dnf -y install \
                docker-ce docker-ce-cli containerd.io \
                docker-buildx-plugin docker-compose-plugin; then
            print_error "Failed to install Docker. Check your internet connection."
            exit 1
        fi
    fi

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
    echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/docker" | \
        sudo tee /etc/sudoers.d/docker-nopasswd > /dev/null 2>&1 || true
    sudo chmod 0440 /etc/sudoers.d/docker-nopasswd 2>/dev/null || true

    # If docker still not accessible without sudo — wrap it
    if ! docker ps &>/dev/null 2>&1; then
        if sudo docker ps &>/dev/null 2>&1; then
            function docker() { sudo docker "$@"; }
            export -f docker 2>/dev/null || true
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

    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
        if sudo rpm-ostree install -y git; then
            print_success "Git layered via rpm-ostree — reboot required before first use."
        else
            print_warning "Git installation failed — some features may not work."
            print_info "Try manually: sudo rpm-ostree install git"
        fi
    else
        if sudo dnf -y install git; then
            print_success "Git installed!"
        else
            print_warning "Git installation failed — some features may not work."
            print_info "Try manually: sudo dnf install -y git"
        fi
    fi
}

# ─────────────────────────────────────────
# PREFLIGHT CHECK — SYSTEM DEPENDENCIES
# ─────────────────────────────────────────
preflight_check() {
    print_step "Preflight Check — System Dependencies"

    local docker_ok=false docker_compose_ok=false docker_buildx_ok=false
    local git_ok=false curl_ok=false all_ok=true _pf_reboot_needed=false

    # ── docker daemon ────────────────────────────────────────────────
    # Require unprivileged access — install_docker handles permission setup
    # when the daemon is running but the user isn't in the docker group yet.
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        docker_ok=true
    else
        all_ok=false
    fi

    # ── docker compose plugin ────────────────────────────────────────
    # Only accept the plugin subcommand (`docker compose`); the legacy
    # standalone `docker-compose` binary is never used by this script.
    if docker compose version &>/dev/null 2>&1; then
        docker_compose_ok=true
    else
        all_ok=false
    fi

    # ── docker buildx ────────────────────────────────────────────────
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
        # On immutable systems, rpm-ostree layers the package into the next
        # deployment — git won't be on $PATH until after a reboot.
        if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]] && \
           ! command -v git &>/dev/null 2>&1; then
            _pf_reboot_needed=true
        fi
    fi

    # ── Install curl if needed (dnf / rpm-ostree) ────────────────────
    if [[ "$curl_ok" == "false" ]]; then
        print_info "Installing curl..."
        if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
            if ! sudo rpm-ostree install -y --idempotent curl; then
                print_error "Failed to install curl. Run manually: sudo rpm-ostree install curl"
                exit 1
            fi
            _pf_reboot_needed=true
            print_success "curl layered via rpm-ostree."
        else
            if ! sudo dnf -y install curl; then
                print_error "Failed to install curl. Run manually: sudo dnf install -y curl"
                exit 1
            fi
            print_success "curl installed!"
        fi
    fi

    # ── Reboot if rpm-ostree changes are pending ─────────────────────
    # rpm-ostree layers packages into the next deployment; they are not
    # available on $PATH until after a reboot. Trigger one now — consistent
    # with how install_docker handles the compose plugin on immutable systems.
    if [[ "$_pf_reboot_needed" == "true" ]]; then
        echo ""
        print_warning "New packages were layered via rpm-ostree and require a reboot."
        print_info "Re-run this script after rebooting to continue the install."
        print_info "Rebooting in 10 seconds — Ctrl+C to cancel."
        sleep 10
        sudo systemctl reboot
        exit 0
    fi

    # ── Re-verify after install ──────────────────────────────────────
    print_info "Verifying all dependencies are now available..."
    local failed=()
    command -v docker &>/dev/null || failed+=("docker")
    docker compose version &>/dev/null 2>&1 || failed+=("docker compose")
    docker buildx version &>/dev/null 2>&1 || failed+=("docker buildx")
    command -v git &>/dev/null || failed+=("git")
    command -v curl &>/dev/null || failed+=("curl")

    if [[ ${#failed[@]} -gt 0 ]]; then
        print_error "The following dependencies could not be installed: ${failed[*]}"
        print_info "Install them manually and re-run this script."
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
      - ./modules:/azerothcore/modules:Z
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
    if command -v konsole &>/dev/null; then
        TERM_BIN="/usr/bin/konsole"
        TERM_ARGS="--hold -e bash ~/wow-playerbots-launcher.sh"
    elif command -v gnome-terminal &>/dev/null; then
        TERM_BIN="/usr/bin/gnome-terminal"
        TERM_ARGS="-- bash -c 'bash ~/wow-playerbots-launcher.sh; read -r'"
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
  Start:   cd ${SERVER_DIR} && docker compose up -d
  Stop:    cd ${SERVER_DIR} && docker compose down
  Logs:    cd ${SERVER_DIR} && docker compose logs -f
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
show_summary
install_server
wait_for_server
create_accounts
setup_gaming_mode
show_completion
