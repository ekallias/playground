# Paperless / Immich Ingest Prep

Written 2026-11-05.

`ingest_prep.py` sorts a messy document library (here: an ownCloud VM) into staging folders for
paperless-ngx (documents) and Immich (photos/videos). Python 3.8+, stdlib only; `pip install pillow`
is optional but recommended (EXIF check to tell photos from scans).

## What it does

- Sorts files into `OUT/paperless/`, `OUT/immich/` and `OUT/review/` by type. JPG/PNG with camera
  EXIF go to Immich; without EXIF they go to `review/` (or use `--ambiguous-images paperless|immich`).
- Skips exact duplicates (SHA-256 of the content, not the filename). Same name with different content
  is kept and renamed with a hash suffix.
- If `x.pdf` and `x.docx`/`.doc`/`.odt`/`.rtf` are in the same folder, only the PDF is staged and the
  other file is left alone (`--keep-all-formats` disables this).
- Age filters: `--newer-than` / `--older-than`, each a date (`2015-01-01`) or an age (`90d`, `6m`, `10y`).
- Dry run by default, copies by default. `--execute` writes, `--move` removes originals (do not use
  `--move` on an ownCloud data directory).

## Workflow

```bash
python ingest_prep.py SRC OUT --plan        # 1. writes OUT/selection.txt (folder + file checklist)
# 2. edit OUT/selection.txt
python ingest_prep.py SRC OUT               # 3. preview
python ingest_prep.py SRC OUT --execute     # 4. stage copies
python ingest_prep.py SRC OUT --cleanup     # later: preview deleting ingested originals
python ingest_prep.py SRC OUT --cleanup --execute
```

### The selection file

`--plan` lists folders (to `--plan-depth`, default 3) with recursive counts, and up to `--plan-files`
(default 15) files per folder with size and date.

| Mark  | Meaning                                                                 |
| ----- | ----------------------------------------------------------------------- |
| `[x]` | ingest this folder/file and everything below it                         |
| `[-]` | skip this folder/file and everything below it                           |
| `[ ]` | no decision: inherits from the folder above (skipped if none is ticked) |

The most specific line wins, so tick `Docs/` and put `[-]` on `Docs/Old stuff/`. Nothing is ticked by
default; `--plan-default include` ticks only the root so you opt out with `[-]`. `--plan` refuses to
overwrite an existing file without `--force`. The normal run uses `OUT/selection.txt` automatically
(`--ignore-selection` bypasses it).

### Ledger and cleanup

Every `--execute` run appends to `OUT/manifest.csv` (the ledger); `OUT/last_run_report.csv` is the full
report of the latest run. Re-runs do not stage files twice. `--cleanup` deletes only originals the
ledger lists as staged for paperless/Immich, and only if their hash still matches (edited files are
kept). Duplicates of ingested files are removed only with `--include-duplicates`. It cannot know whether
paperless/Immich actually ingested a file, so check first. After deleting from an ownCloud data
directory, rescan: `sudo -u www-data php occ files:scan --all`.

Deleting `OUT` is safe if you never used `--move` (everything in it is a copy). To start over but keep
your selection, delete `paperless/`, `immich/`, `review/`, `manifest.csv` and `last_run_report.csv`
together; deleting only the staged files makes the ledger treat them as "already staged".

## Pushing to paperless without folder tags

1. Paperless ignores subfolders of the consume folder unless `PAPERLESS_CONSUMER_RECURSIVE=true`
   (leave `PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS` unset). Restart paperless afterwards. Alternatively stage
   with `--flat`.
2. Pilot with one small folder, check the result in the UI, then send the rest in batches of a few hundred.
3. Paperless rejects exact duplicates by checksum on its own.

## Samba share for the consume folder (Proxmox LXC, script install)

In this container paperless runs as `root`, consume is `/opt/paperless_data/consume` (owner `root:root`),
and there is no `paperless` user.

```bash
apt update && apt install -y samba
useradd -M -s /usr/sbin/nologin ingest
smbpasswd -a ingest
chown ingest:ingest /opt/paperless_data/consume
```

Append to `/etc/samba/smb.conf`:

```
[Paperless-Consume]
   path = /opt/paperless_data/consume
   read only = no
   guest ok = no
   valid users = ingest
   create mask = 0664
   directory mask = 0775
   hosts allow = 192.168.1.0/24 192.168.2.0/24 127.0.0.1
```

```bash
testparm -s && systemctl restart smbd && systemctl enable smbd
```

Files copied in are owned by `ingest`; paperless (root) can read and delete them. Do not use
`force user = paperless` unless that user exists, otherwise login works but opening the share fails.
`192.168.1.255` is a broadcast address, so use the `/24` networks in `hosts allow`.

### Windows gotchas

Windows allows one login per server name. A session already open to the same server as another user
(for example `root` on a `Paperless-Archive` share) gives "Multiple connections to a server ... using
more than one user name". Clear it, then map the share:

```bat
net use * /delete
net use Z: \\192.168.1.32\Paperless-Consume /user:ingest *
```

Debugging: the PAM lines in `journalctl -u smbd` only show that login worked; the reason a share cannot
be opened is in `/var/log/samba/log.smbd`.
