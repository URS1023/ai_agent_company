# Enterprise portal

An opt-in workspace entry point that opens native Dify resources without replacing their routes or permissions, and links the shared alert/quality device workspace, source management, and dashboard workspace. The AI workbench remains explicitly unconnected. Dashboard navigation is implemented; this does not imply completed live-source or publication acceptance.

## Internal Modules

- `feature-flag.ts`: shared server/client rollout switch, disabled by default.
- `navigation-entry.tsx`: additive sidebar entry.
- `portal.tsx`: workspace header, native shortcuts, and business readiness labels.
- `application-lists.tsx`: generated application queries, pagination, and native ACL-aware links.
- `use-apps-page.ts`: URL-owned `appsPage` state (1–99999), with history navigation and strict parsing.
- `resource-section.tsx`: honest loading, error, retry, and empty states.

## External Modules

- `context/workspace-state`, `account-state`, and `permission-state`: existing authenticated workspace and membership.
- `service/client` and `service/use-apps`: generated contracts and the existing application normalization boundary.
- `features/system-features/client`: existing RBAC capability configuration.
- `utils/permission` and `utils/app-redirection`: native console ACL decisions.
- `explore/installed-app/routes` and `integrations/routes`: native route builders.

Set `NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL=true` in the web deployment to opt in. The existing runtime body mapping is used on the client; disabling the switch also returns not found for direct `/enterprise` requests. This switch is a rollout control, not an authorization boundary. This module creates no business data or reports and makes no business-service API calls.
