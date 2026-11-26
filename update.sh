#!/usr/bin/env bash

set -Eeuo pipefail
set +x
umask 077
export LC_ALL=C
if (( $# > 1 )); then
    printf 'Usage: bash update.sh [SOURCE_DIRECTORY]\n' >&2
    exit 1
fi
if [[ ! -f /opt/puppy-points/install.sh || ! -f /opt/puppy-points/backup.sh ]]; then
    printf '[ERROR] Install Puppy Points before running the updater.\n' >&2
    exit 1
fi
# shellcheck source=install.sh
. /opt/puppy-points/install.sh
# shellcheck source=backup.sh
. /opt/puppy-points/backup.sh

printf '%s\n' 'Puppy Points Bot Updater'
require_root_and_systemd
lock_maintenance
[[ -f $CONFIG_PATH && ! -L $CONFIG_PATH && -s $CONFIG_PATH && -f $UNIT_PATH && ! -L $UNIT_PATH ]] ||
    die 'The installed service or configuration is missing. Rerun install.sh from a complete release.'
if (( $# == 1 )); then
    requested_source=$1
elif [[ -f $SOURCE_PATH && ! -L $SOURCE_PATH ]] && IFS= read -r requested_source <"$SOURCE_PATH"; then
    [[ -n $requested_source ]] || die 'Run bash update.sh /path/to/complete-release to set the update source.'
else
    die 'Run bash update.sh /path/to/complete-release to set the update source.'
fi
validate_source "$requested_source"
backup_database
stage_release
activate_release update
save_update_source
printf '\nUpdate successful. Discord is connected and commands are registered.\n'
printf 'Daily use resumes with the existing database and configuration.\n'
