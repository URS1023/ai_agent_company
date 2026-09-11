# Enterprise business transport

Routes the generated business namespace through native session-aware transport without changing native console routes.

## Internal Modules

- `link.ts`: Lazily loads the business contract and resolves ordinary API paths against the browser origin.
- `workbench-send.ts`: Sends one generation POST using generated input and route contracts, same-origin/base-path resolution, and native cookie/CSRF handling. Uses `base` rather than the JSON wrapper to avoid automatic refresh/reissue. Closing the stream cancels the HTTP body, not the model task. The caller retains the client message ID and owns uncertain-state reconciliation.
- `workbench-stream.ts`: Bounded, abortable SSE framing. Performs no request, retry, identity acknowledgement or terminal inference. Preserves unknown event envelopes; an owner must validate identities and terminal state before updating conversations.

## External Modules

- `env`: Supplies the same Next base path used by the web deployment.
- `service/base`: Owns ordinary JSON session-refresh and error handling.
- `service/fetch`: Owns native cookie/CSRF injection and raw response transport with retry methods disabled.
- `@enterprise/business-contracts`: Generated routes, request schemas and types from the actual enterprise HTTP API.
