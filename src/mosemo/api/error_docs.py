from collections.abc import Mapping

from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs

# Temporary home of the central ErrorCode docs. Each bounded context move takes
# its members to that context's presentation/error_docs.py; every member has
# moved and #154 removes this module.
ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {}
