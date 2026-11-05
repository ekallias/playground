#!/usr/bin/env python3
"""
ingest_prep.py - sort a messy document/photo library into staging folders for
paperless-ngx (documents) and Immich (photos/videos).

    <out>/paperless/         -> point paperless's consume dir here (or copy contents in)
    <out>/immich/            -> upload with `immich upload --recursive` or immich-go
    <out>/review/            -> things the script couldn't confidently classify
    <out>/manifest.csv       -> cumulative LEDGER of everything staged (used by --cleanup)
    <out>/last_run_report.csv-> full report of the most recent run (incl. skipped files)

Safe by default: DRY RUN unless you pass --execute, and files are COPIED unless
you pass --move. Exact duplicates (SHA-256) are skipped. Re-running is safe:
files already in the ledger are not staged twice.

If a word-processor file (.doc/.docx/.odt/.rtf) has a PDF with the same name in
the same folder, only the PDF is staged; the other file is left alone.

Workflow
    1. python ingest_prep.py SRC OUT --plan          # writes OUT/selection.txt (folder checklist)
    2. edit OUT/selection.txt: put an x in [ ] for what you want ingested
    3. python ingest_prep.py SRC OUT                 # preview (uses selection.txt automatically)
    4. python ingest_prep.py SRC OUT --execute       # stage copies
    5. later: python ingest_prep.py SRC OUT --cleanup [--execute]   # delete ingested originals

Without a selection file everything is considered. Use --ignore-selection to bypass it.

Only stdlib is required. `pip install pillow` enables EXIF-based detection of
real photos vs. scans/screenshots (strongly recommended).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False

# What paperless-ngx can consume (office/eml/html need Tika+Gotenberg enabled).
PAPERLESS_EXT = {
    ".pdf", ".txt", ".csv", ".md", ".tif", ".tiff", ".gif", ".webp",
    ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".eml", ".msg", ".html", ".htm",
}
# Word-processor formats that are left alone when a same-named PDF exists.
WORDPROC_EXT = {".doc", ".docx", ".odt", ".rtf"}
# Images that could be either a photo or a scanned document.
AMBIGUOUS_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
# Always photos/videos.
IMMICH_EXT = {
    ".heic", ".heif", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf",
    ".raf", ".rw2", ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm",
    ".mts", ".m2ts",
}
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".picasa.ini", ".nomedia"}
PHOTO_EXIF = {"Make", "Model", "DateTimeOriginal", "GPSInfo"}

LEDGER = "manifest.csv"
REPORT = "last_run_report.csv"
LEDGER_FIELDS = ["source", "destination", "category", "sha256", "action", "staged_at"]
INGEST_CATEGORIES = {"paperless", "immich"}  # review/ is never treated as ingested


@dataclass
class Decision:
    category: str  # paperless | immich | review | skip
    reason: str


def has_camera_exif(path: Path) -> bool:
    if not HAVE_PIL:
        return False
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            names = {TAGS.get(k, k) for k in exif}
            # DateTimeOriginal / GPS live in sub-IFDs
            for ifd in (0x8769, 0x8825):
                try:
                    names |= {TAGS.get(k, k) for k in exif.get_ifd(ifd)}
                except Exception:
                    pass
                if ifd == 0x8825 and exif.get(0x8825):
                    names.add("GPSInfo")
            return bool(names & PHOTO_EXIF)
    except Exception:
        return False


def classify(path: Path, ambiguous: str) -> Decision:
    ext = path.suffix.lower()
    if path.name.lower() in JUNK_NAMES or path.name.startswith("._"):
        return Decision("skip", "junk file")
    if ext in PAPERLESS_EXT:
        return Decision("paperless", f"document type {ext}")
    if ext in IMMICH_EXT:
        return Decision("immich", f"photo/video type {ext}")
    if ext in AMBIGUOUS_IMG_EXT:
        if has_camera_exif(path):
            return Decision("immich", "image with camera EXIF")
        why = "image without camera EXIF" if HAVE_PIL else "image (Pillow missing, can't inspect EXIF)"
        return Decision(ambiguous, why)
    return Decision("review", f"unknown type {ext or '(none)'}")


def parse_cutoff(value: str) -> float:
    """'2015-01-31' -> that date; '10y' / '6m' / '90d' -> that long ago. Returns epoch seconds."""
    m = re.fullmatch(r"(\d+)\s*([dmy])", value.strip().lower())
    if m:
        days = int(m.group(1)) * {"d": 1, "m": 30.4375, "y": 365.25}[m.group(2)]
        return time.time() - days * 86400
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").timestamp()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}': use YYYY-MM-DD or an age like 90d, 6m, 10y")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def unique_dest(dest: Path, digest: str) -> Path:
    """Free path for dest. An existing file with identical content is reused (re-runs)."""
    if not dest.exists():
        return dest
    if dest.is_file() and sha256(dest) == digest:
        return dest
    return dest.with_name(f"{dest.stem}_{digest[:8]}{dest.suffix}")


def safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


PLAN_NAME = "selection.txt"


def list_files(src: Path, out: Path) -> list[Path]:
    return sorted(p for p in src.rglob("*") if p.is_file() and out not in p.parents)


def outside_age(mtime: float, args) -> bool:
    return bool((args.newer_than and mtime < args.newer_than) or (args.older_than and mtime > args.older_than))


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def make_plan(src: Path, out: Path, args) -> int:
    """Write an editable folder checklist. Counts are recursive."""
    plan = (args.selection or out / PLAN_NAME).expanduser()
    if plan.exists() and not args.force:
        sys.exit(f"{plan} already exists and may contain your edits. "
                 "Use --force to overwrite it, or --selection OTHER_FILE.")
    stats: dict[str, dict[str, int]] = {}
    listed: dict[str, list[tuple[str, int, float]]] = {}
    for p in list_files(src, out):
        if p.name.lower() in JUNK_NAMES or p.name.startswith("._"):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if outside_age(st.st_mtime, args):
            continue
        ext = p.suffix.lower()
        kind = "docs" if ext in PAPERLESS_EXT else "photos" if ext in IMMICH_EXT | AMBIGUOUS_IMG_EXT else "other"
        parts = p.relative_to(src).parts[:-1]
        for i in range(min(len(parts), args.plan_depth) + 1):
            key = "/".join(parts[:i]) or "."
            s = stats.setdefault(key, {"docs": 0, "photos": 0, "other": 0, "size": 0})
            s[kind] += 1
            s["size"] += st.st_size
        # each file is shown under its deepest *listed* folder (full path, so deeper ones are clear)
        owner = "/".join(parts[:min(len(parts), args.plan_depth)]) or "."
        listed.setdefault(owner, []).append((p.relative_to(src).as_posix(), st.st_size, st.st_mtime))

    include_all = args.plan_default == "include"
    keys = sorted(stats, key=lambda k: () if k == "." else tuple(k.split("/")))
    entries: list[tuple[str, str | None]] = []  # (label, note); note None = plain comment line
    for k in keys:
        s = stats[k]
        depth = 0 if k == "." else k.count("/") + 1
        mark = "x" if (k == "." and include_all) else " "
        entries.append(("  " * depth + f"[{mark}] " + ("./" if k == "." else k + "/"),
                        f"docs {s['docs']}, photos {s['photos']}, other {s['other']}, {human_size(s['size'])}"))
        shown = sorted(listed.get(k, []))
        for rel_s, size, mt in shown[:args.plan_files]:
            entries.append(("  " * (depth + 1) + f"[ ] {rel_s}",
                            f"{human_size(size)}, {datetime.fromtimestamp(mt):%Y-%m-%d}"))
        if args.plan_files > 0 and len(shown) > args.plan_files:
            entries.append(("  " * (depth + 1) + f"# ... and {len(shown) - args.plan_files} more files here "
                            "(raise --plan-files to list them)", None))
    labelled = [len(label) for label, note in entries if note is not None]
    width = min(max(labelled), 90) + 2 if labelled else 0
    lines = [
        "# SELECTION - edit and save, then run ingest_prep.py SRC OUT again (add --execute to stage).",
        "#   [x]  ingest this folder and everything below it",
        "#   [-]  skip this folder and everything below it",
        "#   [ ]  no decision: inherits from the folder above (skipped if nothing above is ticked)",
        "# The most specific line wins, so you can tick 'Documents/' and put [-] on 'Documents/Old stuff/'.",
        "# Delete a line to leave that folder undecided. File lines work the same way:",
        "#   e.g.  [x] Documents/Taxes/2022/summary.pdf   or   [-] Documents/junk.pdf",
        "# Counts are recursive. docs = paperless-able, photos = images/videos, other = unknown types.",
        "",
    ]
    for label, note in entries:
        lines.append(label if note is None else label + " " * max(2, width - len(label)) + "# " + note)
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = stats.get(".", {"docs": 0, "photos": 0, "other": 0})
    print(f"wrote {plan}: {len(keys)} folders, {total['docs'] + total['photos'] + total['other']} files "
          f"(default: {'everything ticked, use [-] to exclude' if include_all else 'nothing ticked'}, "
          f"listed to depth {args.plan_depth})")
    print("Edit it, then run the same command without --plan.")
    return 0


def load_selection(path: Path) -> dict[str, bool]:
    rules: dict[str, bool] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"\[([ xX-]?)\]\s+(.*)$", line)
        if not m:
            sys.exit(f"{path}:{n}: expected '[x] path', '[-] path' or '[ ] path', got: {raw!r}")
        if m.group(1) in ("", " "):  # '[ ]' = no decision
            continue
        target = re.split(r"\s{2,}#", m.group(2), maxsplit=1)[0].strip().replace("\\", "/")
        while target.startswith("./"):
            target = target[2:]
        target = target.rstrip("/") or "."
        rules[target] = m.group(1) != "-"
    return rules


def is_selected(rel: Path, rules: dict[str, bool]) -> bool:
    """Most specific rule wins: the file itself, then its folders from deepest to the root."""
    parts = rel.parts
    for i in range(len(parts), 0, -1):
        key = "/".join(parts[:i])
        if key in rules:
            return rules[key]
    return rules.get(".", False)


def load_ledger(out: Path) -> list[dict]:
    p = out / LEDGER
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_ledger(out: Path, new_rows: list[dict]) -> None:
    if not new_rows:
        return
    p = out / LEDGER
    is_new = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if is_new:
            w.writeheader()
        w.writerows(new_rows)


def cleanup(src: Path, out: Path, execute: bool, include_dups: bool) -> int:
    """Delete source files that the ledger says were ingested. Everything else stays."""
    ledger = load_ledger(out)
    if not ledger:
        sys.exit(f"no ledger found at {out / LEDGER} - nothing to clean up")
    wanted = {"copied"} | ({"duplicate"} if include_dups else set())
    stats: dict[str, int] = {}
    report = []
    for r in ledger:
        if r["category"] not in INGEST_CATEGORIES or r["action"] not in wanted:
            continue
        p = (src / r["source"]).resolve()
        if src not in p.parents:
            status = "refused (path outside source)"
        elif not p.is_file():
            status = "already gone"
        else:
            try:
                same = sha256(p) == r["sha256"]
            except OSError as e:
                same, status = False, f"error: {e}"
            else:
                if not same:
                    status = "kept (changed since ingest)"
                elif execute:
                    try:
                        p.unlink()
                        status = "deleted"
                    except OSError as e:
                        status = f"error: {e}"
                else:
                    status = "would delete"
        key = status.split(" (")[0].split(":")[0]
        stats[key] = stats.get(key, 0) + 1
        report.append([r["source"], r["category"], r["action"], status])

    print(("CLEANUP EXECUTED" if execute else "CLEANUP DRY RUN (nothing deleted; add --execute)") + f" in {src}")
    for k, v in stats.items():
        print(f"  {k:<28}{v}")
    if execute:
        with (out / "cleanup_report.csv").open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerows([[datetime.now().isoformat(timespec="seconds"), *row] for row in report])
        print(f"log appended to {out / 'cleanup_report.csv'}")
        print("\nIf the source is an ownCloud data directory, rescan afterwards, e.g.:\n"
              "  sudo -u www-data php occ files:scan --all")
    else:
        for row in report[:25]:
            print(f"  [{row[3]}] {row[0]}")
        if len(report) > 25:
            print(f"  ... and {len(report) - 25} more")
    print("\nOnly delete originals after confirming they are really in paperless / Immich.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--execute", action="store_true", help="actually copy/move/delete (default: dry run)")
    ap.add_argument("--move", action="store_true", help="move instead of copy (originals are removed!)")
    ap.add_argument("--flat", action="store_true",
                    help="don't keep folder structure (default keeps it; paperless can turn "
                         "subfolders into tags, Immich/immich-go can turn them into albums)")
    ap.add_argument("--ambiguous-images", choices=["review", "paperless", "immich"], default="review",
                    help="where JPG/PNG without camera EXIF go (default: review)")
    ap.add_argument("--min-size", type=int, default=1, help="skip files smaller than N bytes (default 1)")
    ap.add_argument("--newer-than", type=parse_cutoff, metavar="DATE|AGE",
                    help="only files modified after this (e.g. 2015-01-01 or 10y); older files are skipped")
    ap.add_argument("--older-than", type=parse_cutoff, metavar="DATE|AGE",
                    help="only files modified before this (e.g. 30d); skips recent, possibly still-edited files")
    ap.add_argument("--keep-all-formats", action="store_true",
                    help="also stage .doc/.docx/.odt/.rtf files that have a same-named PDF")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete originals that the ledger says were ingested (dry run unless --execute)")
    ap.add_argument("--include-duplicates", action="store_true",
                    help="with --cleanup: also delete exact duplicates of ingested files")
    ap.add_argument("--plan", action="store_true",
                    help="write an editable folder checklist (OUT/selection.txt) and exit")
    ap.add_argument("--plan-depth", type=int, default=3, metavar="N",
                    help="with --plan: list folders down to this depth (default 3); deeper ones inherit")
    ap.add_argument("--plan-files", type=int, default=15, metavar="N",
                    help="with --plan: list up to N files per folder so you can see what's inside "
                         "(default 15, 0 = folders only)")
    ap.add_argument("--plan-default", choices=["exclude", "include"], default="exclude",
                    help="with --plan: 'exclude' (default) leaves everything unticked so you opt in; "
                         "'include' ticks only the root so everything is in and you opt out with [-]")
    ap.add_argument("--selection", type=Path, metavar="FILE",
                    help="selection file to write (--plan) or use (default: OUT/selection.txt if it exists)")
    ap.add_argument("--ignore-selection", action="store_true", help="consider everything, ignore selection.txt")
    ap.add_argument("--force", action="store_true", help="with --plan: overwrite an existing selection file")
    args = ap.parse_args()

    src, out = args.source.expanduser().resolve(), args.out.expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"source not a directory: {src}")
    if out == src or out in src.parents:
        sys.exit("out must not be the source folder or one of its parents")

    if args.cleanup:
        return cleanup(src, out, args.execute, args.include_duplicates)
    if args.plan:
        return make_plan(src, out, args)

    if not HAVE_PIL:
        print("note: Pillow not installed - JPG/PNG can't be checked for EXIF (pip install pillow)\n")

    ledger = load_ledger(out)
    ledger_keys = {(r["source"], r["category"], r["action"]) for r in ledger}
    # content already staged for paperless/immich in earlier runs
    seen: dict[str, str] = {
        r["sha256"]: r["source"]
        for r in ledger
        if r["category"] in INGEST_CATEGORIES and r["action"] in ("copied", "moved")
    }
    new_ledger: list[dict] = []
    counts: dict[str, int] = {}
    rows = []

    files = list_files(src, out)
    sel_path = None
    if not args.ignore_selection:
        sel_path = args.selection.expanduser() if args.selection else (out / PLAN_NAME)
        if args.selection and not sel_path.is_file():
            sys.exit(f"selection file not found: {sel_path}")
        if not sel_path.is_file():
            sel_path = None
    if sel_path:
        rules = load_selection(sel_path)
        print(f"selection: {sel_path} ({sum(rules.values())} ticked, {len(rules) - sum(rules.values())} excluded)")
        for key in rules:
            if key != "." and not (src / key).exists():
                print(f"  warning: selection line not found in source: {key}")
        kept = [p for p in files if is_selected(p.relative_to(src), rules)]
        if len(kept) != len(files):
            counts["not selected"] = len(files) - len(kept)
        files = kept
    else:
        print("no selection file in use - considering ALL files (create one with --plan)")
    pdf_by_name = {
        (p.parent, p.stem.lower()): p.name
        for p in files
        if p.suffix.lower() == ".pdf" and safe_size(p) >= args.min_size
    }

    for path in files:
        rel = path.relative_to(src)
        rel_s = rel.as_posix()
        try:
            if path.stat().st_size < args.min_size:
                rows.append([rel, "", "skip", "empty/too small", "", "skipped"]); counts["skip"] = counts.get("skip", 0) + 1
                continue
            mtime = path.stat().st_mtime
            if outside_age(mtime, args):
                when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                rows.append([rel, "", "skip", f"outside age cutoff (modified {when})", "", "skipped"])
                counts["age cutoff"] = counts.get("age cutoff", 0) + 1
                continue
            if not args.keep_all_formats and path.suffix.lower() in WORDPROC_EXT:
                pdf = pdf_by_name.get((path.parent, path.stem.lower()))
                if pdf:
                    rows.append([rel, "", "skip", f"left in place: superseded by {pdf}", "", "skipped"])
                    counts["superseded"] = counts.get("superseded", 0) + 1
                    continue
            d = classify(path, args.ambiguous_images)
            if d.category == "skip":
                rows.append([rel, "", "skip", d.reason, "", "skipped"]); counts["skip"] = counts.get("skip", 0) + 1
                continue
            digest = sha256(path)
        except OSError as e:
            rows.append([rel, "", "error", str(e), "", "error"]); counts["error"] = counts.get("error", 0) + 1
            continue

        if digest in seen:
            first = seen[digest]
            if first == rel_s:
                rows.append([rel, "", d.category, "already staged in an earlier run", digest, "skipped"])
                counts["already staged"] = counts.get("already staged", 0) + 1
            else:
                rows.append([rel, "", d.category, f"duplicate of {first}", digest, "skipped"])
                counts["duplicate"] = counts.get("duplicate", 0) + 1
                if (rel_s, d.category, "duplicate") not in ledger_keys:
                    new_ledger.append({"source": rel_s, "destination": "", "category": d.category,
                                       "sha256": digest, "action": "duplicate",
                                       "staged_at": datetime.now().isoformat(timespec="seconds")})
            continue
        seen[digest] = rel_s

        dest = out / d.category / (Path(path.name) if args.flat else rel)
        dest = unique_dest(dest, digest)
        action = "planned"
        if args.execute:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                (shutil.move if args.move else shutil.copy2)(path, dest)
                action = "moved" if args.move else "copied"
            except OSError as e:
                action = f"error: {e}"
        rows.append([rel, dest.relative_to(out), d.category, d.reason, digest, action])
        counts[d.category] = counts.get(d.category, 0) + 1
        if action in ("copied", "moved") and (rel_s, d.category, action) not in ledger_keys:
            new_ledger.append({"source": rel_s, "destination": dest.relative_to(out).as_posix(),
                               "category": d.category, "sha256": digest, "action": action,
                               "staged_at": datetime.now().isoformat(timespec="seconds")})

    if args.execute:
        out.mkdir(parents=True, exist_ok=True)
        append_ledger(out, new_ledger)
        with (out / REPORT).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["source", "destination", "category", "reason", "sha256", "action"])
            w.writerows(rows)

    print(("EXECUTED" if args.execute else "DRY RUN (nothing written; add --execute)") + f" -> {out}")
    for k in ("paperless", "immich", "review", "duplicate", "already staged", "superseded",
              "not selected", "age cutoff", "skip", "error"):
        if k in counts:
            print(f"  {k:<16}{counts[k]}")
    if not args.execute:
        for r in rows[:25]:
            print(f"  [{r[2]:<9}] {r[0]}  ({r[3]})")
        if len(rows) > 25:
            print(f"  ... and {len(rows) - 25} more")
    else:
        print(f"ledger: {out / LEDGER}   report: {out / REPORT}")
        print("\nNext steps:\n"
              f"  paperless: copy/mount {out/'paperless'} into your consume dir "
              "(set PAPERLESS_CONSUMER_RECURSIVE=true and PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS=true for folder tags)\n"
              f"  immich:    immich upload --recursive {out/'immich'}   (add --album to use folder names as albums)\n"
              "  later:     rerun with --cleanup to remove the originals that were ingested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
