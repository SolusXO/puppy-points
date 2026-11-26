#!/usr/bin/env bash

# update.sh loads these functions to apply releases in the same way as install.sh.
APP_DIR=/opt/puppy-points
SERVICE=puppy-points
UNIT_PATH=/etc/systemd/system/puppy-points.service
CONFIG_PATH=$APP_DIR/puppy-points.env
SOURCE_PATH=$APP_DIR/update-source
VENV_PATH=$APP_DIR/.venv
IMAGE_FILES=(plus_1.png plus_10.png plus_50.png plus_100.png
             minus_1.png minus_10.png minus_50.png minus_100.png)
RELEASE_FILES=(bot.py database.py requirements.txt install.sh update.sh backup.sh
               uninstall.sh puppy-points.service README.md .gitignore)
for image in "${IMAGE_FILES[@]}"; do
    RELEASE_FILES+=("assets/$image")
done

ok() {
    if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
        printf '\033[32m[OK]\033[0m %s\n' "$1"
    else
        printf '[OK] %s\n' "$1"
    fi
}

die() {
    printf '[ERROR] %s\n' "$1" >&2
    exit 1
}

require_root_and_systemd() {
    (( EUID == 0 )) || die 'Run this script as root inside the Debian container.'
    command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] && \
        systemctl show --property=Version --value >/dev/null ||
        die 'A running systemd system manager is required. Boot the container and run the script inside it.'
}

lock_maintenance() {
    exec 9>>/run/puppy-points-maintenance.lock
    flock -n 9 || die 'Another Puppy Points maintenance operation is running.'
}

validate_source() {
    local requested=$1 file
    local -a missing=()
    release_source=$(realpath -e -- "$requested") || die "Release source does not exist: $requested"
    [[ -d $release_source ]] || die "Release source is not a directory: $release_source"
    [[ $release_source != "$APP_DIR" && $release_source != "$APP_DIR/"* ]] ||
        die 'Use a release source directory outside /opt/puppy-points.'
    [[ $release_source != *$'\n'* ]] || die 'The release source path cannot contain a newline.'
    for file in "${RELEASE_FILES[@]}"; do
        if [[ ! -f $release_source/$file || ! -s $release_source/$file ]]; then
            missing+=("$file")
        fi
    done
    if ((${#missing[@]})); then
        printf '[ERROR] Required release files missing or empty in %s:\n' "$release_source" >&2
        printf '  %s\n' "${missing[@]}" >&2
        exit 1
    fi
    ok 'Verified all release files and all 8 required PNG assets are present'
}

save_update_source() {
    local source_file=$stage/update-source
    printf '%s\n' "$release_source" >"$source_file"
    mv -fT -- "$source_file" "$SOURCE_PATH"
}

save_configuration() {
    local token roles role value
    local -a role_ids=()
    if [[ -e $CONFIG_PATH || -L $CONFIG_PATH ]]; then
        [[ -f $CONFIG_PATH && ! -L $CONFIG_PATH && -s $CONFIG_PATH ]] ||
            die "The existing configuration is invalid: $CONFIG_PATH"
        chown root:root -- "$CONFIG_PATH"
        chmod 600 -- "$CONFIG_PATH"
        ok 'Preserved the existing Discord configuration'
        return
    fi

    while true; do
        printf 'Enter Discord bot token (input hidden): ' >&2
        if ! IFS= read -r -s token; then
            printf '\n' >&2
            die 'A Discord bot token is required.'
        fi
        printf '\n' >&2
        [[ $token =~ ^[A-Za-z0-9._-]+$ ]] && break
        printf '[ERROR] Copy the bot token from the Discord Developer Portal without spaces or a prefix.\n' >&2
    done

    while true; do
        printf 'Enter staff role IDs (comma or space separated, blank for administrators only): ' >&2
        IFS= read -r roles || die 'No response was received for the staff role IDs.'
        roles=${roles//,/ }
        read -r -a role_ids <<<"$roles"
        value=
        for role in "${role_ids[@]}"; do
            if [[ ! $role =~ ^[1-9][0-9]{0,19}$ || (${#role} == 20 && $role > 18446744073709551615) ]]; then
                break
            fi
            value+=${value:+,}$role
        done
        if [[ $value == "$(IFS=,; printf '%s' "${role_ids[*]}")" ]]; then
            break
        fi
        printf '[ERROR] Role IDs must be positive Discord numeric IDs.\n' >&2
    done

    printf 'DISCORD_TOKEN=%s\nPUPPY_STAFF_ROLE_IDS=%s\n' "$token" "$value" >"$stage/configuration"
    chmod 600 -- "$stage/configuration"
    mv -fT -- "$stage/configuration" "$CONFIG_PATH"
    ok 'Saved the Discord configuration securely'
}

stage_release() {
    local file mode
    stage=$(mktemp -d "$APP_DIR/.release.XXXXXXXXXX")
    new_venv=
    old_venv=
    rollback_armed=0
    was_enabled=0
    was_running=0
    venv_swapped=0
    start_attempted=0
    trap 'finish_deployment "$?"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    install -d -m 0755 -- "$stage/new/assets" "$stage/old/assets"
    for file in "${RELEASE_FILES[@]}"; do
        mode=0644
        [[ $file != *.sh ]] || mode=0755
        install -m "$mode" -- "$release_source/$file" "$stage/new/$file"
    done
    python3 - "$stage/new/assets" "${IMAGE_FILES[@]}" <<'PY'
from pathlib import Path
import sys

directory, *filenames = sys.argv[1:]
for filename in filenames:
    path = Path(directory) / filename
    with path.open("rb") as image:
        if image.read(8) != b"\x89PNG\r\n\x1a\n":
            raise SystemExit(f"Invalid PNG asset: {filename}")
PY
    ok 'Verified the PNG signature of all 8 images'

    if [[ -x $VENV_PATH/bin/python ]] && cmp -s -- "$stage/new/requirements.txt" "$APP_DIR/requirements.txt" &&
        "$VENV_PATH/bin/python" -B -c 'import discord'; then
        ok 'Reused the existing Python environment and Discord dependency'
        return
    fi

    new_venv=$(mktemp -d "$APP_DIR/.venv.XXXXXXXXXX")
    chmod 755 -- "$new_venv"
    (
        umask 022
        python3 -m venv "$new_venv"
        ok 'Created the Python virtual environment'
        "$new_venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$stage/new/requirements.txt"
        "$new_venv/bin/python" -m pip check
        ok 'Installed and checked Python dependencies'
    )
    ln -s -- "${new_venv##*/}" "$stage/new-venv"
}

activate_release() {
    local action=$1 file
    [[ ! -L $APP_DIR/assets && ! -L $APP_DIR/data && ! -L $UNIT_PATH ]] ||
        die 'An installed assets directory, data directory, or service file is a symbolic link.'
    install -d -m 0755 -- "$APP_DIR/assets"
    for file in "${RELEASE_FILES[@]}"; do
        if [[ -e $APP_DIR/$file || -L $APP_DIR/$file ]]; then
            [[ -f $APP_DIR/$file && ! -L $APP_DIR/$file ]] || die "Invalid installed file: $file"
            cp -p -- "$APP_DIR/$file" "$stage/old/$file"
        fi
    done
    if [[ -f $UNIT_PATH ]]; then
        cp -p -- "$UNIT_PATH" "$stage/old-unit"
        systemctl is-active --quiet "$SERVICE" && was_running=1
        systemctl is-enabled --quiet "$SERVICE" && was_enabled=1
    fi

    rollback_armed=1
    if [[ -f $UNIT_PATH ]]; then
        systemctl stop "$SERVICE"
    fi
    for file in "${RELEASE_FILES[@]}"; do
        mv -fT -- "$stage/new/$file" "$APP_DIR/$file"
    done
    if [[ -n $new_venv ]]; then
        if [[ -e $VENV_PATH || -L $VENV_PATH ]]; then
            if [[ -L $VENV_PATH ]]; then
                old_venv=$(readlink -- "$VENV_PATH")
            fi
            mv -T -- "$VENV_PATH" "$stage/old-venv"
        fi
        venv_swapped=1
        mv -T -- "$stage/new-venv" "$VENV_PATH"
    fi
    ok 'Installed application files and original images'

    runuser -u "$SERVICE" -- "$VENV_PATH/bin/python" -B "$APP_DIR/database.py" init
    ok 'SQLite database is ready'
    install -m 0644 -- "$APP_DIR/puppy-points.service" "$UNIT_PATH"
    systemctl daemon-reload
    ok 'Installed the systemd service'
    if [[ $action == install ]]; then
        systemctl enable "$SERVICE"
        systemctl is-enabled --quiet "$SERVICE"
        ok 'Enabled automatic startup'
    fi

    systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
    printf 'Waiting for Discord authentication, connection, and slash command registration.\n'
    start_attempted=1
    systemctl restart "$SERVICE" || die 'The bot did not complete startup.'
    systemctl is-active --quiet "$SERVICE" || die 'The bot exited after startup.'
    ok 'Bot is running and Discord accepted the slash command registration'
}

restore_release() {
    local file
    if ((start_attempted)); then
        systemctl stop "$SERVICE" || return 1
    fi
    for file in "${RELEASE_FILES[@]}"; do
        if [[ -f $stage/old/$file ]]; then
            mv -fT -- "$stage/old/$file" "$APP_DIR/$file" || return 1
        else
            rm -f -- "$APP_DIR/$file" || return 1
        fi
    done
    if ((venv_swapped)); then
        rm -f -- "$VENV_PATH" || return 1
        if [[ -e $stage/old-venv || -L $stage/old-venv ]]; then
            mv -T -- "$stage/old-venv" "$VENV_PATH" || return 1
        fi
    fi
    if [[ -f $UNIT_PATH ]] && ((! was_enabled)); then
        systemctl disable "$SERVICE" || return 1
    fi
    if [[ -f $stage/old-unit ]]; then
        mv -fT -- "$stage/old-unit" "$UNIT_PATH" || return 1
    else
        rm -f -- "$UNIT_PATH" || return 1
    fi
    systemctl daemon-reload || return 1
    if ((was_running)); then
        systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
        systemctl start "$SERVICE" || return 1
    fi
}

finish_deployment() {
    local status=$1
    trap - EXIT INT TERM
    set +e
    if ((status != 0)); then
        if ((rollback_armed)); then
            if restore_release; then
                printf 'Restored the previous application files and service state.\n' >&2
            else
                printf '[ERROR] Automatic rollback failed. Recovery files are in %s.\n' "$stage" >&2
                printf 'Check systemctl status puppy-points and journalctl -u puppy-points -n 100 --no-pager.\n' >&2
                exit "$status"
            fi
        fi
        [[ -z $new_venv ]] || rm -rf -- "$new_venv"
        printf '[ERROR] Deployment stopped. Database and configuration were preserved.\n' >&2
        printf 'Check systemctl status puppy-points and journalctl -u puppy-points -n 100 --no-pager.\n' >&2
    elif [[ $old_venv =~ ^\.venv\.[[:alnum:]]{10}$ ]]; then
        rm -rf -- "$APP_DIR/$old_venv"
    fi
    rm -rf -- "$stage"
    exit "$status"
}

install_main() {
    set -Eeuo pipefail
    set +x
    umask 077
    export LC_ALL=C
    (($# == 0)) || die 'Usage: bash install.sh'
    printf '%s\n' '========================================' '       Puppy Points Bot Installer' \
        '                v1.0.0' '========================================'
    require_root_and_systemd
    [[ -r /etc/os-release ]] || die 'Unable to determine the operating system.'
    # shellcheck source=/dev/null
    . /etc/os-release
    [[ ${ID:-} == debian && (${VERSION_ID:-} == 12 || ${VERSION_ID:-} == 13) ]] ||
        die 'Puppy Points requires Debian 12 or 13.'
    ok "Detected Debian $VERSION_ID with systemd"
    lock_maintenance
    validate_source "$(dirname -- "${BASH_SOURCE[0]}")"

    apt update || die 'apt update failed. Check DNS, network access, and Debian package sources.'
    apt install -y --no-install-recommends python3 python3-venv ca-certificates ||
        die 'Installing the required Debian packages failed. Review the apt output above.'
    ok 'Installed required Debian packages'
    if ! id "$SERVICE" >/dev/null 2>&1; then
        useradd --system --user-group --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE"
    fi
    [[ $(id -u "$SERVICE") != 0 && $(id -gn "$SERVICE") == "$SERVICE" ]] ||
        die 'The existing puppy-points account must be non-root and have the puppy-points primary group.'
    ok 'Dedicated non-root service account is ready'

    [[ ! -L $APP_DIR && ! -L $APP_DIR/data ]] || die 'The application and data directories must be ordinary directories.'
    install -d -o root -g root -m 0755 -- "$APP_DIR"
    install -d -o "$SERVICE" -g "$SERVICE" -m 0700 -- "$APP_DIR/data"
    stage_release
    save_configuration
    activate_release install
    if [[ ! -e $SOURCE_PATH && ! -L $SOURCE_PATH ]]; then
        save_update_source
    fi
    printf '\n%s\n' '========================================' '        Installation Successful!' \
        '========================================' 'Service status: Active (running)' 'Images loaded: 8/8' \
        'Database: Ready' 'Automatic startup: Enabled' 'Discord connection: Connected' '' 'Puppy Points is ready!'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    install_main "$@"
fi
