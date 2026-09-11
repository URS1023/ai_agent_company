# Enterprise run report

Shows a scoped execution and its business conclusion separately, with bounded evidence previews and full JSON report downloads.

## Internal Modules

- `devices/access`: native account/workspace business gate and explicit request failures.

## External Modules

None.

Reports use generated business contracts. The events API returns an array after `after_sequence`, not a Page envelope; the report requests cursor zero and displays that array in client-side pages of 20. Refresh invalidates only the current run's history; audit payloads are not rendered or added to exports. A truncated preview never truncates the downloaded RunView. Browser rendering and actual report file-save behavior still require browser acceptance testing.
