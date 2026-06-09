# Close-out gates (reference implementations)

Deterministic checks an `orc` close-out can run before marking a spec shipped.
They encode failure *classes* that recurred in the reference deployment until a
gate caught them. They are **project-shaped by nature** — each is tuned for a
common stack and exposes its paths via env, so adapt them to yours.

| gate | catches | adjust via |
|------|---------|-----------|
| `check_nav_reachability.sh` | a new page built but ORPHANED (no nav link, URL-only) | `NAV_SIDEBAR`, `NAV_APP_DIR` (Next.js App Router defaults) |
| `check_data_consumers.sh` | a data spec ships its table while a downstream surface goes stale | `CONSUMERS_ROUTERS_DIR`, `CONSUMERS_LIB_DIR` (Python defaults) |
| `write_deploy_manifest.sh` | "is it live / which host / which sha" re-derived every review tick | `DEPLOY_SERVER` (required), `VERSION_URL`, `ALEMBIC_INI`, `MANIFEST_PATH` |

These are optional. The *concepts* are the durable part (orphan-nav, cross-spec
data dependencies, a single deploy-ground-truth manifest); the scripts are a
starting point, not a framework-agnostic library.
