# ADR 006: Environment Simplification and SDK Migration

## Status

Accepted

## Context

The `.env` file contained 6 variables, but most were no longer necessary:

- `DATA_MODEL_KEY` was supplanted by the Stage 1 data model selection dialog
  (users pick the model interactively, not via environment variable)
- `DATA_MODEL_STORE_API_KEY` always fell back to `NETRIAS_API_KEY` (the keys
  are now unified)
- `CDE_RECOMMEND_URL` belongs in the SDK alongside other service URLs
- `DEV_MODE` only controls static file caching — can be set inline by the
  `just` recipe
- `CORS_ALLOW_ORIGINS` already defaults to `"*"` in code

Additionally, `data_model_client.py` was a temporary HTTP wrapper duplicating
capabilities already present in the `netrias_client` SDK (marked TODO since
initial implementation).

## Decision

1. **Single env var**: `.env` contains only `NETRIAS_API_KEY`
2. **Environment URL registry**: Added `Environment` enum to `netrias_client`
   SDK with prod/staging URL presets. Consumers pass
   `environment=Environment.PROD` instead of configuring individual URLs.
3. **Shared singleton**: One `NetriasClient` instance in `dependencies.py`,
   injected into `HarmonizeService`, `MappingDiscoveryService`, and the
   data model adapter.
4. **Data model adapter**: Thin `data_model_adapter.py` converts SDK types
   to domain types. Replaces the deleted `data_model_client.py`.
5. **Threaded target_schema**: `populate_cde_cache()` now accepts
   `data_model_key` as a parameter (from the user's Stage 1 selection)
   instead of reading from an env var.

## Files Changed

- **Deleted**: `src/domain/data_model_client.py` (232 lines)
- **Created**: `src/domain/data_model_adapter.py` — thin adapter wrapping SDK calls
- **Modified**: `src/domain/dependencies.py` — single `NetriasClient` singleton
- **Modified**: `src/domain/config.py` — stripped to `NETRIAS_API_KEY` only
- **Modified**: `src/domain/data_model_cache.py` — `populate_cde_cache()` takes `data_model_key` param
- **Modified**: Stage 1/2/3 routers — thread `target_schema` from UI selection

## Related ADRs

- [ADR 005](ADR_005_cde_lambda_migration.md) — initial `netrias-client` SDK migration; this ADR continues that work

## Consequences

- **Simpler onboarding**: New developers only need one API key
- **No hardcoded model**: Data model is selected at runtime via the UI
- **URL management centralized**: Service URLs live in the SDK's environment
  registry, not scattered across application config
- **Temporary simplification**: The URL registry is a dict literal in the SDK.
  If environments proliferate, this should become a config file or service
  discovery mechanism.
