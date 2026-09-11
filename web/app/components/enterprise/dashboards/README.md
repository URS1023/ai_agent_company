# Dashboards

Manage fixed-template dashboards, approved data bindings and SQL generation drafts without editing template visuals.

## Internal Modules

- `devices/access`: workspace access gate and opaque request failure UI.
- `dashboards/index`: URL-owned dashboard selection and management entry points.
- `dashboards/detail` and `dashboards/dashboard-canvas`: versioned dashboard reads and renderer lifecycle.
- `dashboards/bindings`, `dashboards/binding-editor` and `dashboards/binding-form`: approved-query binding selection and validation.
- `dashboards/sql-generation`: prompt submission and URL-owned `sqlDraft` selection.
- `dashboards/saved-sql-draft`: management-only saved draft inspection, scoped queries and reauthorization before displaying cached content.
- `dashboards/sql-proposal-cards`: business-first metric/time presentation with independently expandable, text-only SQL and field mappings per slot.
- `dashboards/sql-trial` and `dashboards/sql-trial-result`: explicit saved-slot trials, scoped response validation, lossless text cells and local result pagination without automatic retries or publication.
- `dashboards/sql-trial-history` and `dashboards/sql-trial-table`: lazy metadata history, current-authorized saved result inspection, and shared lossless result presentation. Successful trial recording invalidates only the current draft's history list.
- `dashboards/sql-trial-assessment`: on-demand authorized sample assessment, localized type/mapping issues and explicit outstanding unit/business review; never a publish action.
- `dashboards/sql-sample-check`: automatically matched configured sample rules, policy provenance and bounded issue presentation using the generated read-only endpoint; no rule-ID form or publication action.
- `dashboards/sql-trial-preview`: authorized single-slot preview using the pinned canvas, exact evidence/mapping checks and visible unfilled-widget/long-value notices; never updates persisted dashboard data or its query cache.
- `dashboards/sql-draft-preview-result`: multi-slot response validation against the exact selected evidence and draft, including mapping precision, duplicate/foreign records and shared renderer identity.
- `dashboards/sql-draft-preview`: explicit selected-ID preview requests and local combined rendering; history owns one selected evidence per slot and removal, while changed selections reset prior preview results.
- `dashboards/renderer-loader`: pinned original renderer resources.

## External Modules

- `env`: enterprise renderer base path configuration.

Draft inspection is not SQL execution or approval. Server-side current grants remain authoritative. Saved draft URLs also require the dashboard's `view` selection; a draft ID alone does not select a dashboard. Loading and failed reauthorization hide cached draft content. Real authenticated browser, model and database acceptance remains separate from mocked component regression tests.
