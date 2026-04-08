"""Control-plane console package.

Keep package import side-effect free so pure view helpers do not pull in
manifest/runtime-policy wiring during module import.
"""

__all__: tuple[str, ...] = ()
