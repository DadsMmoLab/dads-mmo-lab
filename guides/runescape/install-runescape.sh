#!/bin/bash
# ============================================================
#  Dad's MMO Lab — RuneScape 2009 Server Installer
#  Powered by 2009scape Singleplayer Edition
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.1.0
#
#  Usage:
#    chmod +x install-runescape.sh
#    ./install-runescape.sh
#
#  What this does:
#    1. Installs Java (JRE) — needed to run the server + client
#    2. Clones the 2009scape Singleplayer Edition for Linux
#       (includes bundled MySQL, server.jar, ms.jar, client.jar)
#    3. Initializes the bundled database (one-time setup)
#    4. Sets up the Gaming Mode launcher
#
#  Powered by:
#    2009scape Singleplayer Edition
#    github.com/2009scape/Singleplayer-Edition-Linux
#
#  ⚠️  This uses the SINGLEPLAYER edition — everything is
#  bundled together (MySQL, server, client). No Docker needed!
#  Java runs it all natively on Linux. No Proton required!
# ============================================================

INSTALLER_VERSION="1.1.0"

set -o pipefail

RST='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'

RS='\033[0;33m'
RSB='\033[1;33m'

print_header() {
    clear
    echo ""
    echo -e "${RS}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${RS}║${WHITE}${BOLD}         🗡️  DAD'S MMO LAB                        ${RST}${RS}║${RST}"
    echo -e "${RS}║${WHITE}         RuneScape 2009 Installer v${INSTALLER_VERSION}          ${RST}${RS}║${RST}"
    echo -e "${RS}║${BLUE}         2009scape Singleplayer Edition           ${RST}${RS}║${RST}"
    echo -e "${RS}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} $1${RST}"
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
}

print_success() { echo -e "${GREEN}✅ $1${RST}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${RST}"; }
print_error()   { echo -e "${RED}❌ $1${RST}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${RST}"; }

ask_yes_no() {
    while true; do
        printf "${WHITE}$1 (y/n): ${RST}"
        read -r answer
        case $answer in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "Please answer y or n." ;;
        esac
    done
}

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
SERVER_DIR="$HOME/runescape-server"

# ─────────────────────────────────────────
# SYSTEM CHECK
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System"
    [[ "$OSTYPE" != "linux-gnu"* ]] && { print_error "Linux required."; exit 1; }
    print_success "Linux detected"

    AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 5 ] 2>/dev/null; then
        print_error "Need at least 5GB free. Have ${AVAILABLE_GB}GB."
        exit 1
    fi
    print_success "Disk space OK (${AVAILABLE_GB:-unknown}GB available)"

    if ! ping -c 1 github.com &>/dev/null; then
        print_error "No internet connection."
        exit 1
    fi
    print_success "Internet OK"
}

# ─────────────────────────────────────────
# JAVA
# ─────────────────────────────────────────
install_java() {
    # ── CRITICAL ─────────────────────────────────────────────
    # 2009scape's PlayerSaver uses Nashorn (Java's old built-in
    # JavaScript engine) to serialize player data during save.
    # Nashorn was REMOVED from the JDK in Java 15. So on Java 15+
    # (which is what `jre-openjdk` resolves to today), the save
    # path throws NullPointerException because ScriptEngineManager
    # can't find a "javascript" engine.
    #
    # Symptom: server starts and accepts logins fine, but every
    # save attempt fails silently — character resets to first-
    # login state every time the player reconnects.
    #
    # Fix: install Java 11 specifically. It has Nashorn built in.
    # SteamOS's `jre11-openjdk` package installs to
    # /usr/lib/jvm/java-11-openjdk/ without conflicting with any
    # newer Java the user already has. The launcher pins its PATH
    # to this version so server.jar always runs on Java 11
    # regardless of system default.
    # ─────────────────────────────────────────────────────────

    # Check if Java 11 specifically is already installed
    if [ -x "/usr/lib/jvm/java-11-openjdk/bin/java" ]; then
        local ver
        ver=$(/usr/lib/jvm/java-11-openjdk/bin/java -version 2>&1 | head -1)
        print_success "Java 11 already installed: $ver"
        # Verify Nashorn is actually present (paranoid check)
        if /usr/lib/jvm/java-11-openjdk/bin/jrunscript -l 2>/dev/null | \
           grep -q "ECMAScript"; then
            print_success "Nashorn JavaScript engine confirmed available"
            return 0
        else
            print_warning "Java 11 installed but Nashorn missing — unusual"
            print_info "Continuing — server may still fail to save"
            return 0
        fi
    fi

    print_info "Installing Java 11 (required for 2009scape's save engine)..."

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || true
    fi

    # Install Java 11 specifically (NOT latest)
    if sudo pacman -Sy --noconfirm jre11-openjdk 2>/dev/null; then
        print_success "Java 11 installed at /usr/lib/jvm/java-11-openjdk/"
        # Verify Nashorn
        if /usr/lib/jvm/java-11-openjdk/bin/jrunscript -l 2>/dev/null | \
           grep -q "ECMAScript"; then
            print_success "Nashorn JavaScript engine confirmed"
        else
            print_warning "Java 11 installed but Nashorn check failed"
            print_info "Continuing anyway — engine should still register"
        fi
        return 0
    fi

    # If Java 11 unavailable, try the JDK variant
    if sudo pacman -Sy --noconfirm jdk11-openjdk 2>/dev/null; then
        print_success "JDK 11 installed (heavier than needed but works)"
        return 0
    fi

    # Last resort: any Java — but warn loudly
    print_warning "Couldn't install Java 11 specifically. Trying latest..."
    if sudo pacman -Sy --noconfirm jre-openjdk 2>/dev/null; then
        print_warning "Installed default Java — character saves WILL FAIL"
        print_info "Reason: Nashorn (needed by 2009scape) was removed in Java 15"
        print_info "Try: sudo pacman -Sy jre11-openjdk"
        return 0
    fi

    print_error "Java installation failed."
    print_info "Try manually: sudo pacman -Sy jre11-openjdk"
    print_info "Then re-run this installer."
    exit 1
}

install_git() {
    if command -v git &>/dev/null; then
        print_success "Git already installed"; return 0
    fi
    print_info "Installing git..."
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || true
    fi
    sudo pacman -Sy --noconfirm git 2>/dev/null && \
        print_success "Git installed!" || \
        print_warning "Git install failed — continuing anyway"
}

install_wmctrl() {
    # wmctrl + xdotool let the launcher resize/position the Java client window
    # so it fills the Steam Deck's 1280x800 screen instead of being letterboxed.
    # Not fatal if either fails — launcher falls back to native window size.
    if command -v wmctrl &>/dev/null && command -v xdotool &>/dev/null; then
        print_success "Window-management tools already installed"; return 0
    fi
    print_info "Installing window-management tools (wmctrl, xdotool)..."
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || true
    fi
    sudo pacman -Sy --noconfirm wmctrl xdotool 2>/dev/null && \
        print_success "Window tools installed!" || \
        print_warning "Window tools install failed — client will use default 765x503 window"
}

install_mysql_deps() {
    # The bundled mysqld from 2009scape SP Linux was compiled against an
    # older glibc and expects libcrypt.so.1, which Arch/SteamOS removed
    # years ago (libcrypt.so.2 is the modern replacement, and isn't ABI
    # compatible). Without libxcrypt-compat installed, mysqld crashes
    # IMMEDIATELY on startup with:
    #
    #   bin/mysqld: error while loading shared libraries: libcrypt.so.1:
    #               cannot open shared object file: No such file or directory
    #
    # This is the #1 cause of "bundled mysql failed to start" on Steam Deck.
    # libaio is also a runtime dep of modern mysqld builds.
    print_info "Checking MySQL runtime dependencies..."

    local need_install=false
    # Test for libcrypt.so.1 — check both common library paths
    if ! ldconfig -p 2>/dev/null | grep -q "libcrypt.so.1"; then
        need_install=true
        print_info "  libcrypt.so.1 missing — needed by bundled mysqld"
    fi
    if ! ldconfig -p 2>/dev/null | grep -q "libaio.so"; then
        need_install=true
        print_info "  libaio missing — needed by InnoDB"
    fi

    if [ "$need_install" = "false" ]; then
        print_success "MySQL runtime libraries already present"
        return 0
    fi

    print_info "Installing libxcrypt-compat and libaio (mysqld dependencies)..."
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || true
    fi

    if sudo pacman -Sy --noconfirm libxcrypt-compat libaio 2>/dev/null; then
        print_success "MySQL runtime libraries installed!"
    else
        print_warning "Couldn't install libxcrypt-compat/libaio via pacman"
        print_info "If the database fails to start, run manually:"
        print_info "  sudo pacman -Sy libxcrypt-compat libaio"
    fi
}

verify_mysqld_libs() {
    # Run ldd on the bundled mysqld to confirm no missing libraries.
    # Catches lib issues BEFORE we try to start the server.
    # Called after the repo is cloned, before init_database.
    local mysqld="$SERVER_DIR/database/bin/mysqld"
    if [ ! -x "$mysqld" ]; then
        return 0  # Will fail downstream with a clearer error
    fi

    local missing
    missing=$(ldd "$mysqld" 2>&1 | grep "not found" || true)
    if [ -n "$missing" ]; then
        print_warning "Bundled mysqld has missing library dependencies:"
        echo "$missing" | sed 's/^/    /'
        echo ""
        # If libcrypt.so.1 is in the list, give exact fix
        if echo "$missing" | grep -q "libcrypt.so.1"; then
            print_info "Fix: sudo pacman -Sy libxcrypt-compat"
        fi
        if echo "$missing" | grep -q "libaio"; then
            print_info "Fix: sudo pacman -Sy libaio"
        fi
        return 1
    fi

    print_success "Bundled mysqld has all required libraries"
    return 0
}

# ─────────────────────────────────────────
# WELCOME
# ─────────────────────────────────────────
show_welcome() {
    print_header
    echo -e "${WHITE}Welcome to the RuneScape 2009 installer!${RST}"
    echo ""
    echo -e "${RSB}RuneScape 2009 era${RST}"
    echo -e "${WHITE}Peak RuneScape. Before the Evolution of Combat.${RST}"
    echo -e "${WHITE}The game everyone in school played.${RST}"
    echo -e "${WHITE}Mining. Fishing. Quests. The Grand Exchange.${RST}"
    echo ""
    echo -e "${RSB}What makes this special:${RST}"
    echo -e "${WHITE}  🗡️  2009scape Singleplayer — everything bundled in one repo${RST}"
    echo -e "${WHITE}  ☕ Java client runs natively — NO Proton needed!${RST}"
    echo -e "${WHITE}  📦 Bundled MySQL, management server, game server, client${RST}"
    echo -e "${WHITE}  🌍 Most globally recognized MMO name after WoW${RST}"
    echo ""
    echo -e "${YELLOW}⚠️  This is the singleplayer edition.${RST}"
    echo -e "${YELLOW}   You play solo locally on your Steam Deck.${RST}"
    echo -e "${YELLOW}   Just log in with any username to create your account!${RST}"
    echo ""
    echo -e "${BLUE}ℹ️  Install time: ~5 minutes${RST}"
    echo -e "${BLUE}ℹ️  Storage needed: ~500MB${RST}"
    echo -e "${BLUE}ℹ️  No Docker. No Proton. Pure Java on Linux.${RST}"
    echo ""
    ask_yes_no "Ready to grind? 🗡️" || { echo "Run when ready!"; exit 0; }
}

# ─────────────────────────────────────────
# STEP 1 — CLONE SINGLEPLAYER EDITION
# ─────────────────────────────────────────
clone_server() {
    print_header
    print_step "STEP 1/3 — Downloading 2009scape Singleplayer Edition"

    install_java
    install_git
    install_wmctrl
    install_mysql_deps

    if [ -d "$SERVER_DIR" ]; then
        print_warning "Existing RuneScape installation found at $SERVER_DIR"
        if ask_yes_no "Remove it and start fresh?"; then
            rm -rf "$SERVER_DIR"
            print_success "Old installation removed"
        else
            # Keep it — check if DB is already good
            if [ -d "$SERVER_DIR/database/data" ] && \
               [ "$(ls -A "$SERVER_DIR/database/data" 2>/dev/null)" ]; then
                print_success "Existing installation looks good — skipping clone"
                return 0
            fi
        fi
    fi

    print_info "Cloning 2009scape Singleplayer Edition..."
    print_info "The repo includes JARs via Git LFS — this may take a few minutes"
    echo ""

    # Install git-lfs if not present — the JARs are stored in LFS
    if ! git lfs version &>/dev/null 2>&1; then
        print_info "Installing git-lfs (needed for binary files in this repo)..."
        if command -v steamos-readonly &>/dev/null; then
            sudo steamos-readonly disable 2>/dev/null || true
        fi
        sudo pacman -Sy --noconfirm git-lfs 2>/dev/null || true
        git lfs install 2>/dev/null || true
    fi
    if ! git lfs version &>/dev/null 2>&1; then
        print_warning "git-lfs not available — JAR binaries may not download correctly"
        print_info "The JAR size check after cloning will catch any issues."
    else
        print_success "git-lfs ready"
    fi

    if ! git clone \
        https://github.com/2009scape/Singleplayer-Edition-Linux.git \
        "$SERVER_DIR"; then
        print_error "Clone failed. Check your internet connection."
        exit 1
    fi

    cd "$SERVER_DIR"

    print_info "Pulling binary files via Git LFS..."
    if ! git lfs pull 2>/dev/null; then
        print_warning "git lfs pull had issues — checking JAR sizes..."
    fi

    print_success "2009scape Singleplayer Edition cloned!"

    # Sanity-check the JARs — LFS stubs are tiny (< 1KB)
    local all_ok=true
    for jar in server.jar ms.jar client.jar; do
        if [ ! -f "$SERVER_DIR/$jar" ]; then
            print_warning "Missing: $jar"
            all_ok=false
        else
            local size
            size=$(wc -c < "$SERVER_DIR/$jar" 2>/dev/null || echo 0)
            if [ "$size" -lt 10000 ]; then
                print_warning "$jar is only ${size} bytes — likely an LFS pointer stub"
                all_ok=false
            else
                print_success "$jar OK (${size} bytes)"
            fi
        fi
    done

    if [ "$all_ok" = false ]; then
        echo ""
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo -e "${WHITE}${BOLD} Git LFS Download Issue — Manual Fix${RST}"
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
        echo ""
        echo -e "  ${WHITE}The JAR files didn't download properly via LFS.${RST}"
        echo -e "  ${WHITE}This can happen on SteamOS. Here's the fix:${RST}"
        echo ""
        echo -e "  ${CYAN}1. Go to this URL in a browser on any device:${RST}"
        echo -e "  ${GREEN}   https://github.com/2009scape/Singleplayer-Edition-Linux${RST}"
        echo -e "  ${CYAN}2. Click green Code button → Download ZIP${RST}"
        echo -e "  ${CYAN}3. Transfer the ZIP to your Steam Deck${RST}"
        echo -e "  ${CYAN}4. Extract it to: ${GREEN}~/runescape-server${RST}"
        echo -e "  ${CYAN}5. Re-run this installer${RST}"
        echo ""
        if ! ask_yes_no "Continue anyway and try to initialize the database?"; then
            print_info "Come back after downloading the ZIP manually!"
            exit 0
        fi
    fi
}

# ─────────────────────────────────────────
# STEP 2 — INITIALIZE DATABASE
# ─────────────────────────────────────────
init_database() {
    print_header
    print_step "STEP 2/3 — Initializing Game Database"

    cd "$SERVER_DIR"

    # Skip if already done
    if [ -d "$SERVER_DIR/database/data" ] && \
       [ "$(ls -A "$SERVER_DIR/database/data" 2>/dev/null)" ]; then
        print_success "Database already initialized — skipping!"
        return 0
    fi

    print_info "Setting up the bundled MySQL database..."
    print_info "This is a one-time setup — takes about 30-60 seconds."
    echo ""

    # The bundled mysqld needs its own library path
    export LD_LIBRARY_PATH="$SERVER_DIR/database/lib"

    # Verify mysqld libs BEFORE trying to start it. Catches the libcrypt.so.1
    # issue with a clear error message instead of a generic "failed to start".
    if ! verify_mysqld_libs; then
        print_error "Bundled mysqld can't run — missing libraries (see above)."
        print_info "Install the listed packages, then re-run this installer."
        exit 1
    fi

    # ── Pre-flight: is port 3306 free? ────────────────────────
    # mysqld binds to 0.0.0.0:3306. If anything else is on it
    # (orphan mysqld from a previous install attempt, another
    # emulator's database container, system mariadb), the bind
    # will fail with "Address already in use". Catch this BEFORE
    # starting mysqld so the user gets a clear, actionable error.
    #
    # If the holder is OUR OWN bundled mysqld (orphan from a
    # previous install run that didn't clean up), we auto-kill
    # it — no point making the user do it manually when we know
    # exactly what it is and that we own it.
    print_info "Checking port 3306 is available..."
    PORT_HOLDER=""
    PORT_HOLDER_PID=""
    if command -v ss &>/dev/null; then
        PORT_HOLDER=$(sudo ss -tlnp 2>/dev/null | grep -E ':3306\b' | head -1)
        PORT_HOLDER_PID=$(echo "$PORT_HOLDER" | grep -oP 'pid=\K[0-9]+' | head -1)
    elif command -v lsof &>/dev/null; then
        PORT_HOLDER=$(sudo lsof -iTCP:3306 -sTCP:LISTEN 2>/dev/null | tail -1)
        PORT_HOLDER_PID=$(echo "$PORT_HOLDER" | awk '{print $2}')
    fi

    if [ -n "$PORT_HOLDER" ]; then
        # Is this an orphan of OUR OWN bundled mysqld?
        # Check by reading /proc/<pid>/cmdline and matching SERVER_DIR.
        OWNED_BY_US=false
        if [ -n "$PORT_HOLDER_PID" ] && \
           [ -r "/proc/$PORT_HOLDER_PID/cmdline" ]; then
            HOLDER_CMD=$(tr '\0' ' ' < "/proc/$PORT_HOLDER_PID/cmdline")
            # The bundled mysqld is run with relative paths (bin/mysqld)
            # from $SERVER_DIR/database. Check the working dir to
            # confirm it's our install.
            HOLDER_CWD=$(sudo readlink "/proc/$PORT_HOLDER_PID/cwd" 2>/dev/null)
            if echo "$HOLDER_CMD" | grep -q "bin/mysqld" && \
               echo "$HOLDER_CWD" | grep -q "$SERVER_DIR/database"; then
                OWNED_BY_US=true
            fi
        fi

        if [ "$OWNED_BY_US" = "true" ]; then
            print_warning "Port 3306 held by an orphan from a previous install."
            print_info "  PID: $PORT_HOLDER_PID"
            print_info "  CWD: $HOLDER_CWD"
            print_info "  This is our own bundled mysqld — auto-cleaning..."
            sudo kill -TERM "$PORT_HOLDER_PID" 2>/dev/null
            # Wait for graceful exit
            local wait_kill=0
            while [ "$wait_kill" -lt 10 ]; do
                if ! kill -0 "$PORT_HOLDER_PID" 2>/dev/null; then
                    break
                fi
                sleep 1
                wait_kill=$((wait_kill + 1))
            done
            # Force-kill if still alive
            if kill -0 "$PORT_HOLDER_PID" 2>/dev/null; then
                sudo kill -KILL "$PORT_HOLDER_PID" 2>/dev/null
                sleep 2
            fi
            # Belt-and-suspenders: any other bundled mysqld instance
            sudo pkill -KILL -f "$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
            sleep 1
            # Verify port is now free
            if command -v ss &>/dev/null && \
               sudo ss -tlnp 2>/dev/null | grep -qE ':3306\b'; then
                print_error "Couldn't free port 3306 — kill didn't take effect."
                print_info "Please run manually: sudo kill -9 $PORT_HOLDER_PID"
                exit 1
            fi
            print_success "Orphan cleared — port 3306 is free now"
        else
            # Not our process — could be docker, system mariadb,
            # another emulator. Show diagnostics and bail out, since
            # killing it could disrupt something else the user wants.
            print_error "Port 3306 is already in use by a non-2009scape process!"
            echo ""
            echo "  What's using it:"
            echo "    $PORT_HOLDER"
            if [ -n "$PORT_HOLDER_PID" ] && [ -n "$HOLDER_CMD" ]; then
                echo "    Cmdline: $HOLDER_CMD"
            fi
            echo ""
            print_info "Common causes and fixes:"
            echo ""
            echo "  1. Another Dad's MMO Lab emulator's database container:"
            echo "     docker ps | grep -iE \"mysql|mariadb|database\""
            echo "     docker stop <container_name>"
            echo ""
            echo "  2. System MariaDB:"
            echo "     sudo systemctl stop mariadb mysqld 2>/dev/null"
            echo ""
            echo "  3. Some other application listening on 3306:"
            echo "     sudo kill -9 $PORT_HOLDER_PID"
            echo ""
            echo "  After clearing port 3306, re-run this installer."
            exit 1
        fi
    fi
    print_success "Port 3306 is free"

    # ── Pre-flight: clean up any stale socket/lock files ──────
    # If a previous run died mid-write, these files prevent the new
    # mysqld from starting cleanly.
    rm -f "$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
    rm -f "$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true
    rm -f /tmp/mysql.sock /tmp/mysql.sock.lock 2>/dev/null || true

    mkdir -p "$SERVER_DIR/database/data"

    print_info "Starting bundled MySQL..."
    cd "$SERVER_DIR/database"
    bin/mysqld --console --skip-grant-tables \
        --lc-messages-dir="./share/" \
        --datadir="./data" \
        2>/tmp/rs-mysql-init.log &
    local MYSQL_PID=$!

    # Poll for a real connection rather than just checking the process is alive.
    # kill -0 only proves mysqld didn't crash immediately — it says nothing about
    # whether it's actually accepting connections yet.
    local db_ready=false
    local db_elapsed=0
    printf "  Waiting for MySQL to accept connections"
    while [ "$db_elapsed" -lt 30 ]; do
        if "$SERVER_DIR/database/bin/mysql" -u root -e "SELECT 1" >/dev/null 2>&1; then
            db_ready=true
            break
        fi
        if ! kill -0 "$MYSQL_PID" 2>/dev/null; then
            break  # Process died — fall through to error below
        fi
        printf "."
        sleep 2
        db_elapsed=$((db_elapsed + 2))
    done
    echo ""

    if [ "$db_ready" != "true" ]; then
        print_error "Bundled MySQL failed to start."
        echo ""
        print_info "Last 20 lines of /tmp/rs-mysql-init.log:"
        tail -20 /tmp/rs-mysql-init.log 2>/dev/null | sed 's/^/    /'
        echo ""
        # Pattern-match the most common failure modes and give targeted fixes
        if grep -q "libcrypt.so.1" /tmp/rs-mysql-init.log 2>/dev/null; then
            print_warning "DIAGNOSIS: Missing libcrypt.so.1 — this is the most common cause."
            print_info "Fix: sudo pacman -Sy libxcrypt-compat"
        elif grep -q "libaio" /tmp/rs-mysql-init.log 2>/dev/null; then
            print_warning "DIAGNOSIS: Missing libaio — required by InnoDB."
            print_info "Fix: sudo pacman -Sy libaio"
        elif grep -qi "address already in use\|bind on tcp" /tmp/rs-mysql-init.log 2>/dev/null; then
            print_warning "DIAGNOSIS: Port 3306 already in use by another MySQL/MariaDB."
            print_info "Fix: sudo systemctl stop mysqld mariadb 2>/dev/null"
            print_info "Or:  pkill -9 mysqld"
        elif grep -qi "no space left\|disk full" /tmp/rs-mysql-init.log 2>/dev/null; then
            print_warning "DIAGNOSIS: Disk full — InnoDB needs at least 100MB free."
            print_info "Fix: free up disk space, then retry."
        else
            print_info "If you don't see libcrypt or libaio errors above, run:"
            print_info "  ldd $SERVER_DIR/database/bin/mysqld | grep 'not found'"
            print_info "to see what's missing."
        fi
        exit 1
    fi
    print_success "MySQL accepting connections (PID $MYSQL_PID)"

    print_info "Creating game database..."
    cd "$SERVER_DIR"
    echo | database/bin/mysql -u root \
        -e "CREATE DATABASE IF NOT EXISTS global;" 2>/dev/null && \
        print_success "Database 'global' created!" || \
        print_warning "Database create had output — may already exist"

    print_info "Importing game data (world, NPCs, quests)..."
    if [ ! -f "$SERVER_DIR/data/global.sql" ]; then
        print_error "data/global.sql is missing — the repo clone may be incomplete."
        print_info "Try removing $SERVER_DIR and re-running the installer."
        exit 1
    fi
    echo | database/bin/mysql -u root \
        global < data/global.sql 2>/dev/null && \
        print_success "Game data imported!" || \
        print_warning "Import had output — check /tmp/rs-mysql-init.log if issues arise"

    sleep 3

    print_info "Copying client cache files..."
    mkdir -p "$HOME/.runite_rs/runescape"
    if cp -f "$SERVER_DIR/data/cache/"* "$HOME/.runite_rs/runescape/" 2>/dev/null; then
        print_success "Cache files copied!"
    else
        print_warning "Cache copy had issues — client may prompt to re-cache on first run"
    fi

    # ── Create player save directory ──────────────────────────
    # The 2009scape SP Linux build does NOT ship with data/players/
    # pre-created. The server's PlayerSaver tries to write character
    # JSONs there at logout/auto-save time, and if the dir doesn't
    # exist, the save throws an exception and silently fails — user
    # logs back in to a fresh-tutorial state.
    #
    # This is the actual root cause of "my character resets on login"
    # that affected our YouTube viewer (confirmed via stack trace in
    # /tmp/rs-launch.log: PlayerSaver.kt:77 throws inside
    # DisconnectionQueue.save).
    #
    # Fix: create the directory at install time, world-writable so
    # the server can definitely write to it under any environment.
    print_info "Creating player save directory..."
    mkdir -p "$SERVER_DIR/data/players"
    chmod 755 "$SERVER_DIR/data/players"
    # Test we can actually write — catches edge cases like
    # filesystem readonly mounts, ACL issues, etc.
    if touch "$SERVER_DIR/data/players/.write-test" 2>/dev/null; then
        rm -f "$SERVER_DIR/data/players/.write-test"
        print_success "Player save directory created and writable"
    else
        print_warning "Player save directory exists but isn't writable!"
        print_info "Try: chmod -R u+w $SERVER_DIR/data"
    fi

    # ── Clean shutdown of init MySQL ──────────────────────────
    # Use the full bundled-mysqld path so we don't accidentally kill
    # another emulator's mysqld. Verify the process is actually gone
    # before declaring success — otherwise it can hold port 3306 and
    # block the next install attempt.
    print_info "Shutting down init MySQL cleanly..."
    kill -TERM "$MYSQL_PID" 2>/dev/null || true
    # Poll for exit — mysqld can take ~5 seconds to flush InnoDB
    local shutdown_wait=0
    while [ "$shutdown_wait" -lt 15 ]; do
        if ! kill -0 "$MYSQL_PID" 2>/dev/null; then
            break  # Process gone — clean exit
        fi
        sleep 1
        shutdown_wait=$((shutdown_wait + 1))
    done
    # If it's still alive after 15s, force-kill
    if kill -0 "$MYSQL_PID" 2>/dev/null; then
        print_warning "mysqld didn't exit gracefully — force-killing"
        pkill -KILL -f "$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
        sleep 2
    fi
    # Belt-and-suspenders: kill any bundled mysqld still around
    pkill -KILL -f "$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
    rm -f "$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
    rm -f "$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true

    print_success "Database initialized! 🗡️"
    echo ""
    echo -e "${GREEN}  You never have to do this again — the DB is ready.${RST}"
}

# ─────────────────────────────────────────
# STEP 3 — GAMING MODE LAUNCHER
# ─────────────────────────────────────────
setup_launcher() {
    print_header
    print_step "STEP 3/3 — Setting Up Gaming Mode Launcher"

    cat > "$HOME/runescape-launcher.sh" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — RuneScape 2009 Launcher v${INSTALLER_VERSION}

# ── CRITICAL: Pin to Java 11 ─────────────────────────────────
# 2009scape uses Nashorn (Java's JavaScript engine) for player
# saves. Nashorn was removed in Java 15. If the system default
# is Java 17/21/etc, saves silently fail and characters reset
# on every login. We force Java 11 here regardless of system
# default.
JAVA_11_HOME="/usr/lib/jvm/java-11-openjdk"
if [ ! -x "\$JAVA_11_HOME/bin/java" ]; then
    echo ""
    echo "  ❌ Java 11 not found at \$JAVA_11_HOME"
    echo ""
    echo "  Character saves will NOT work without Java 11."
    echo "  (2009scape's save engine uses Nashorn, removed in Java 15)"
    echo ""
    echo "  💡 FIX (in Desktop Mode terminal):"
    echo "        sudo steamos-readonly disable"
    echo "        sudo pacman -Sy jre11-openjdk"
    echo "        sudo steamos-readonly enable"
    echo ""
    echo "  Then re-run this launcher."
    echo "  Window stays open for 30s so you can read this."
    sleep 30
    exit 1
fi
export JAVA_HOME="\$JAVA_11_HOME"
export PATH="\$JAVA_HOME/bin:/usr/bin:/usr/local/bin:/bin:\$PATH"

unset LD_PRELOAD LD_LIBRARY_PATH
LOGFILE="/tmp/rs-launch.log"
> "\$LOGFILE"

SERVER_DIR="${SERVER_DIR}"

# ── Trap: always clean up on exit, even if interrupted ───────
# Save-aware shutdown: the 2009scape server.jar writes player JSON
# files when it receives SIGTERM, but a full character save can take
# 10-20 seconds (XP, inventory, position, bank, quest flags all flush
# to disk). If we killed it too aggressively, players would log back
# in to a fresh-tutorial state — a real bug that hit users.
#
# Order matters here:
#   1. Client first (it's usually already exited by the time we get here)
#   2. Server gets a LONG grace period (30s) to write player saves
#   3. ms.jar after server is done writing
#   4. mysqld LAST — server writes through it, so killing it first
#      mid-save would corrupt the player JSON
cleanup() {
    echo ""
    echo "  Shutting down (saving character data — please wait)..."

    # Capture save state BEFORE shutdown so we can verify after
    local PLAYERS_DIR=""
    for candidate in \\
        "\$SERVER_DIR/data/players" \\
        "\$SERVER_DIR/game/data/players" \\
        "\$SERVER_DIR/Server/data/players"; do
        if [ -d "\$candidate" ]; then
            PLAYERS_DIR="\$candidate"
            break
        fi
    done

    local PRE_SAVE_NEWEST=""
    if [ -n "\$PLAYERS_DIR" ]; then
        PRE_SAVE_NEWEST=\$(find "\$PLAYERS_DIR" -name "*.json" -type f \\
            -printf '%T@\\n' 2>/dev/null | sort -rn | head -1)
    fi

    # 1. Client first (usually already gone, but be explicit)
    pkill -TERM -f "\$SERVER_DIR/client.jar" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "\$SERVER_DIR/client.jar" 2>/dev/null || true

    # 2. Server gets a LONG grace period to flush player saves.
    # We poll for the process to exit on its own — if it does, the save
    # is complete and we move on. If 30s pass, force-kill.
    pkill -TERM -f "\$SERVER_DIR/server.jar" 2>/dev/null || true
    local WAITED=0
    while [ \$WAITED -lt 30 ]; do
        if ! pgrep -f "\$SERVER_DIR/server.jar" > /dev/null 2>&1; then
            break  # Server exited cleanly — save should be complete
        fi
        sleep 1
        WAITED=\$((WAITED + 1))
        # Show a dot every 3s so the user knows we're not frozen
        if [ \$((WAITED % 3)) -eq 0 ]; then
            printf "."
        fi
    done
    if [ \$WAITED -ge 30 ]; then
        echo ""
        echo "  ⚠️  Server didn't exit gracefully in 30s — force-killing."
        echo "      Your latest progress may not have saved."
        pkill -KILL -f "\$SERVER_DIR/server.jar" 2>/dev/null || true
    fi
    echo ""

    # 3. Management server — after the game server is done with it
    pkill -TERM -f "\$SERVER_DIR/ms.jar" 2>/dev/null || true
    sleep 2
    pkill -KILL -f "\$SERVER_DIR/ms.jar" 2>/dev/null || true

    # 4. mysqld LAST — server.jar was writing to it during save
    pkill -TERM -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
    sleep 3
    pkill -KILL -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true

    # Cleanup stale lock/socket files so next launch is clean
    rm -f "\$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
    rm -f "\$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true
    rm -f /tmp/mysql.sock /tmp/mysql.sock.lock 2>/dev/null || true

    # ── Verify save actually happened ────────────────────────
    # If the newest JSON in data/players/ wasn't touched during this
    # session, the save almost certainly didn't write. Tell the user.
    if [ -n "\$PLAYERS_DIR" ]; then
        local POST_SAVE_NEWEST
        POST_SAVE_NEWEST=\$(find "\$PLAYERS_DIR" -name "*.json" -type f \\
            -printf '%T@\\n' 2>/dev/null | sort -rn | head -1)

        if [ -z "\$POST_SAVE_NEWEST" ]; then
            echo ""
            echo "  ⚠️  WARNING: no player JSON files found in \$PLAYERS_DIR"
            echo "      Your character did not save."
            echo ""
            # Look for the specific Nashorn NPE in the log — most common cause
            if grep -q "scriptEngine.*null\|ScriptEngine.put.*null" "\$LOGFILE" 2>/dev/null; then
                echo "  ⚠️  DIAGNOSIS: Nashorn JavaScript engine missing!"
                echo "      The server's save code needs Java 11's built-in Nashorn"
                echo "      engine. Nashorn was removed in Java 15+, so newer Java"
                echo "      versions cause saves to fail with NullPointerException."
                echo ""
                echo "  💡 FIX:"
                echo "        sudo steamos-readonly disable"
                echo "        sudo pacman -Sy jre11-openjdk"
                echo "        sudo steamos-readonly enable"
                echo ""
                echo "      Then run this launcher again — it will auto-detect"
                echo "      Java 11 and use it for the server."
            else
                echo "      Possible causes:"
                echo "        • Played less than 5 minutes (autosave interval)"
                echo "        • Server doesn't have write permission to \$PLAYERS_DIR"
                echo "        • Wrong save_path in worldprops/default.json"
                echo "      Logs: \$LOGFILE"
            fi
        elif [ "\$POST_SAVE_NEWEST" = "\$PRE_SAVE_NEWEST" ]; then
            echo ""
            echo "  ⚠️  WARNING: no new save data was written this session."
            echo "      Newest save in \$PLAYERS_DIR is unchanged."
            if grep -q "scriptEngine.*null\|ScriptEngine.put.*null" "\$LOGFILE" 2>/dev/null; then
                echo ""
                echo "  ⚠️  DIAGNOSIS: Nashorn JavaScript engine missing (see above)"
                echo "  💡 FIX:  sudo pacman -Sy jre11-openjdk"
            else
                echo "      Try playing longer (~5 min) before logging out,"
                echo "      or quit via the in-game logout option, not just X."
            fi
        else
            echo "  ✅ Character data saved successfully"
        fi
    fi

    echo "  ✅ Done! youtube.com/@DadsMmoLab"
}
trap cleanup EXIT INT TERM

clear
echo ""
echo "  🗡️  DAD'S MMO LAB — RuneScape 2009"
echo "  ══════════════════════════════════════════"
echo "  2009scape Singleplayer Edition"
echo "  ══════════════════════════════════════════"
echo ""

cd "\$SERVER_DIR" || {
    echo "  ❌ Server dir not found: \$SERVER_DIR"
    echo "  Run install-runescape.sh first!"
    sleep 10; exit 1
}

# Bundled MySQL needs this
export LD_LIBRARY_PATH="\$SERVER_DIR/database/lib"

# ── Pre-flight: check mysqld libs ────────────────────────────
# Catches the libcrypt.so.1 issue with a clear message BEFORE
# attempting to start mysqld. Cheap (one ldd call) but saves
# the user a confusing failure later.
if [ -x "\$SERVER_DIR/database/bin/mysqld" ]; then
    MISSING_LIBS=\$(ldd "\$SERVER_DIR/database/bin/mysqld" 2>&1 | grep "not found" || true)
    if [ -n "\$MISSING_LIBS" ]; then
        echo ""
        echo "  ⚠️  Bundled mysqld has missing libraries — it will fail to start."
        echo ""
        echo "\$MISSING_LIBS" | sed 's/^/      /'
        echo ""
        if echo "\$MISSING_LIBS" | grep -q "libcrypt.so.1"; then
            echo "  💡 FIX:  sudo pacman -Sy libxcrypt-compat"
        fi
        if echo "\$MISSING_LIBS" | grep -q "libaio"; then
            echo "  💡 FIX:  sudo pacman -Sy libaio"
        fi
        echo ""
        echo "  Run in Desktop Mode, then try the launcher again."
        echo "  (Window stays open for 30s so you can read this.)"
        sleep 30
        exit 1
    fi
fi

# ── Pre-flight: ensure player save directory exists ─────────
# The server WILL silently fail to save characters if this dir
# doesn't exist — confirmed via stack trace from a user's session
# (PlayerSaver.kt:77 throws inside DisconnectionQueue.save).
# Cheap to verify each launch; self-heals if anything wiped it.
if [ ! -d "\$SERVER_DIR/data/players" ]; then
    echo "  Creating missing player save directory..."
    mkdir -p "\$SERVER_DIR/data/players"
    chmod 755 "\$SERVER_DIR/data/players"
fi
if ! touch "\$SERVER_DIR/data/players/.write-test" 2>/dev/null; then
    echo ""
    echo "  ⚠️  Can't write to \$SERVER_DIR/data/players — saves will fail!"
    echo "      Fix: chmod -R u+w \$SERVER_DIR/data"
    echo ""
    sleep 10
fi
rm -f "\$SERVER_DIR/data/players/.write-test" 2>/dev/null

# ── Pre-flight cleanup ───────────────────────────────────────
# Match the full bundled-mysqld path so we don't accidentally kill
# the system mysqld if one is running. Match each JAR by full path
# too, so we don't kill unrelated Java apps.
echo "  Cleaning up any leftover processes..."
pkill -TERM -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/client.jar"   2>/dev/null || true
sleep 2
pkill -KILL -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/client.jar"   2>/dev/null || true
# Short-name kills catch stale processes from a previous session where
# the process was started with a different working dir and the full
# path pattern no longer matches.
pkill -KILL -f "ms.jar"     2>/dev/null || true
pkill -KILL -f "server.jar" 2>/dev/null || true
pkill -KILL -f "client.jar" 2>/dev/null || true
# bin/mysqld is mysqld_safe (a shell script). It forks+execs the real mysqld
# binary, which gets a new cmdline that no longer contains the path we matched
# above. Kill by owner+name to catch the exec'd child regardless of its args.
pkill -KILL -u "\$(id -u)" mysqld 2>/dev/null || true
# If fuser is available, kill anything holding a lock on the InnoDB data files
# directly — most reliable method since it works by file descriptor, not name.
if command -v fuser &>/dev/null; then
    fuser -KILL "\$SERVER_DIR/database/data/ibdata1"        2>/dev/null || true
    fuser -KILL "\$SERVER_DIR/database/data/aria_log_control" 2>/dev/null || true
fi
# Wait for all mysqld owned by this user to exit. The OS flock on ibdata1 and
# aria_log_control is released only when the process fully exits.
KILL_WAIT=0
while pgrep -u "\$(id -u)" mysqld > /dev/null 2>&1; do
    sleep 1
    KILL_WAIT=\$((KILL_WAIT + 1))
    [ \$KILL_WAIT -ge 15 ] && break
done
# Stale socket/lock files from a crashed previous run will prevent
# mysqld from starting. Remove them.
rm -f "\$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
rm -f "\$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true
rm -f /tmp/mysql.sock /tmp/mysql.sock.lock 2>/dev/null || true

# ── Start bundled MySQL ──────────────────────────────────────
echo "  Starting database..."
cd "\$SERVER_DIR/database"
bin/mysqld --console --skip-grant-tables \\
    --lc-messages-dir="./share/" \\
    --datadir="./data" \\
    >> "\$LOGFILE" 2>&1 &
MYSQL_PID=\$!

# ── Real health check — don't just trust kill -0 on a zombie ──
# Try to actually open a connection. mysqld reports "ready" via the
# socket being acceptable, not via the process being alive.
echo "  Waiting for database to accept connections..."
DB_READY=false
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    if "\$SERVER_DIR/database/bin/mysql" -u root \\
        -e "SELECT 1" >/dev/null 2>&1; then
        DB_READY=true
        break
    fi
    # Also fail fast if the process actually died
    if ! kill -0 \$MYSQL_PID 2>/dev/null; then
        echo ""
        echo "  ❌ Database process died during startup!"
        echo ""
        echo "  Last 30 lines of \$LOGFILE:"
        tail -30 "\$LOGFILE" 2>/dev/null | sed 's/^/      /'
        echo ""
        # Diagnose the failure by grepping the log for known patterns
        if grep -q "libcrypt.so.1" "\$LOGFILE" 2>/dev/null; then
            echo "  ⚠️  DIAGNOSIS: Missing libcrypt.so.1 — bundled mysqld can't load."
            echo "      This is the #1 cause of mysql failures on SteamOS."
            echo ""
            echo "  💡 FIX (in Desktop Mode terminal):"
            echo "        sudo steamos-readonly disable"
            echo "        sudo pacman -Sy libxcrypt-compat"
            echo "        sudo steamos-readonly enable"
            echo "      Then re-run this launcher."
        elif grep -qi "libaio" "\$LOGFILE" 2>/dev/null; then
            echo "  ⚠️  DIAGNOSIS: Missing libaio — required by InnoDB."
            echo ""
            echo "  💡 FIX:  sudo pacman -Sy libaio"
        elif grep -qi "address already in use\|bind on tcp\|port.*3306" "\$LOGFILE" 2>/dev/null; then
            echo "  ⚠️  DIAGNOSIS: Port 3306 already in use by another database."
            echo ""
            echo "  💡 FIX:  Check what's using it:"
            echo "        sudo ss -tlnp | grep 3306"
            echo "      Then kill it, or stop the system MySQL/MariaDB:"
            echo "        sudo systemctl stop mysqld mariadb 2>/dev/null"
        elif grep -qi "no space left\|disk full" "\$LOGFILE" 2>/dev/null; then
            echo "  ⚠️  DIAGNOSIS: Disk is full."
            echo ""
            echo "  💡 FIX:  Free up space (InnoDB needs ~100MB minimum)"
        elif grep -qi "innodb.*corruption\|log scan aborted\|page corruption" "\$LOGFILE" 2>/dev/null; then
            echo "  ⚠️  DIAGNOSIS: InnoDB data files appear corrupted."
            echo "      This usually happens after a system crash mid-write."
            echo ""
            echo "  💡 FIX (DESTRUCTIVE — wipes the database, you'll re-init):"
            echo "        rm -rf \$SERVER_DIR/database/data"
            echo "        bash ~/install-runescape.sh  # re-run to re-init"
            echo "      Note: this loses character data. Consider backing up"
            echo "      \$SERVER_DIR/database/data first if anything's important."
        else
            echo "  ⚠️  DIAGNOSIS: Cause not auto-detected. Run:"
            echo "        ldd \$SERVER_DIR/database/bin/mysqld | grep 'not found'"
            echo "      to check for missing libraries."
            echo ""
            echo "      Or post the log lines above to the Dad's MMO Lab"
            echo "      community for help."
        fi
        echo ""
        echo "  Window will close in 30 seconds — copy any error above first."
        sleep 30
        exit 1
    fi
    sleep 2
done

if [ "\$DB_READY" != "true" ]; then
    echo "  ❌ Database did not accept connections within 60 seconds!"
    echo "  Last 30 lines of \$LOGFILE:"
    tail -30 "\$LOGFILE"
    sleep 15
    exit 1
fi
echo "  Database ready!"

# ── Start management server ──────────────────────────────────
echo "  Starting management server..."
cd "\$SERVER_DIR"
java -jar ms.jar >> "\$LOGFILE" 2>&1 &
MS_PID=\$!
sleep 5

if ! kill -0 \$MS_PID 2>/dev/null; then
    echo "  ❌ Management server failed to start!"
    echo "  Last 20 lines of \$LOGFILE:"
    tail -20 "\$LOGFILE"
    sleep 15
    exit 1
fi

# ── Start game server ────────────────────────────────────────
echo "  Starting game server..."
java -jar server.jar >> "\$LOGFILE" 2>&1 &
SERVER_PID=\$!

echo "  Waiting for Gielinor to open..."
# Poll the log for "ready"-ish signals rather than blindly waiting 20s.
SERVER_READY=false
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
    if grep -qiE "listening|ready|started|game world is open|world is now online" \\
        "\$LOGFILE" 2>/dev/null; then
        SERVER_READY=true
        break
    fi
    if ! kill -0 \$SERVER_PID 2>/dev/null; then
        echo "  ❌ Game server died during startup!"
        tail -30 "\$LOGFILE"
        sleep 15
        exit 1
    fi
    sleep 1
done
# Even without an explicit signal, give it a few more seconds for sockets to bind
sleep 3

echo ""
echo "  ══════════════════════════════════════════"
echo "  ✅ GIELINOR IS OPEN! 🗡️"
echo "  ══════════════════════════════════════════"
echo ""
echo "  ⚠️  IMPORTANT:"
echo "     • Click the LEFT button (Standard Detail / SD)"
echo "     • DO NOT click HD — it doesn't work on this client"
echo "       and will cause 'error connecting to server'"
echo ""
echo "     If you're already stuck on HD: in-game, go to"
echo "     Settings → Graphics → switch back to Standard."
echo ""
echo "  Launching client now..."
echo "  Log in with any username + password to play!"
echo "  (First login creates your account automatically)"
echo ""
echo "  💾 SAVING:"
echo "     • Server auto-saves every ~5 minutes"
echo "     • To save before logging out: use in-game LOGOUT button"
echo "       (don't just close the window mid-fight)"
echo "     • The launcher will wait up to 30 seconds for the server"
echo "       to write your character data when you exit"
echo ""

# ── Launch client ────────────────────────────────────────────
java -jar "\$SERVER_DIR/client.jar" >> "\$LOGFILE" 2>&1 &
CLIENT_PID=\$!

# ── Resize client window to fit Steam Deck screen ────────────
# The Java client opens at 765x503 by default — letterboxed on the
# Deck's 1280x800 screen. wmctrl can stretch it to native, but
# we have to wait for the window to actually exist first.
if command -v wmctrl &>/dev/null && command -v xdotool &>/dev/null; then
    echo "  Waiting for client window..."
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        # Look for any window with "RuneScape", "2009scape", or "Client" in title
        WIN_ID=\$(wmctrl -l 2>/dev/null | grep -iE "runescape|2009scape|jagex" | \\
                 head -1 | awk '{print \$1}')
        if [ -n "\$WIN_ID" ]; then
            # Stretch to 1280x800 at position 0,0 — Steam Deck native.
            # First arg: 0=normal, 2=maximize/etc; gravity 0=default.
            wmctrl -i -r "\$WIN_ID" -e "0,0,0,1280,800" 2>/dev/null || true
            echo "  Client window resized to Steam Deck native (1280x800)"
            break
        fi
        sleep 2
    done
fi

# Block on the client process (foreground behavior of original launcher)
wait \$CLIENT_PID 2>/dev/null

# Trap will handle cleanup on exit.
LAUNCHER

    chmod +x "$HOME/runescape-launcher.sh"

    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — RuneScape 2009
  2009scape Singleplayer Edition
====================================

NO Docker. NO Proton. Pure Java.
Everything runs locally on your Deck:
  - Bundled MySQL database
  - Management server (ms.jar)
  - Game server (server.jar)
  - Java client (client.jar)

====================================
  Playing
====================================
Launch: bash ~/runescape-launcher.sh

At the login screen:
  Type any username + any password
  Your account is created automatically!

⚠️  IMPORTANT — DO NOT CLICK HD:
  The legacy 2009scape client doesn't support HD scaling
  properly on non-experimental builds. Clicking HD causes
  "error connecting to server" because the HD assets aren't
  bundled and the client tries to fetch them.

  Always click the LEFT button (Standard Detail / SD).

  If you accidentally chose HD and the client remembers it:
  in-game go to Settings → Graphics → Display mode
  and switch back to "Fixed" or "Resizable" (standard detail).

To get admin rights in-game:
  cd ${SERVER_DIR}
  ./run-linux.sh  (pick option 4)
  Enter your username

====================================
  Gaming Mode Setup
====================================
Add konsole to Steam:
  Target:  /usr/bin/konsole
  Options: --hold -e bash ~/runescape-launcher.sh
  Proton:  OFF (Java is Linux-native!)

====================================
  Window Resolution
====================================
The launcher auto-resizes the client window to 1280x800
(Steam Deck native) using wmctrl. If you don't have wmctrl
installed it'll use the default 765x503 window with black bars.

For a larger play area within the window, in-game go to:
  Settings → Graphics → Display mode → Resizable

====================================
  Troubleshooting
====================================

"Bundled mysql failed to start" / "Database process died":
  This is almost always one of these:

  1. MISSING libcrypt.so.1 (most common on SteamOS)
     The bundled mysqld needs an older crypto library that
     SteamOS removed. Fix:
       sudo steamos-readonly disable
       sudo pacman -Sy libxcrypt-compat
       sudo steamos-readonly enable

  2. MISSING libaio
     Required by InnoDB. Fix:
       sudo pacman -Sy libaio

  3. PORT 3306 IN USE by system MySQL/MariaDB
     Check: sudo ss -tlnp | grep 3306
     Stop:  sudo systemctl stop mysqld mariadb

  4. STALE LOCK FILES from a previous crash
     The launcher auto-cleans these, but if needed manually:
       pkill -9 -f mysqld
       rm -f ${SERVER_DIR}/database/data/*.pid
       rm -f ${SERVER_DIR}/database/data/*.sock*

  5. CORRUPTED DATABASE (rare, after a hard crash)
     DESTRUCTIVE — wipes character data:
       rm -rf ${SERVER_DIR}/database/data
       bash ~/install-runescape.sh   # re-run to re-init

  The launcher auto-diagnoses these and prints the exact fix
  when it detects a failure. Look for "💡 FIX:" lines.

"Launcher closes immediately, Java never opens":
  Same diagnosis path as above — mysqld failed silently.
  Check /tmp/rs-launch.log for the actual error.

"Error connecting to server" at login:
  You clicked HD. Restart the launcher and click SD this time.
  If preferences saved your HD choice, delete:
    ~/.runite_rs/preferences.json   (if it exists)
  and try again.

"My character keeps resetting to tutorial / first-login state":
  Your character data isn't being saved.

  ⭐ FIRST CHECK: Java version (this is the most common cause)

     The 2009scape server uses Nashorn (Java's old JavaScript
     engine) to serialize player data. Nashorn was REMOVED in
     Java 15. So if your system Java is 17, 21, etc., saves
     fail silently with NullPointerException.

     Verify Java 11 is installed:
       /usr/lib/jvm/java-11-openjdk/bin/java -version
     Should print: openjdk version "11.x.x"

     If not, install it:
       sudo steamos-readonly disable
       sudo pacman -Sy jre11-openjdk
       sudo steamos-readonly enable

     The launcher auto-detects and uses Java 11. Your system
     default Java can stay whatever version — we just need 11
     installed alongside it.

  Other less-common causes:

  1. PLAY FOR AT LEAST 5 MINUTES before logging out — the autosave
     interval is roughly 5 minutes. Quick logins won't persist.

  2. USE THE IN-GAME LOGOUT BUTTON, not just the X on the window.
     Closing the client first triggers a save; killing the window
     can leave the server with no time to write your character.

  3. CHECK YOUR SAVE FOLDER. After playing & logging out, run:
       ls -la ${SERVER_DIR}/data/players/
     You should see *.json files matching your account name.

  4. WATCH THE LAUNCHER OUTPUT on exit. New versions print:
       "✅ Character data saved successfully"     ← good
       "⚠️ no new save data was written"          ← bad
       "⚠️ no player JSON files found"            ← very bad
     The "very bad" case now shows a Nashorn-specific diagnosis
     if it detects that's the cause.

  5. CHECK THE LOG for NullPointerException:
       grep -B 3 "scriptEngine.*null" /tmp/rs-launch.log
     If you see hits → it's the Nashorn issue. Install Java 11.

====================================
  Manual Start (if launcher fails)
====================================
  cd ${SERVER_DIR}
  ./run-linux.sh  (option 1 = run game)

Logs: /tmp/rs-launch.log
====================================
INFO

    print_success "Launcher ready: ~/runescape-launcher.sh"
    print_success "Info saved: $SERVER_DIR/MY_SERVER.txt"
}

show_completion() {
    echo ""
    echo -e "${RSB}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${RSB}║   🗡️  GIELINOR IS OPEN!                          ║${RST}"
    echo -e "${RSB}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "  ${WHITE}Server:${RST}  ${RS}2009scape Singleplayer Edition${RST}"
    echo -e "  ${WHITE}Client:${RST}  ${RS}Java — Linux native, NO Proton!${RST}"
    echo -e "  ${WHITE}Login:${RST}   ${RS}Any username + password = auto account creation${RST}"
    echo ""
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} Gaming Mode Setup${RST}"
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "  1. Open Steam in Desktop Mode"
    echo -e "  2. Click ${CYAN}Games${RST} → ${CYAN}Add a Non-Steam Game${RST}"
    echo -e "  3. Browse to ${CYAN}/usr/bin/${RST} → select ${CYAN}konsole${RST}"
    echo -e "  4. Right-click → Properties → rename: ${GREEN}RuneScape 2009${RST}"
    echo -e "  5. Set Launch Options to:"
    echo ""
    echo -e "  ${GREEN}--hold -e bash ~/runescape-launcher.sh${RST}"
    echo ""
    echo -e "  6. ${RED}Do NOT enable Proton${RST} — Java runs natively!"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${RST}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${RST}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "${RSB}Welcome back to Gielinor. 🗡️${RST}"
    echo ""

    echo -e "${WHITE}Launch RuneScape now to test it? (y/n): ${RST}"
    read -r launch_now
    if [[ "$launch_now" =~ ^[Yy]$ ]]; then
        print_info "Launching 2009scape..."
        bash "$HOME/runescape-launcher.sh"
    fi
}

check_system
show_welcome
clone_server
init_database
setup_launcher
show_completion
