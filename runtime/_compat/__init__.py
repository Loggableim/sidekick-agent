"""
Compat shim package — bridges old import paths to the new sidekick structure.

Modules in this package re-export names from their canonical locations in
``shared.*`` so that legacy agent code using ``from runtime._compat.shim_constants import …``
or ``from sidekick_logging import …`` continues to work without changes.
"""
