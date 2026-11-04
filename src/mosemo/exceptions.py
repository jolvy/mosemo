from mosemo.shared.errors import ApiErrorCode


class ErrorCode(ApiErrorCode):
    """Central error codes kept until each bounded context moves its own members.

    Every member has moved; #154 removes this enum.
    """
