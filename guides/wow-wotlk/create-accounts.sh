#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Account Creator
#  Create WoW accounts easily from Desktop Mode
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.0.0
#
#  Usage:
#    chmod +x create-accounts.sh
#    ./create-accounts.sh
# ============================================================

SCRIPT_VERSION="1.0.0"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'

clear
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${WHITE}${BOLD}         ⚙️  DAD'S MMO LAB                        ${NC}${CYAN}║${NC}"
echo -e "${CYAN}║${WHITE}         Account Creator                          ${NC}${CYAN}║${NC}"
echo -e "${CYAN}║${BLUE}         github.com/DadsMmoLab/dads-mmo-lab       ${NC}${CYAN}║${NC}"
echo -e "${CYAN}║${YELLOW}         Version ${SCRIPT_VERSION}                              ${NC}${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }

# ─────────────────────────────────────────
# DETECT RUNNING SERVER
# ─────────────────────────────────────────
WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
    2>/dev/null | grep -i "worldserver" | head -1)

if [ -z "$WORLD_CONTAINER" ]; then
    print_error "No WoW server is running!"
    echo ""
    print_info "Start your server first:"
    echo -e "  ${CYAN}cd ~/wow-server && docker compose up -d${NC}"
    echo -e "  ${CYAN}cd ~/wow-server-npcbots && docker compose up -d${NC}"
    echo -e "  ${CYAN}cd ~/wow-server-playerbots && docker compose up -d${NC}"
    echo ""
    exit 1
fi

print_success "Found server: $WORLD_CONTAINER"
echo ""

# ─────────────────────────────────────────
# CHECK SERVER IS READY
# ─────────────────────────────────────────
print_info "Checking server is ready..."
if ! docker logs "$WORLD_CONTAINER" 2>/dev/null | grep -q "ready\.\.\."; then
    print_warning "Server may still be initializing."
    print_info "Wait for 'AZEROTH IS READY' then run this script again."
    exit 1
fi
print_success "Server is ready!"
echo ""

# ─────────────────────────────────────────
# ACCOUNT CREATION LOOP
# ─────────────────────────────────────────
echo -e "${WHITE}${BOLD}Create your WoW accounts below.${NC}"
echo -e "${WHITE}Type as many as you need — just press Enter after each.${NC}"
echo ""

ACCOUNTS_CREATED=0

while true; do
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    echo -e "${WHITE}Username (or press Enter to finish): ${NC}"
    read -r USERNAME
    [ -z "$USERNAME" ] && break

    echo -e "${WHITE}Password: ${NC}"
    read -rs PASSWORD
    echo ""
    if [ -z "$PASSWORD" ]; then
        print_warning "Password cannot be empty. Try again."
        continue
    fi

    echo ""
    print_info "Creating account: $USERNAME..."
    echo ""

    # Write commands to a temp file
    TMPFILE=$(mktemp /tmp/wow-cmds-XXXX.txt)
    echo "account create ${USERNAME} ${PASSWORD} ${PASSWORD}" > "$TMPFILE"

    # Send via docker exec with input redirect
    docker exec -i "$WORLD_CONTAINER" \
        bash -c "cat" < "$TMPFILE" 2>/dev/null || true
    sleep 4

    # Set GM level
    echo "account set gmlevel ${USERNAME} 3 -1" > "$TMPFILE"
    docker exec -i "$WORLD_CONTAINER" \
        bash -c "cat" < "$TMPFILE" 2>/dev/null || true
    sleep 3

    rm -f "$TMPFILE"

    # Verify via database
    DB_CONTAINER=$(docker ps --format '{{.Names}}' \
        2>/dev/null | grep -iE "ac.database|ac_database" | head -1)

    if [ -n "$DB_CONTAINER" ]; then
        FOUND=$(docker exec "$DB_CONTAINER" \
            mysql -uroot -ppassword acore_auth -sNe \
            "SELECT COUNT(*) FROM account WHERE username=UPPER('${USERNAME}');" \
            2>/dev/null)

        if [ "${FOUND}" = "1" ]; then
            print_success "Account created: ${USERNAME}"
            ACCOUNTS_CREATED=$((ACCOUNTS_CREATED + 1))
        else
            print_warning "Could not verify ${USERNAME}."
            print_info "Create it manually in the GM console:"
            echo ""
            echo -e "  ${CYAN}docker attach $WORLD_CONTAINER${NC}"
            echo -e "  ${GREEN}account create ${USERNAME} ${PASSWORD} ${PASSWORD}${NC}"
            echo -e "  ${GREEN}account set gmlevel ${USERNAME} 3 -1${NC}"
            echo -e "  ${YELLOW}Then Ctrl+P then Ctrl+Q to exit${NC}"
        fi
    else
        print_success "Account command sent: ${USERNAME}"
        ACCOUNTS_CREATED=$((ACCOUNTS_CREATED + 1))
    fi

    echo ""
done

# ─────────────────────────────────────────
# DONE
# ─────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ $ACCOUNTS_CREATED -gt 0 ]; then
    echo -e "${GREEN}${BOLD}✅ Done! Created $ACCOUNTS_CREATED account(s).${NC}"
else
    echo -e "${YELLOW}No accounts created.${NC}"
fi

echo ""
echo -e "${WHITE}Set your WoW realmlist to: ${GREEN}set realmlist 127.0.0.1${NC}"
echo -e "${WHITE}All accounts have GM Level 3 — full admin powers.${NC}"
echo ""
echo -e "${WHITE}Need to create more accounts later? Just run:${NC}"
echo -e "  ${CYAN}chmod +x create-accounts.sh && ./create-accounts.sh${NC}"
echo ""
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${NC}"
echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${NC}"
echo -e "${WHITE}  ☕ ko-fi.com/dadsmmolab${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
