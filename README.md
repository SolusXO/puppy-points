# Puppy Points

Puppy Points is a Discord bot for manually awarding and removing points. It runs as a dedicated non-root user inside a Debian 12 or Debian 13 Proxmox LXC container. Each Discord server has its own balances and transaction history in SQLite. A member's balance starts at zero, may go below zero, and follows their Discord user ID through name changes.

## 1. Create the Proxmox container

1. In the Proxmox web interface, select the node and a storage that accepts container templates, typically **local**. Open **CT Templates** → **Templates** and download a **Debian 12 standard** or **Debian 13 standard** template.
2. Click **Create CT**. In **General**, choose an unused **CT ID** such as `100`, enter `puppy-points` as the hostname, set a root password, check **Unprivileged container**, and clear **Nesting**.
3. In **Template**, select the Debian system template you downloaded. Set the container options below, confirm the configuration, and create the container.
4. Select the new container. In **Options**, edit **Start at boot** and set it to **Yes**. If needed, check **Options** → **Features** to verify that **Nesting** is disabled. Start the container.

| Setting | Value |
| --- | --- |
| Hostname | `puppy-points` |
| Operating system | Debian 12 or Debian 13 |
| CPU | 1 core |
| Memory | 256 MiB |
| Swap | 256 MiB |
| Root disk | 2 GiB on a storage that accepts container disks |
| Network | LAN bridge, usually `vmbr0`, with IPv4 set to DHCP |
| DNS | Use host defaults or your network's DNS servers |
| Unprivileged container | Yes |
| Start at boot | Yes |
| Nesting | Disabled |
| Inbound ports | None |

The container needs DNS, outbound access to Debian's repositories and the Python package index during setup, and outbound HTTPS and WebSocket connections to Discord while running. The bot runs without a web server, port forwarding, a public IP, or a GPU. [Proxmox Container Toolkit guide](https://github.com/proxmox/pve-docs/blob/master/pct.adoc)

## 2. Create the Discord application and copy staff role IDs

1. Open the Discord Developer Portal. Under **Applications**, choose **New Application**, name it **Puppy Points**, and select **Create**. Discord provisions a bot user for new applications. Open the **Bot** tab to see it. [Discord Developer Portal](https://discord.com/developers/applications)
2. In **Bot** → **Token**, select **Reset Token** to generate a bot token. Complete any verification prompt, copy the token immediately, and store it somewhere private. Enter it only at the installer's hidden prompt. Keep all **Privileged Gateway Intents** disabled. [Discord bot setup guide](https://docs.discord.com/developers/quick-start/getting-started)
3. Under **General Information**, leave **Interactions Endpoint URL** blank so interactions arrive over the Discord Gateway. [Discord interactions overview](https://docs.discord.com/developers/interactions/overview#preparing-for-interactions)
4. Open **Installation**. Select **Guild Install** as the only installation context and set **Install Link** to **Discord Provided Link**. Under **Default Install Settings** → **Guild Install**, select the `bot` and `applications.commands` scopes. Save the changes. [Discord install settings](https://docs.discord.com/developers/quick-start/getting-started#choosing-installation-contexts)
5. In the same **Guild Install** settings, give the bot **View Channels**, **Send Messages**, **Embed Links**, and **Attach Files**. Leave **Administrator** unchecked. If you use the bot in threads, grant **Send Messages in Threads** where it should respond. Channel overrides must permit the relevant actions regardless of the permissions granted when inviting the bot. [Discord permissions reference](https://docs.discord.com/developers/topics/permissions)
6. In the Discord desktop or web client, enable **Developer Mode** in **User Settings**. Search settings for **Developer Mode** if you do not see it under **Advanced** or **Developer**. In each server, open **Server Settings** → **Roles**, right-click each designated staff role, and choose **Copy Role ID**. Keep the numeric staff role IDs for installation. [Discord Developer Mode instructions](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID)

## 3. Prepare and transfer the project

For a packaged download, open **Releases** on the GitHub repository and download `puppy-points.tar.gz` from the release's **Assets** list.

The eight original PNGs are included in the project's `assets/` directory. Use the table to verify that each filename matches the sign and amount shown in the image. Keep the original file contents when transferring or updating the release.

| Amount shown | File |
| --- | --- |
| +1 | `assets/plus_1.png` |
| +10 | `assets/plus_10.png` |
| +50 | `assets/plus_50.png` |
| +100 | `assets/plus_100.png` |
| -1 | `assets/minus_1.png` |
| -10 | `assets/minus_10.png` |
| -50 | `assets/minus_50.png` |
| -100 | `assets/minus_100.png` |

This transfer uses the Proxmox host's SSH service and requires no incoming connection to the container. If you have the packaged `puppy-points.tar.gz`, use it directly. If you have the source directory, open a terminal in the directory containing `puppy-points` and make the archive.

```bash
tar -czf puppy-points.tar.gz puppy-points
```

Run the upload from the directory containing the archive. Replace `192.168.1.10` with your Proxmox host's LAN address.

```bash
scp puppy-points.tar.gz root@192.168.1.10:/root/
```

On the **Proxmox host** shell, replace `100` with your container ID and run the following. Use the node that runs the container.

```bash
pct push 100 /root/puppy-points.tar.gz /root/puppy-points.tar.gz
pct enter 100
```

You are now root **inside the Debian container**. Unpack the release there. Keep `/root/puppy-points` as the initial update source.

```bash
tar -xzf /root/puppy-points.tar.gz -C /root
cd /root/puppy-points
```

[Proxmox `pct` command reference](https://github.com/proxmox/pve-docs/blob/master/generated/pct.1-synopsis.adoc)

## 4. Install the bot

Run the installer in the container from `/root/puppy-points` as root.

```bash
bash install.sh
```

Paste the bot token and press Enter. The prompt keeps what you type or paste off the screen. Then enter the staff role IDs separated by commas or spaces. A blank role list allows only Discord administrators to award points, remove points, and see transaction history.

The script installs `python3`, `python3-venv`, and `ca-certificates`, creates the `puppy-points` Linux user, installs the application and its pinned Discord dependency, and initializes the database. It enables the `puppy-points` systemd service and waits for successful Discord authentication, global slash command registration, and the first Gateway ready event. The bot can reach that point before it is invited to a server.

An existing configuration and database are reused on reinstall. The configuration is `/opt/puppy-points/puppy-points.env`, owned by root with mode `0600`. The SQLite database is created at `/opt/puppy-points/data/puppy_points.db`.

## 5. Invite the bot and verify installation

1. In your Discord application's **Installation** tab, copy the install link. Open it in your browser, choose **Add to server**, choose the target server, then select **Authorize**. The account authorizing the invite needs **Manage Server** in that Discord server. [Discord server installation guide](https://docs.discord.com/developers/quick-start/getting-started#install-to-server)
2. In the container, verify the service and recent log entries.

   ```bash
   systemctl is-active puppy-points
   systemctl is-enabled puppy-points
   journalctl -u puppy-points -n 50 --no-pager
   ```

   The first two commands should print `active` and `enabled`. The journal should show that database initialization, command registration, and the Discord connection succeeded.
3. In a Discord server channel where the bot can send messages and attachments, type `/puppy points` and submit it. A new member has zero points and has no rank until their first transaction. An authorized staff member can verify an image receipt by running `/puppy add` in a test channel.

## Using the slash commands

Type `/puppy` in a Discord server and select a subcommand. For commands that accept `member`, pick a server member using Discord's selector. The `amount` field provides the exact valid choices. A `reason` can be left blank.

| Command | Who can use it | What it does |
| --- | --- | --- |
| `/puppy add member amount [reason]` | Administrator or configured staff | Adds `+1`, `+10`, `+50`, or `+100`. Posts a public receipt with the matching original image. |
| `/puppy subtract member amount [reason]` | Administrator or configured staff | Applies `-1`, `-10`, `-50`, or `-100`. Posts a public receipt with the matching original image. |
| `/puppy points [member]` | Everyone | Shows the selected member's balance and rank. Leave `member` blank to view your own. |
| `/puppy leaderboard` | Everyone | Shows ranked balances, highest first, with medals for the first three. Use **Previous Page** and **Next Page** to browse 10 members at a time. The footer shows the current and total page count. |
| `/puppy history member` | Administrator or configured staff | Privately shows the selected member's transactions with the signed amount, previous and resulting balances, who made the change, the date and time, and any reason. |

The leaderboard includes members with at least one recorded transaction, including members whose balance has since returned to zero. Tied balances are ordered by ascending numeric user ID. If a member's display name is unavailable, their numeric user ID appears on the leaderboard. Browsing pages sends no member mentions. History shows the newest transactions first, 10 per page. Only the person who ran the command can use its page buttons, which expire after three minutes of inactivity. Long reasons may be shortened in embeds. SQLite stores the full text of each recorded reason.

## Updating the bot

The update source is a complete local release directory on the container. At install time the installer records the source path in `/opt/puppy-points/update-source`. To use the usual update command, replace the contents of that external source directory with the new release, including the eight original PNGs. The source and the installed directory must be different paths.

Run the update as root inside the container.

```bash
cd /opt/puppy-points
bash update.sh
```

You can also transfer a release archive using the same `scp` and `pct push` steps as for installation, then unpack it into a new directory. On success the updater records that path for subsequent runs.

```bash
mkdir /root/puppy-points-new
tar -xzf /root/puppy-points.tar.gz --strip-components=1 -C /root/puppy-points-new
cd /opt/puppy-points
bash update.sh /root/puppy-points-new
```

The updater backs up the database before applying application changes, preserves the token and staff roles, and checks service readiness after restarting. If a release fails to start, the updater attempts to restore the previous application files and service state. Inspect service status and the journal before trying again.

## Creating and restoring backups

### Create a backup

Run this as root inside the container. The bot can stay online. The script uses SQLite's backup API and prints the timestamped file in `/opt/puppy-points/backups/`. The snapshot includes balances and history. It contains no credentials.

```bash
cd /opt/puppy-points
bash backup.sh
```

Keep a copy of important backups outside the container so they survive a container or storage failure. To retrieve a backup through Proxmox, use `pct pull` in the host shell with the container ID, the absolute backup file path inside the container, and the destination file path on the host. [Proxmox `pct` command reference](https://github.com/proxmox/pve-docs/blob/master/generated/pct.1-synopsis.adoc)

### Restore a backup

Restoring reverts all servers' balances and history to the snapshot. If moving to a new container, first install the bot there and place the snapshot in `/opt/puppy-points/backups/` as root. In the installed container, run the block below as root. At the prompt, enter only the filename shown in the list. The commands verify the selected snapshot, stop the bot, and try to back up the current database. If SQLite cannot back it up, the commands preserve its raw files in a separate root-only directory before installing the chosen snapshot. If any step fails after the stop, check the error before starting the bot again.

```bash
(
  set -Eeuo pipefail
  umask 077
  cd /opt/puppy-points
  exec 9>>/run/puppy-points-maintenance.lock
  flock -n 9 || { echo 'Another Puppy Points maintenance operation is running.' >&2; exit 1; }
  ls -1 backups/
  read -r -p 'Backup filename: ' snapshot
  case "$snapshot" in
    ''|*/*) echo 'Enter a filename from the backups list.' >&2; exit 1 ;;
  esac
  backup="/opt/puppy-points/backups/$snapshot"
  db=/opt/puppy-points/data/puppy_points.db
  if [[ ! -f $backup || -L $backup || ! -d data || -L data || -L $db ]]; then
    echo 'Choose a regular backup file and check the installed data directory.' >&2
    exit 1
  fi
  python3 - "$backup" <<'PY'
import sqlite3
import sys
from pathlib import Path

uri = Path(sys.argv[1]).resolve(strict=True).as_uri() + '?mode=ro'
with sqlite3.connect(uri, uri=True) as connection:
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise SystemExit('The snapshot failed the SQLite integrity check.')
    tables = {
        name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if not {'balances', 'transactions'} <= tables:
        raise SystemExit('The snapshot does not contain the Puppy Points tables.')
PY
  systemctl stop puppy-points
  . ./backup.sh
  if [[ -f $db ]] && ! backup_database; then
    rescue=$(mktemp -d /opt/puppy-points/backups/raw-pre-restore.XXXXXX)
    for source in "$db" "$db-journal" "$db-wal" "$db-shm"; do
      if [[ -f $source && ! -L $source ]]; then
        install -m 0600 -- "$source" "$rescue/${source##*/}"
      fi
    done
    printf 'Preserved the previous database files in %s\n' "$rescue"
  fi
  staged=$(mktemp /opt/puppy-points/data/.restore.XXXXXX)
  trap 'rm -f -- "$staged"' EXIT
  install -o puppy-points -g puppy-points -m 0600 -- "$backup" "$staged"
  rm -f -- "$db-journal" "$db-wal" "$db-shm"
  mv -fT -- "$staged" "$db"
  systemctl start puppy-points
)
```

Verify the service is running and use `/puppy points` and an authorized `/puppy history` invocation to check the restored data.

## Managing the service

Run these commands as root in the container. Use `Ctrl+C` to exit the live journal view.

```bash
systemctl start puppy-points
systemctl stop puppy-points
systemctl restart puppy-points
systemctl status puppy-points
systemctl enable puppy-points
journalctl -u puppy-points -f
```

To change the staff roles after installation, use a root-only text editor to edit `PUPPY_STAFF_ROLE_IDS` in `/opt/puppy-points/puppy-points.env`. Set it to a comma-separated list of numeric role IDs, or leave it empty for administrator-only access. Keep the file owned by root with mode `0600`, then restart the service.

## Uninstalling

Run as root inside the container.

```bash
cd /opt/puppy-points
bash uninstall.sh
```

Type `uninstall` when prompted to stop, disable, and remove the service and application. Press Enter at the separate data prompt to preserve the database, transaction history, backups, and local configuration. Type `delete-data` only if you intend to permanently delete these files. To remove the bot from Discord, use the server's integration settings or remove the bot from the member list.

## Troubleshooting

Start with the installer or updater's error and the latest service journal.

```bash
systemctl status puppy-points --no-pager
journalctl -u puppy-points -n 100 --no-pager
```

| Symptom | What to check |
| --- | --- |
| The installer reports missing image files | Check that the originals are in `assets/` in the transferred source directory. Use the case-sensitive filenames in the image table and rerun the installer. |
| Discord rejects authentication | In the Developer Portal, open the application's **Bot** tab and reset its token. As root, open `/opt/puppy-points/puppy-points.env` in a console text editor and replace the `DISCORD_TOKEN` value while preserving the file's permissions. If the first install failed and rolled back the service files, return to the source directory and rerun `bash install.sh`. If the service is already installed, run `systemctl restart puppy-points`. Enter credentials only in the hidden installer prompt or the protected configuration. |
| The service times out connecting or syncing commands | Check the container's IP address, route, and DNS, and allow outbound access to Discord over TCP 443. `getent hosts discord.com gateway.discord.gg` checks DNS resolution. Read the journal for errors from Discord. |
| `/puppy` is missing in Discord | Verify the bot was installed to the server with both scopes and successful registration appears in the journal. Check the channel's **Use Application Commands** permission for the invoking member and the server's **Integrations** settings. Refresh the Discord client if it still shows a cached command list. |
| Discord says you are unauthorized | The caller needs the server's **Administrator** permission or one of the configured staff roles for `add`, `subtract`, and `history`. Compare the copied role ID with the configuration and restart after edits. |
| A channel fails to show transaction images | Check the bot's channel-level permissions for **View Channel**, **Send Messages**, **Embed Links**, and **Attach Files**. In a thread, check **Send Messages in Threads**. If a receipt fails to publish after a change, check `/puppy history` before submitting a new point transaction. |
| SQLite will not open or write | Check available disk space with `df -h /opt/puppy-points`. The service user `puppy-points` needs ownership of `/opt/puppy-points/data/` and write access to the database. Preserve the database while diagnosing the error. |
| The installer cannot reach systemd | Run the script as root inside the running Debian container. `ps -p 1 -o comm=` should print `systemd`. The Proxmox host shell is used for `pct` commands. |

## License

The software and its documentation are available under the [MIT License](LICENSE). See the [artwork notice](assets/README.md) for the original PNGs.
