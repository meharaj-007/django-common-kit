"""Standard error messages (PRD §5).

Written for a person, not for a log: "You don't have permission to perform this
action", not "Permission denied". These strings are read by users.

A project overrides any of these by subclassing; nothing here is a business
message, and none of it is a substitute for a serializer's own field errors.
"""


class ErrorMessage:
    """Standardised error messages used across the application."""

    # -- authentication -----------------------------------------------------
    INVALID_CREDENTIALS = "Invalid credentials provided."
    AUTHENTICATION_FAILED = "Authentication failed. Please check your credentials."
    TOKEN_EXPIRED = "Your session has expired. Please log in again."
    INVALID_TOKEN = "Invalid or expired authentication token."
    ACCOUNT_DISABLED = "Your account is disabled. Please contact support."
    ACCOUNT_LOCKED = "Account has been locked for multiple invalid login attempts."
    EMAIL_NOT_VERIFIED = "Please verify your email address before continuing."

    # -- authorisation ------------------------------------------------------
    PERMISSION_DENIED = "You don't have permission to perform this action."
    INSUFFICIENT_PERMISSIONS = "Insufficient permissions to access this resource."
    IP_NOT_WHITELISTED = "Your IP address is not authorized to access this endpoint."

    # -- users --------------------------------------------------------------
    USER_NOT_FOUND = "User not found."
    USER_ALREADY_EXISTS = "User with this email already exists."

    # -- request ------------------------------------------------------------
    INVALID_REQUEST = "The request contains invalid data."
    MISSING_REQUIRED_FIELDS = "Required fields are missing from the request."
    INVALID_FIELD_FORMAT = "One or more fields have an invalid format."
    # Both of these take a {field} placeholder; the caller formats them.
    MISSING_REQUIRED_FIELD = "Missing required field: {field}."
    INVALID_FIELD_VALUE = "Invalid value for field: {field}."

    # -- resources ----------------------------------------------------------
    RESOURCE_NOT_FOUND = "The requested resource was not found."
    RESOURCE_ALREADY_EXISTS = "A resource with these details already exists."
    RESOURCE_IN_USE = "This resource is currently in use and cannot be modified."

    # -- validation ---------------------------------------------------------
    VALIDATION_FAILED = "Data validation failed."
    INVALID_EMAIL_FORMAT = "Please provide a valid email address."
    INVALID_PHONE_FORMAT = "Please provide a valid phone number."
    WEAK_PASSWORD = "Password does not meet security requirements."

    # -- server -------------------------------------------------------------
    # GENERIC_ERROR_MESSAGE and INTERNAL_SERVER_ERROR are the same string on
    # purpose: the message a user should see when the server broke does not
    # depend on which name the caller reached for.
    GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again later."
    INTERNAL_SERVER_ERROR = "Something went wrong. Please try again later."
    SERVICE_UNAVAILABLE = "The service is temporarily unavailable."
    DATABASE_ERROR = "A database error occurred. Please try again."

    # -- rate limiting ------------------------------------------------------
    RATE_LIMIT_EXCEEDED = "Too many requests. Please try again later."

    # -- file upload --------------------------------------------------------
    FILE_TOO_LARGE = "The uploaded file is too large."
    INVALID_FILE_TYPE = "The uploaded file type is not supported."
    FILE_UPLOAD_FAILED = "File upload failed. Please try again."
