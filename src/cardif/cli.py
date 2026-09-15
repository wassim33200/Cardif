"""Command line interface.

    cardif scan       data/                 what the folders and filenames say
    cardif profile    data/ -o headers.xlsx every header ever seen
    cardif run        data/ --audit a.xlsx  process without writing anything
    cardif commit     data/                 process and append to the warehouse
    cardif status                           what the warehouse already holds
    cardif export                           regenerate complete Excel deliveries

Every command defaults to *not* writing: ``run`` is the dry pass and ``commit`` is the
only one that changes the warehouse.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .audit import write_audit
from .config import load_config
from .filemeta import scan
from .normalize import normalize_header
from .pipeline import commit as commit_run
from .pipeline import profile_headers, run
from .store import SchemaContractError, Warehouse, WarehouseCorrupt


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, help="folder containing the bank folders")
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--produit", "--profile", dest="profile", default=None,
                        help="force a product instead of reading it from the filename")
    parser.add_argument("--no-model", action="store_true", help="skip the local model")
    parser.add_argument(
        "--overrides", type=Path,
        help="YAML mapping of source headers to canonical fields (or __ignore__)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="print only the summary, errors and output paths",
    )


def _load_overrides(path: Path | None, config) -> dict[str, str] | None:
    if path is None:
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and "mappings" in raw:
        raw = raw["mappings"]
    if not isinstance(raw, dict):
        raise ValueError("override file must contain a YAML mapping")
    allowed = set(config.schema_.fields) | {"__ignore__"}
    overrides = {normalize_header(str(k)): str(v) for k, v in raw.items()}
    invalid = sorted({value for value in overrides.values() if value not in allowed})
    if invalid:
        raise ValueError(
            f"unknown override fields {invalid}; use a canonical field from schema.yaml"
        )
    return overrides


def _print_details(outcome) -> None:
    """Print the complete per-file decision trail formerly hidden behind the UI."""
    for result in outcome.results:
        status = "OK" if result.ok else "ERROR"
        print(
            f"\n[{status}] {result.meta.path}\n"
            f"  bank={result.meta.bank_code or '?'} product={result.produit or '?'} "
            f"period={result.meta.period or '?'} sheet={result.sheet or '?'} "
            f"rows={result.n_rows}"
        )
        if result.error:
            print(f"  error: {result.error}")
        for note in result.notes:
            print(f"  note: {note}")
        for mapping in result.mappings:
            if not mapping.normalized:
                continue
            target = mapping.canonical or "UNRESOLVED"
            print(
                f"  col {mapping.column_index + 1:>3}: {mapping.header!r} -> {target} "
                f"[{mapping.method}, {mapping.confidence:.0%}] {mapping.reason}"
            )
            if mapping.profile:
                print(f"           content: {mapping.profile.describe()}")
            for warning in mapping.warnings:
                print(f"           WARNING: {warning}")
            if mapping.suggestions and mapping.canonical is None:
                suggestions = ", ".join(
                    f"{name} ({score:.0f})" for name, score in mapping.suggestions
                )
                print(f"           suggestions: {suggestions}")


def cmd_scan(args) -> int:
    config = load_config(args.config)
    metas = scan(args.root, config.banks, config.schema_)
    if not metas:
        print(f"no workbooks found under {args.root}")
        return 1

    print(f"{len(metas)} workbooks\n")
    print(f"{'file':38s} {'bank':7s} {'product':22s} {'period':9s}")
    print("-" * 80)
    unresolved = 0
    for meta in metas:
        if not meta.resolved:
            unresolved += 1
        print(
            f"{meta.path.name[:37]:38s} {(meta.bank_code or '?'):7s} "
            f"{(meta.produit or '?')[:21]:22s} {(meta.period or '?'):9s}"
        )
        for note in meta.notes:
            print(f"    ! {note}")
    if unresolved:
        print(f"\n{unresolved} file(s) need a bank, product or period set by hand.")
    return 0


def cmd_profile(args) -> int:
    config = load_config(args.config)
    frame = profile_headers(args.root, config)
    if frame.empty:
        print("no headers found")
        return 1

    unresolved = frame[frame["champ_propose"] == "(non résolu)"]
    print(f"{len(frame)} distinct headers across the corpus")
    print(f"{len(frame) - len(unresolved)} map to a known field, {len(unresolved)} do not\n")
    colonnes = [c for c in ("variantes", "banques", "produits", "fichiers",
                            "champ_propose", "methode") if c in frame.columns]
    print(frame[colonnes].to_string(index=False, max_colwidth=32))

    if args.output:
        frame.to_excel(args.output, index=False, sheet_name="Entêtes")
        print(f"\nwritten to {args.output}")
    return 0


def cmd_run(args) -> int:
    config = load_config(args.config)
    overrides = _load_overrides(args.overrides, config)
    outcome = run(
        args.root, config, args.profile, use_model=not args.no_model,
        overrides=overrides,
    )
    print(outcome.summary_line('en'))

    if not args.quiet:
        _print_details(outcome)

    for path, reason in outcome.skipped:
        print(f"  skipped {path.name}: {reason}")

    unresolved = outcome.unresolved_headers()
    if unresolved:
        print("\nheaders needing a decision:")
        for header, count in unresolved.most_common():
            print(f"  {count:3d}x {header!r}")

    if outcome.validation.flags:
        print("\nflags:")
        counts = outcome.validation.to_frame().groupby(["severity", "code"]).size()
        for (severity, code), count in counts.items():
            print(f"  {severity:8s} {code:26s} {count}")

    if args.audit and outcome.frame is not None:
        path = write_audit(args.audit, outcome.results, outcome.validation, outcome.frame)
        print(f"\naudit written to {path}")
    return 0 if not outcome.failed_results else 2


def cmd_commit(args) -> int:
    config = load_config(args.config)
    overrides = _load_overrides(args.overrides, config)
    warehouse = Warehouse(config, args.warehouse, args.profile)

    metas = scan(args.root, config.banks, config.schema_)
    buckets = warehouse.pending([m.path for m in metas])
    todo = buckets["new"] + buckets["changed"]

    print(
        f"{len(buckets['unchanged'])} unchanged, {len(buckets['new'])} new, "
        f"{len(buckets['changed'])} changed"
    )
    if not todo and not args.force:
        print("nothing to do")
        return 0
    if args.force:
        todo = [m.path for m in metas]

    outcome = run(
        args.root, config, args.profile, use_model=not args.no_model, only=todo,
        overrides=overrides,
    )
    print(outcome.summary_line('en'))
    if not args.quiet:
        _print_details(outcome)

    if outcome.validation.errors and not args.allow_errors:
        print(
            f"\n{outcome.validation.errors} error-level flags. "
            "Review them, then re-run with --allow-errors to commit anyway."
        )
        if args.audit:
            write_audit(args.audit, outcome.results, outcome.validation, outcome.frame)
            print(f"audit written to {args.audit}")
        return 2

    review_count = sum(len(result.needs_review) for result in outcome.results)
    if review_count and not args.allow_review:
        print(
            f"\n{review_count} column decision(s) require review (unresolved, "
            "content mismatch, or model table correction). Review the detailed output "
            "or audit, provide --overrides, then re-run. Use --allow-review only when "
            "you have explicitly accepted these decisions."
        )
        if args.audit and outcome.frame is not None:
            write_audit(args.audit, outcome.results, outcome.validation, outcome.frame)
            print(f"audit written to {args.audit}")
        return 2

    try:
        summary = commit_run(outcome, config, args.warehouse, dry_run=args.dry_run)
    except SchemaContractError as exc:
        print(f"\n{exc}")
        return 3

    verb = "would write" if args.dry_run else "wrote"
    print(
        f"{verb} {summary['written']} rows "
        f"(replaced {summary['replaced']}), warehouse now holds {summary['total']}"
    )
    for code, detail in sorted(summary.get("par_produit", {}).items()):
        print(f"  {code:24s} {detail['total']:6d} rows")
    excel_files = summary.get("excel_files", [])
    if excel_files:
        print(f"  Excel exports: {len(excel_files)}")
        if not args.quiet:
            for path in excel_files:
                print(f"    {path}")
    for note in summary.get("skipped_xlsx", []):
        print(f"  note: {note}")
    if args.audit and outcome.frame is not None:
        write_audit(args.audit, outcome.results, outcome.validation, outcome.frame)
        print(f"audit written to {args.audit}")
    return 0


def cmd_status(args) -> int:
    config = load_config(args.config)
    warehouse = Warehouse(config, args.warehouse)
    fact = warehouse.read_fact()
    if fact.empty:
        print(f"warehouse at {warehouse.root} is empty")
        return 0

    print(f"warehouse: {warehouse.root}")
    print(f"rows:      {len(fact)}")
    print(f"files:     {len(warehouse.manifest.files)}")
    print(f"products:  {len(warehouse.produits_presents())}")
    print()
    cles = [c for c in ("banque", "produit_code") if c in fact.columns]
    grouped = fact.groupby(cles).agg(
        lignes=("row_hash", "count"),
        mois=("mois_reception", "nunique"),
    )
    if "prime_totale" in fact.columns:
        grouped["prime_totale"] = fact.groupby(cles)["prime_totale"].sum().round(2)
    print(grouped.to_string())
    return 0


def cmd_export(args) -> int:
    config = load_config(args.config)
    warehouse = Warehouse(config, args.warehouse)
    paths = warehouse.export_excel(args.produit)
    if not paths:
        print("no warehouse rows to export")
        return 1
    print(f"wrote {len(paths)} Excel-compatible file(s):")
    for path in paths:
        print(f"  {path}")
    return 0


def _run_command(args) -> int:
    """Dispatch, turning the expected failures into messages rather than tracebacks."""
    try:
        return args.func(args)
    except WarehouseCorrupt as exc:
        print(f"\n{exc}")
        return 4
    except SchemaContractError as exc:
        print(f"\n{exc}")
        return 3
    except FileNotFoundError as exc:
        print(f"\n{exc}")
        return 1
    except (ValueError, yaml.YAMLError) as exc:
        print(f"\ninvalid input or configuration: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; nothing was written")
        return 130


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardif", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("scan", help="show bank and period for every file")
    _add_common(p)
    p.set_defaults(func=cmd_scan)

    p = subparsers.add_parser("profile", help="inventory every header in the corpus")
    _add_common(p)
    p.add_argument("-o", "--output", type=Path, help="write the inventory to an xlsx")
    p.set_defaults(func=cmd_profile)

    p = subparsers.add_parser("run", help="process everything without writing")
    _add_common(p)
    p.add_argument("--audit", type=Path, help="write the audit workbook here")
    p.set_defaults(func=cmd_run)

    p = subparsers.add_parser("commit", help="process and append to the warehouse")
    _add_common(p)
    p.add_argument("--warehouse", type=Path, default=None)
    p.add_argument("--audit", type=Path, help="write the audit workbook here")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="reprocess unchanged files too")
    p.add_argument("--allow-errors", action="store_true",
                   help="commit even when error-level flags were raised")
    p.add_argument("--allow-review", action="store_true",
                   help="commit despite unresolved or model-corrected columns")
    p.set_defaults(func=cmd_commit)

    p = subparsers.add_parser("status", help="what the warehouse holds")
    p.add_argument("--config", type=Path, default=Path("config"))
    p.add_argument("--warehouse", type=Path, default=None)
    p.set_defaults(func=cmd_status)

    p = subparsers.add_parser(
        "export", help="regenerate complete .xlsx deliveries from Parquet"
    )
    p.add_argument("--config", type=Path, default=Path("config"))
    p.add_argument("--warehouse", type=Path, default=None)
    p.add_argument("--produit", help="export one product only")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return _run_command(args)


if __name__ == "__main__":
    sys.exit(main())
