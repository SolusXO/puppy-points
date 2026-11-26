#!/usr/bin/env bash

backup_database() {
    local root_dir=/opt/puppy-points backup_dir stamp destination
    if (( EUID != 0 )); then
        printf '[ERROR] Run the backup as root inside the Debian container.\n' >&2
        return 1
    fi
    backup_dir=$root_dir/backups
    if [[ -L $root_dir || -L $backup_dir || ! -x $root_dir/.venv/bin/python || ! -f $root_dir/database.py ]]; then
        printf '[ERROR] A regular Puppy Points installation and backup directory are required.\n' >&2
        return 1
    fi
    install -d -o root -g root -m 0700 -- "$backup_dir" || return 1
    stamp=$(date -u +%Y%m%dT%H%M%S.%NZ) || return 1
    destination=$backup_dir/puppy_points_$stamp.db
    if ! "$root_dir/.venv/bin/python" -B "$root_dir/database.py" backup "$destination"; then
        printf '[ERROR] SQLite backup failed. Review the database error above.\n' >&2
        return 1
    fi
    printf '[OK] Backup completed: %s\n' "$destination"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -Eeuo pipefail
    umask 077
    if (( EUID != 0 || $# != 0 )); then
        printf '[ERROR] Run bash backup.sh as root inside the Debian container.\n' >&2
        exit 1
    fi
    exec 9>>/run/puppy-points-maintenance.lock
    if ! flock -n 9; then
        printf '[ERROR] Another Puppy Points maintenance operation is running.\n' >&2
        exit 1
    fi
    backup_database
fi
