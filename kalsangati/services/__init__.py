"""Service layer for Kālsangati.

Services orchestrate use-cases.  Each service accepts a
``sqlite3.Connection`` by parameter (dependency injection) and returns
a dataclass with the operation's outcome.  Services own exception
raising for expected failures, all inheriting from
:class:`kalsangati.exceptions.KalsangatiError`.

Services are PyQt5-free and headlessly testable.
"""

from __future__ import annotations
