#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Master Uninstaller v1.0.0
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Usage:
#    chmod +x uninstall.sh
#    ./uninstall.sh
#
#  Menu-driven uninstaller for any or all games.
#  Removes servers, containers, volumes, and launchers.
#
#  ⚠️  Permanent. Characters and progress will be deleted.
# ============================================================

UNINSTALLER_VERSION="1.2.0"

RST='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; GRAY='\033[0;37m'
MAGENTA='\033[0;35m'; CYAN='\033[0;36m'

print_success() { echo -e "${GREEN}✅ $1${RST}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${RST}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${RST}"; }

# ─────────────────────────────────────────
# REQUIRE TYPING "YES" TO CONFIRM
# ─────────────────────────────────────────
confirm_delete() {
    local name="$1"
    echo ""
    echo -e "${RED}${BOLD}⚠️  THIS CANNOT BE UNDONE${RST}"
    echo ""
    echo -e "${WHITE}About to permanently delete: ${RED}${BOLD}$name${RST}"
    echo -e "${WHITE}Removes: server files, containers, volumes, characters.${RST}"
    echo ""
    echo -e "${YELLOW}Type ${WHITE}${BOLD}YES${RST}${YELLOW} to confirm, anything else to cancel:${RST}"
    printf "${WHITE}> ${RST}"
    read -r answer
    [ "$answer" = "YES" ] && return 0
    echo -e "${GREEN}Cancelled — nothing deleted.${RST}"
    return 1
}

# ─────────────────────────────────────────
# GENERIC UNINSTALL CORE
# ─────────────────────────────────────────
do_uninstall() {
    local server_dir="$1"
    shift
    local launchers=("$@")

    # Compose down with volumes (check both compose file conventions)
    if [ -f "$server_dir/compose.yml" ] || [ -f "$server_dir/docker-compose.yml" ]; then
        print_info "Stopping Docker stack..."
        (cd "$server_dir" && docker compose down -v 2>/dev/null) || true
    fi

    # Remove server directory
    if [ -d "$server_dir" ]; then
        sudo rm -rf "$server_dir"
        print_success "Removed: $server_dir"
    fi

    # Remove launchers
    for f in "${launchers[@]}"; do
        if [ -f "$f" ]; then
            rm -f "$f"
            print_success "Removed launcher: $(basename "$f")"
        fi
    done
}

# ─────────────────────────────────────────
# STATUS — shown in menu header
# ─────────────────────────────────────────
show_status() {
    local entries=(
        "WoW Playerbots:$HOME/wow-server-playerbots"
        "WoW Vanilla:$HOME/wow-vanilla-server"
        "WoW TBC:$HOME/wow-tbc-server"
        "Dark Age of Camelot:$HOME/daoc-server"
        "Ragnarok Online:$HOME/ro-server"
        "Monster Hunter Frontier Z:$HOME/mhf-server"
        "MapleStory v83:$HOME/maplestory-server"
        "EverQuest 1:$HOME/eq1-server"
        "Tibia:$HOME/tibia-server"
        "Lineage 2:$HOME/lineage2-server"
        "Final Fantasy XI:$HOME/ffxi-server"
        "Star Wars Galaxies:$HOME/swg-server"
        "Ultima Online:$HOME/uo-server"
        "RuneScape 2009:$HOME/runescape-server"
        "PSO Blue Burst:$HOME/pso-server"
        "MU Online:$HOME/muonline-server"
        "LEGO Universe:$HOME/lego-server"
    )
    local found=0
    for e in "${entries[@]}"; do
        local nm="${e%%:*}" dr="${e#*:}"
        if [ -d "$dr" ]; then
            printf "  ${GREEN}✅${RST} %s\n" "$nm"
            found=$((found + 1))
        else
            printf "  ${DIM}·  %s${RST}\n" "$nm"
        fi
    done
    echo ""
    if [ $found -gt 0 ]; then
        echo -e "  ${GREEN}${BOLD}$found game(s) installed${RST}"
    else
        echo -e "  ${YELLOW}No games installed${RST}"
    fi
}

# ─────────────────────────────────────────
# INDIVIDUAL GAME UNINSTALLERS
# ─────────────────────────────────────────

uninstall_wow_wotlk() {
    confirm_delete "WoW Playerbots (AzerothCore WotLK)" || return
    do_uninstall "$HOME/wow-server-playerbots" \
        "$HOME/wow-playerbots-launcher.sh"
    docker volume rm wow-server-playerbots_ac-database \
        wow-server-playerbots_client-data 2>/dev/null || true
    print_success "WoW Playerbots uninstalled!"
}

uninstall_wow_vanilla() {
    confirm_delete "WoW Classic — Vanilla 1.12 (CMaNGOS + Playerbots)" || return

    local server_dir="$HOME/wow-vanilla-server"

    # ── Stop and remove containers + their named volumes ──
    # Our installer creates compose.yml (not docker-compose.yml — the newer
    # convention). do_uninstall checks for docker-compose.yml, so we handle
    # the compose teardown explicitly here.
    if [ -f "$server_dir/compose.yml" ] || [ -f "$server_dir/docker-compose.yml" ]; then
        print_info "Stopping vanilla server stack..."
        (cd "$server_dir" && docker compose down -v 2>/dev/null) || true
    fi

    # ── Belt-and-suspenders: stop containers by name in case compose.yml is gone ──
    for container in vanilla-mangosd vanilla-realmd vanilla-db; do
        docker rm -f "$container" 2>/dev/null || true
    done

    # ── Remove the server folder ──
    if [ -d "$server_dir" ]; then
        # Some extracted files may be root-owned — need sudo to rm
        sudo rm -rf "$server_dir"
        print_success "Removed: $server_dir"
    fi

    # ── Remove the launcher ──
    if [ -f "$HOME/wow-vanilla-launcher.sh" ]; then
        rm -f "$HOME/wow-vanilla-launcher.sh"
        print_success "Removed launcher: wow-vanilla-launcher.sh"
    fi

    # ── Remove the local Docker image (~170MB) ──
    # This is OUR built image. We always remove it because reinstalling
    # rebuilds (Docker uses build cache so it's still fast).
    if docker image inspect dml/cmangos-vanilla-server:local >/dev/null 2>&1; then
        docker rmi dml/cmangos-vanilla-server:local 2>/dev/null || \
            print_warning "Couldn't remove dml/cmangos-vanilla-server:local image"
        print_success "Removed Docker image: dml/cmangos-vanilla-server:local"
    fi

    # ── Optionally prune dangling images from the compile ──
    # The multi-stage Dockerfile leaves a dangling builder image. Prune it.
    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
    if [ "$dangling" -gt 0 ]; then
        print_info "Pruning $dangling dangling Docker images from compile..."
        docker image prune -f >/dev/null 2>&1 || true
    fi

    print_success "WoW Vanilla uninstalled!"
}

uninstall_wow_tbc() {
    confirm_delete "WoW Classic — The Burning Crusade 2.4.3 (CMaNGOS + Playerbots)" || return

    local server_dir="$HOME/wow-tbc-server"

    if [ -f "$server_dir/compose.yml" ] || [ -f "$server_dir/docker-compose.yml" ]; then
        print_info "Stopping TBC server stack..."
        (cd "$server_dir" && docker compose down -v 2>/dev/null) || true
    fi

    for container in tbc-mangosd tbc-realmd tbc-db; do
        docker rm -f "$container" 2>/dev/null || true
    done

    # Explicit volume removal in case compose.yml is already gone
    docker volume rm wow-tbc-server_db-data 2>/dev/null || true

    if [ -d "$server_dir" ]; then
        sudo rm -rf "$server_dir"
        print_success "Removed: $server_dir"
    fi

    if [ -f "$HOME/wow-tbc-launcher.sh" ]; then
        rm -f "$HOME/wow-tbc-launcher.sh"
        print_success "Removed launcher: wow-tbc-launcher.sh"
    fi

    if docker image inspect dml/cmangos-tbc-server:local >/dev/null 2>&1; then
        docker rmi dml/cmangos-tbc-server:local 2>/dev/null || \
            print_warning "Couldn't remove dml/cmangos-tbc-server:local image"
        print_success "Removed Docker image: dml/cmangos-tbc-server:local"
    fi

    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
    if [ "$dangling" -gt 0 ]; then
        docker image prune -f >/dev/null 2>&1 || true
    fi

    print_success "WoW TBC uninstalled!"
}

uninstall_daoc() {
    confirm_delete "Dark Age of Camelot — OpenDAoC SinglePlayerBots" || return
    print_info "Stopping database..."
    if [ -f "$HOME/daoc-server/docker-compose.yml" ]; then
        cd "$HOME/daoc-server" && docker compose down -v 2>/dev/null || true
    fi
    docker rm -f daoc-db daoc-gameserver 2>/dev/null || true
    docker volume rm daoc-db-data 2>/dev/null || true
    rm -rf "$HOME/daoc-server" "$HOME/daoc-spbots"
    rm -f "$HOME/daoc-launcher.sh"
    print_success "Dark Age of Camelot uninstalled!"
}

uninstall_ro() {
    confirm_delete "Ragnarok Online — rAthena" || return
    do_uninstall "$HOME/ro-server" "$HOME/ro-launcher.sh"
    print_success "Ragnarok Online uninstalled!"
}

uninstall_mhf() {
    confirm_delete "Monster Hunter Frontier Z — Erupe" || return
    do_uninstall "$HOME/mhf-server" "$HOME/mhf-launcher.sh"
    # Remove /etc/hosts patch
    if grep -q "mhf.capcom.com.jp" /etc/hosts 2>/dev/null; then
        sudo sed -i '/mhf\.capcom\.com\.jp/d' /etc/hosts 2>/dev/null || true
        sudo sed -i '/mhfg\.capcom\.com\.jp/d' /etc/hosts 2>/dev/null || true
        print_success "/etc/hosts patch removed"
    fi
    print_success "Monster Hunter Frontier Z uninstalled!"
}

uninstall_maple() {
    confirm_delete "MapleStory v83 — Cosmic" || return
    do_uninstall "$HOME/maplestory-server" "$HOME/maplestory-launcher.sh"
    print_success "MapleStory uninstalled!"
}

uninstall_eq1() {
    confirm_delete "EverQuest 1 — EQEmu + AkkStack" || return
    if [ -d "$HOME/eq1-server" ]; then
        cd "$HOME/eq1-server"
        [ -f Makefile ] && make down 2>/dev/null || \
            docker compose down -v 2>/dev/null || true
    fi
    rm -rf "$HOME/eq1-server"
    rm -f "$HOME/eq1-launcher.sh"
    print_success "EverQuest 1 uninstalled!"
}

uninstall_tibia() {
    confirm_delete "Tibia — OpenTibiaBR Canary" || return
    do_uninstall "$HOME/tibia-server" "$HOME/tibia-launcher.sh"
    print_success "Tibia uninstalled!"
}

uninstall_l2() {
    confirm_delete "Lineage 2 — L2J Interlude" || return
    do_uninstall "$HOME/lineage2-server" "$HOME/lineage2-launcher.sh"
    print_success "Lineage 2 uninstalled!"
}

uninstall_ffxi() {
    confirm_delete "Final Fantasy XI — LandSandBoat" || return
    do_uninstall "$HOME/ffxi-server" "$HOME/ffxi-launcher.sh"
    docker volume rm ffxi-db-data 2>/dev/null || true
    print_success "Final Fantasy XI uninstalled!"
}

uninstall_swg() {
    confirm_delete "Star Wars Galaxies — SWGEmu Core3" || return
    do_uninstall "$HOME/swg-server" "$HOME/swg-launcher.sh"
    print_success "Star Wars Galaxies uninstalled!"
}

uninstall_uo() {
    confirm_delete "Ultima Online — ModernUO + ClassicUO" || return
    do_uninstall "$HOME/uo-server" "$HOME/uo-launcher.sh"
    docker volume rm uo-db-data uo-saves uo-logs 2>/dev/null || true
    # ClassicUO client dir lives outside server dir
    if [ -d "$HOME/ClassicUO" ]; then
        if confirm_delete "ClassicUO client folder ~/ClassicUO"; then
            rm -rf "$HOME/ClassicUO"
            print_success "Removed: ~/ClassicUO"
        fi
    fi
    print_success "Ultima Online uninstalled!"
}

uninstall_runescape() {
    confirm_delete "RuneScape 2009 — 2009scape" || return
    do_uninstall "$HOME/runescape-server" "$HOME/runescape-launcher.sh"
    if [ -d "$HOME/runescape-client" ]; then
        rm -rf "$HOME/runescape-client"
        print_success "Removed: ~/runescape-client"
    fi
    print_success "RuneScape 2009 uninstalled!"
}

uninstall_pso() {
    confirm_delete "Phantasy Star Online BB — newserv" || return
    # PSO uses a native process, not Docker
    pkill -f newserv 2>/dev/null || true
    if [ -d "$HOME/pso-server" ]; then
        rm -rf "$HOME/pso-server"
        print_success "Removed: ~/pso-server"
    fi
    rm -f "$HOME/pso-launcher.sh"
    print_success "PSO Blue Burst uninstalled!"
}

uninstall_muonline() {
    confirm_delete "MU Online — OpenMU" || return
    do_uninstall "$HOME/muonline-server" "$HOME/muonline-launcher.sh"
    docker volume rm mu-db-data 2>/dev/null || true
    print_success "MU Online uninstalled!"
}

uninstall_lego() {
    confirm_delete "LEGO Universe — Darkflame Universe" || return
    do_uninstall "$HOME/lego-server" "$HOME/lego-launcher.sh"
    docker volume rm lego-db-data lego-client 2>/dev/null || true
    print_success "LEGO Universe uninstalled!"
}

# ─────────────────────────────────────────
# CLEAN DOCKER ENVIRONMENT
# Removes podman/broken shims and installs real Docker.
# Pointed to by install_docker() when podman is detected.
# ─────────────────────────────────────────
clean_docker_environment() {
    clear
    echo ""
    echo -e "${YELLOW}${BOLD}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${YELLOW}${BOLD}║   🐳  CLEAN DOCKER ENVIRONMENT                   ║${RST}"
    echo -e "${YELLOW}${BOLD}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "${WHITE}Diagnoses and fixes Docker environment issues that${RST}"
    echo -e "${WHITE}cause installers to fail (podman shim, broken plugins).${RST}"
    echo ""

    local has_podman=0
    local has_broken_plugin=0
    local issues=0

    if command -v podman &>/dev/null; then
        has_podman=1
        issues=$((issues + 1))
        print_warning "podman is installed — its docker-compose shim breaks 'docker compose'"
    fi

    if [ -f "$HOME/.docker/cli-plugins/docker-compose" ] && \
       ! "$HOME/.docker/cli-plugins/docker-compose" version &>/dev/null; then
        has_broken_plugin=1
        issues=$((issues + 1))
        print_warning "Broken docker-compose plugin: ~/.docker/cli-plugins/docker-compose"
    fi

    if [ $issues -eq 0 ]; then
        print_success "Docker environment looks healthy — no issues found"
        return
    fi

    echo ""
    echo -e "${YELLOW}Type ${WHITE}${BOLD}YES${RST}${YELLOW} to clean the Docker environment:${RST}"
    printf "${WHITE}> ${RST}"
    read -r answer
    if [ "$answer" != "YES" ]; then
        echo -e "${GREEN}Cancelled — nothing changed.${RST}"
        return
    fi

    if [ $has_broken_plugin -eq 1 ]; then
        rm -f "$HOME/.docker/cli-plugins/docker-compose"
        print_success "Removed broken ~/.docker/cli-plugins/docker-compose"
    fi

    if [ $has_podman -eq 1 ]; then
        print_info "Removing podman..."
        sudo steamos-readonly disable 2>/dev/null || true
        if sudo pacman -R --noconfirm podman 2>/dev/null; then
            print_success "podman removed via pacman"
        else
            print_warning "pacman couldn't remove podman — may have been installed another way"
            print_info "Try manually: flatpak uninstall podman  or  sudo pacman -R podman"
        fi
        sudo steamos-readonly enable 2>/dev/null || true
    fi

    echo ""
    echo -e "${WHITE}Install real Docker + Compose now? (Recommended)${RST}"
    printf "${WHITE}[y/N] > ${RST}"
    read -r install_answer
    if [[ "${install_answer,,}" == "y" ]]; then
        print_info "Installing Docker + Compose..."
        sudo steamos-readonly disable 2>/dev/null || true
        if sudo pacman -Sy --noconfirm docker docker-compose; then
            sudo steamos-readonly enable 2>/dev/null || true
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER"
            print_success "Docker installed. Log out and back in (or: newgrp docker) before using."
        else
            sudo steamos-readonly enable 2>/dev/null || true
            print_warning "Docker install failed — try: sudo pacman -S docker docker-compose"
        fi
    else
        sudo steamos-readonly enable 2>/dev/null || true
    fi
}

# ─────────────────────────────────────────
# UNINSTALL EVERYTHING
# ─────────────────────────────────────────
uninstall_all() {
    clear
    echo ""
    echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${RED}${BOLD}║   💀  UNINSTALL EVERYTHING                       ║${RST}"
    echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "${WHITE}Installed games that will be deleted:${RST}"
    echo ""

    local dirs=(
        "WoW Playerbots:$HOME/wow-server-playerbots"
        "WoW Vanilla:$HOME/wow-vanilla-server"
        "WoW TBC:$HOME/wow-tbc-server"
        "Dark Age of Camelot:$HOME/daoc-server"
        "Ragnarok Online:$HOME/ro-server"
        "Monster Hunter Frontier Z:$HOME/mhf-server"
        "MapleStory v83:$HOME/maplestory-server"
        "EverQuest 1:$HOME/eq1-server"
        "Tibia:$HOME/tibia-server"
        "Lineage 2:$HOME/lineage2-server"
        "Final Fantasy XI:$HOME/ffxi-server"
        "Star Wars Galaxies:$HOME/swg-server"
        "Ultima Online:$HOME/uo-server"
        "RuneScape 2009:$HOME/runescape-server"
        "PSO Blue Burst:$HOME/pso-server"
        "MU Online:$HOME/muonline-server"
        "LEGO Universe:$HOME/lego-server"
    )

    local found=0
    for e in "${dirs[@]}"; do
        local nm="${e%%:*}" dr="${e#*:}"
        if [ -d "$dr" ]; then
            echo -e "  ${RED}🗑️  $nm${RST}"
            found=$((found + 1))
        fi
    done

    if [ $found -eq 0 ]; then
        echo -e "  ${GREEN}Nothing to uninstall — no games found!${RST}"
        echo ""
        echo -e "${WHITE}Press ENTER...${RST}"; read -r
        return
    fi

    echo ""
    echo -e "${RED}${BOLD}⚠️  ALL CHARACTER DATA AND PROGRESS WILL BE LOST ⚠️${RST}"
    echo ""
    echo -e "${YELLOW}Type ${WHITE}${BOLD}DELETE ALL${RST}${YELLOW} (exact) to confirm:${RST}"
    printf "${WHITE}> ${RST}"
    read -r answer

    if [ "$answer" != "DELETE ALL" ]; then
        echo -e "${GREEN}Cancelled — nothing deleted.${RST}"
        echo ""; echo -e "${WHITE}Press ENTER...${RST}"; read -r
        return
    fi

    echo ""
    print_info "Stopping all servers..."
    local running; running=$(docker ps -q 2>/dev/null)
    [ -n "$running" ] && docker stop $running 2>/dev/null || true
    sleep 2

    # Compose down every stack (check both compose.yml and docker-compose.yml)
    for e in "${dirs[@]}"; do
        local dr="${e#*:}"
        if [ -f "$dr/compose.yml" ] || [ -f "$dr/docker-compose.yml" ]; then
            cd "$dr" && docker compose down -v 2>/dev/null || true
        fi
    done

    # Remove all server dirs
    print_info "Removing server files..."
    for e in "${dirs[@]}"; do
        local dr="${e#*:}"
        [ -d "$dr" ] && rm -rf "$dr" && print_success "Removed: $dr"
    done

    # Remove source dirs and client dirs
    [ -d "$HOME/daoc-spbots" ] && rm -rf "$HOME/daoc-spbots" && \
        print_success "Removed: ~/daoc-spbots"
    [ -d "$HOME/ClassicUO" ] && rm -rf "$HOME/ClassicUO" && \
        print_success "Removed: ~/ClassicUO"
    [ -d "$HOME/runescape-client" ] && rm -rf "$HOME/runescape-client" && \
        print_success "Removed: ~/runescape-client"

    # Kill native processes (PSO runs without Docker)
    pkill -f newserv 2>/dev/null || true

    # Remove all launchers
    print_info "Removing launchers..."
    local launchers=(
        wow-playerbots-launcher.sh
        wow-vanilla-launcher.sh wow-tbc-launcher.sh daoc-launcher.sh
        ro-launcher.sh mhf-launcher.sh maplestory-launcher.sh eq1-launcher.sh
        tibia-launcher.sh lineage2-launcher.sh ffxi-launcher.sh swg-launcher.sh
        uo-launcher.sh runescape-launcher.sh pso-launcher.sh
        muonline-launcher.sh lego-launcher.sh
    )
    for l in "${launchers[@]}"; do
        [ -f "$HOME/$l" ] && rm -f "$HOME/$l" && print_success "Removed: $l"
    done

    # Remove /etc/hosts patches
    if grep -q "mhf.capcom.com.jp" /etc/hosts 2>/dev/null; then
        sudo sed -i '/mhf\.capcom\.com\.jp/d' /etc/hosts 2>/dev/null || true
        sudo sed -i '/mhfg\.capcom\.com\.jp/d' /etc/hosts 2>/dev/null || true
        print_success "/etc/hosts cleaned"
    fi

    # Prune Docker volumes
    print_info "Pruning Docker volumes..."
    docker volume prune -f 2>/dev/null || true
    print_success "Docker volumes cleaned"

    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${GREEN}${BOLD}║   ✅  Everything uninstalled!                    ║${RST}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "${WHITE}  To reinstall: run DadsMmoLab.sh${RST}"
    echo -e "${BLUE}  📺 youtube.com/@DadsMmoLab${RST}"
    echo ""
}

# ─────────────────────────────────────────
# MAIN MENU
# ─────────────────────────────────────────
main_menu() {
    while true; do
        clear
        echo ""
        echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════╗${RST}"
        echo -e "${RED}${BOLD}║   🗑️  DAD'S MMO LAB — UNINSTALLER  v${UNINSTALLER_VERSION}       ║${RST}"
        echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════╝${RST}"
        echo ""
        show_status
        echo ""
        echo -e "${YELLOW}━━━ World of Warcraft ━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "   ${WHITE}1)${RST}  WoW: Playerbots (WotLK)"
        echo -e "   ${WHITE}2)${RST}  WoW: Vanilla (1.12)"
        echo -e "   ${WHITE}3)${RST}  WoW: The Burning Crusade"
        echo -e "${YELLOW}━━━ Classic MMOs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "   ${WHITE}4)${RST}  Dark Age of Camelot"
        echo -e "   ${WHITE}5)${RST}  Ragnarok Online"
        echo -e "   ${WHITE}6)${RST}  Monster Hunter Frontier Z"
        echo -e "   ${WHITE}7)${RST}  MapleStory v83"
        echo -e "${YELLOW}━━━ More MMOs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "   ${WHITE}8)${RST}  EverQuest 1"
        echo -e "   ${WHITE}9)${RST}  Tibia"
        echo -e "  ${WHITE}10)${RST}  Lineage 2"
        echo -e "  ${WHITE}11)${RST}  Final Fantasy XI"
        echo -e "  ${WHITE}12)${RST}  Star Wars Galaxies"
        echo -e "  ${WHITE}13)${RST}  Ultima Online"
        echo -e "  ${WHITE}14)${RST}  RuneScape 2009"
        echo -e "  ${WHITE}15)${RST}  PSO Blue Burst"
        echo -e "  ${WHITE}16)${RST}  MU Online"
        echo -e "  ${WHITE}17)${RST}  LEGO Universe"
        echo -e "${YELLOW}━━━ Tools ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "   ${YELLOW}D)${RST}  Clean Docker environment (fix podman/shim issues)"
        echo -e "${RED}━━━ Nuclear Option ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "  ${RED}${BOLD}ALL)${RST}  Uninstall EVERYTHING"
        echo ""
        echo -e "   ${GREEN}Q)${RST}  Quit"
        echo ""
        printf "${WHITE}  Your choice: ${RST}"
        read -r choice

        case "${choice,,}" in
            1)   uninstall_wow_wotlk ;;
            2)   uninstall_wow_vanilla ;;
            3)   uninstall_wow_tbc ;;
            4)   uninstall_daoc ;;
            5)   uninstall_ro ;;
            6)   uninstall_mhf ;;
            7)   uninstall_maple ;;
            8)   uninstall_eq1 ;;
            9)   uninstall_tibia ;;
            10)  uninstall_l2 ;;
            11)  uninstall_ffxi ;;
            12)  uninstall_swg ;;
            13)  uninstall_uo ;;
            14)  uninstall_runescape ;;
            15)  uninstall_pso ;;
            16)  uninstall_muonline ;;
            17)  uninstall_lego ;;
            d)   clean_docker_environment ;;
            all) uninstall_all ; continue ;;
            q)   break ;;
            *)   print_warning "Invalid choice." ;;
        esac

        echo ""; printf "${WHITE}Press ENTER to return to menu...${RST}"; read -r
    done

    clear
    echo ""
    echo -e "${GREEN}${BOLD}  Goodbye! youtube.com/@DadsMmoLab ⚔️${RST}"
    echo ""
}

main_menu
