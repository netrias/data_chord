# ADR 004: CDE Lambda Migration (netrias-client 0.1.0)

## Status
Accepted

## Context

The netrias-client library version 0.1.0 introduces breaking API changes to support the new bdi-kit based CDE recommendation Lambda. The previous version (0.0.8) used a different endpoint with different method signatures.

## API Changes

### 1. Client Initialization

Changed from constructor-based to two-step pattern.

**Before (0.0.8):**
```python
# HarmonizeService
client = NetriasClient(api_key=self._api_key)

# MappingDiscoveryService
client = NetriasClient(api_key=self._api_key, confidence_threshold=0.0)
```

**After (0.1.0):**
```python
# HarmonizeService
client = NetriasClient()
client.configure(api_key=self._api_key)

# MappingDiscoveryService
client = NetriasClient()
client.configure(api_key=self._api_key, confidence_threshold=0.0)
```

### 2. discover_cde_mapping

Now requires `target_version` parameter. Method signature changed from keyword-only to positional parameters.

**Before (0.0.8):**
```python
# In HarmonizeService._discover_cde_map
raw_cde_map = self._client.discover_cde_mapping(
    source_csv=file_path,
    target_schema=target_schema,
)

# In MappingDiscoveryService._fetch_cde_mapping
return self._client.discover_cde_mapping(
    source_csv=csv_path,
    target_schema=target_schema,
    sample_limit=sample_limit,
)
```

**After (0.1.0):**
```python
# In HarmonizeService._discover_cde_map
raw_cde_map = self._client.discover_cde_mapping(
    source_csv=file_path,
    target_schema=target_schema,
    target_version="latest",
)

# In MappingDiscoveryService._fetch_cde_mapping
return self._client.discover_cde_mapping(
    source_csv=csv_path,
    target_schema=target_schema,
    target_version="latest",
    sample_limit=sample_limit,
)
```

### 3. harmonize

The library renamed the parameter from `cde_map` to `manifest`. Our internal variable remains `cde_map` for clarity, but we must use the library's parameter name when calling.

**Before (0.0.8):**
```python
netrias_result = self._client.harmonize(
    source_path=file_path,
    cde_map=cde_map,
)
```

**After (0.1.0):**
```python
# Internal variable stays 'cde_map'; library parameter is 'manifest'
netrias_result = self._client.harmonize(
    source_path=file_path,
    manifest=cde_map,
)
```

### 4. MappingClientProtocol

Updated to match the new method signatures.

**Before (0.0.8):**
```python
class MappingClientProtocol(Protocol):
    def discover_cde_mapping(
        self,
        *,
        source_csv: Path,
        target_schema: str,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    ) -> object:
        pass
```

**After (0.1.0):**
```python
class MappingClientProtocol(Protocol):
    def discover_cde_mapping(
        self,
        source_csv: Path,
        target_schema: str,
        target_version: str,
        sample_limit: int = 25,
        top_k: int | None = None,
    ) -> object:
        """Discover CDE mappings for harmonization."""
```

## Decision

Upgrade to netrias-client 0.1.0 and update all call sites to use the new API pattern.

### Affected Files
- `src/domain/harmonize.py`: Client initialization and API calls
- `src/domain/mapping_service.py`: Client initialization, API calls, and protocol definition

### Rollback Procedure

If rollback to 0.0.8 is required:

1. Change `pyproject.toml`: `"netrias-client==0.1.0"` → `"netrias-client==0.0.8"`
2. Revert the commits touching `harmonize.py` and `mapping_service.py`, or checkout the pre-migration versions from git history
3. Run `uv sync --reinstall`
4. Run tests to verify: `uv run pytest tests/`

## Consequences

**Positive:**
- Access to new bdi-kit based CDE recommendations with improved accuracy
- Explicit version specification (`target_version="latest"`) provides clearer API contract

**Negative:**
- Breaking change requires coordinated upgrade of netrias-client across environments
