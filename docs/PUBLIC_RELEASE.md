# Public snapshot scope

## Included

- First-level Python research tools, including data collection, risk checks and valuation logic.
- Multi-agent role prompts and the primary research framework.
- Verdict specification, data-layer documentation, local context-loading/rendering scripts and existing regression tests.
- Setup documentation and an ignore file to reduce accidental publication of runtime data or credentials.

## Deliberately excluded

- Broker research PDFs, downloaded source documents and extracted datasets.
- Company/customer material, client working documents, tender/pitch files, presentation templates and work summaries.
- Historical reports, verdicts, knowledge-base entries, probability predictions, watchlist contents and daily logs.
- Local assistant settings, scheduled-task state, shell automation, virtual environments, caches, backups and operating-system metadata.
- Vendored third-party repositories and auxiliary skill collections; no ownership or licensing claim is made over those excluded materials.

These exclusions apply to this public copy. The original local project and its working data are unchanged.

## Publication-only adjustments

- Local absolute paths in documentation and command examples are made relative.
- The local memory loader derives its project root from its own file location.
- The external benchmark path is optional and configurable through `RRB_ROOT`; the benchmark itself is not distributed.
- The verdict-schema example uses explicitly synthetic figures instead of historical personal research.
- No financial formulas, scoring thresholds or substantive research logic are intentionally changed for publication.

## Validation boundary

Run `python -m pytest -q` to check the included offline regression tests. See [VALIDATION.md](VALIDATION.md) for the actual run result and current failures. Static pattern checks are useful but cannot guarantee the absence of every sensitive fact or establish ownership rights. No live financial-provider or paid model calls are part of publication validation.

Before publishing additions, review them explicitly: an ignored directory can still be added using force options, and a file outside the listed paths can still contain sensitive information.
