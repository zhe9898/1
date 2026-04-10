# Extension Manifests

This directory is the control-plane source of truth for external extension manifests.

Rules:
- Supported file types: `.json`, `.yaml`, `.yml`
- Example files must include `.example.` in the filename so the runtime loader skips them
- Schema references use `module.path:ClassName`
- Referenced classes must be importable and must subclass `pydantic.BaseModel`
- The runtime loads this directory through `backend.extensions.extension_sdk.bootstrap_extension_runtime()`

Recommended flow:
1. Ship your Python package containing Pydantic schema classes
2. Add a manifest file in this directory
3. Restart the control-plane runtime
4. Verify discovery through `/api/v1/extensions`
