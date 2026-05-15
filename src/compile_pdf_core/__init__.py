"""compile-pdf-core — shared infrastructure for the CompilePDF producer family.

Provides lineage storage, retention consent, cache key computation,
Celery task wrappers, queue-depth resolution, and API auth/middleware
used by every CompilePDF producer (trap, impose, marks, rewrite).

Producers import from this package rather than duplicating infra.
"""

from compile_pdf_core.version import VERSION

__version__ = VERSION

__all__ = ["VERSION", "__version__"]
