#!/usr/bin/env bash

set -Eeuo pipefail
umask 077
export LC_ALL=C
if (( EUID != 0 || $# != 0 )); then
    printf '[ERROR] Run bash uninstall.sh as root inside the Debian container.\n' >&2
    exit 1
fi

APP_DIR=/opt/puppy-points
SERVICE=puppy-points
UNIT_PATH=/etc/systemd/system/puppy-points.service
if [[ -L $APP_DIR ]]; then
    printf '[ERROR] The application directory must be an ordinary directory.\n' >&2
    exit 1
fi
if [[ ! -d $APP_DIR && ! -e $UNIT_PATH && ! -L $UNIT_PATH ]]; then
    printf 'Puppy Points is not installed.\n'
    exit 0
fi

exec 9>>/run/puppy-points-maintenance.lock
if ! flock -n 9; then
    printf '[ERROR] Another Puppy Points maintenance operation is running.\n' >&2
    exit 1
fi
trap 'printf "%s\n" "[ERROR] Uninstall stopped. Review the error above before retrying." >&2' ERR

printf 'Type uninstall to stop Puppy Points and remove its application files: '
IFS= read -r answer || answer=
if [[ $answer != uninstall ]]; then
    printf 'Uninstall canceled.\n'
    exit 0
fi

if [[ -e $UNIT_PATH || -L $UNIT_PATH ]]; then
    systemctl stop "$SERVICE"
    systemctl disable "$SERVICE"
    rm -f -- "$UNIT_PATH"
    systemctl daemon-reload
    printf '[OK] Stopped, disabled, and removed the systemd service.\n'
fi

if [[ -d $APP_DIR ]]; then
    rm -f -- "$APP_DIR"/{bot.py,database.py,requirements.txt,install.sh,update.sh,backup.sh,uninstall.sh,puppy-points.service,README.md,.gitignore,update-source}
    if [[ -d $APP_DIR/assets && ! -L $APP_DIR/assets ]]; then
        rm -f -- "$APP_DIR"/assets/{plus_1.png,plus_10.png,plus_50.png,plus_100.png,minus_1.png,minus_10.png,minus_50.png,minus_100.png}
        rmdir -- "$APP_DIR/assets" 2>/dev/null || true
    fi
    rm -rf -- "$APP_DIR/.venv"
    for directory in "$APP_DIR"/.venv.* "$APP_DIR"/.release.*; do
        if [[ -d $directory && ! -L $directory && ${directory##*/} =~ ^\.(venv|release)\.[[:alnum:]]{10}$ ]]; then
            rm -rf -- "$directory"
        fi
    done
fi

printf '[OK] Removed installed application files.\n'
printf 'Type delete-data to permanently delete the database, backups and local configuration, or press Enter to keep them: '
IFS= read -r answer || answer=
if [[ $answer == delete-data ]]; then
    rm -rf -- "$APP_DIR/data" "$APP_DIR/backups"
    rm -f -- "$APP_DIR/puppy-points.env"
    rmdir -- "$APP_DIR" 2>/dev/null || true
    printf '[OK] Deleted the database, transaction history, backups and local configuration.\n'
else
    printf '[OK] Preserved any database, transaction history, backups and local configuration in %s.\n' "$APP_DIR"
fi
printf 'Uninstall complete.\n'
