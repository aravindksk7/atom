class ETLFrameworkError(Exception):
    pass


class ConfigurationError(ETLFrameworkError):
    def __init__(self, message: str, field_name: str | None = None,
                 file_path: str | None = None) -> None:
        self.field_name = field_name
        self.file_path = file_path
        super().__init__(message)


class DatabaseConnectionError(ETLFrameworkError):
    def __init__(self, env_name: str, host: str, port: int, db_name: str) -> None:
        self.env_name = env_name
        self.host = host
        self.port = port
        self.db_name = db_name
        super().__init__(f"Cannot connect to '{env_name}' at {host}:{port}/{db_name}")


class QueryExecutionError(ETLFrameworkError):
    def __init__(self, env_name: str, query: str, original_error: Exception) -> None:
        self.env_name = env_name
        self.query = query
        self.original_error = original_error
        super().__init__(f"Query failed on '{env_name}': {original_error}")


class AutomicAPIError(ETLFrameworkError):
    def __init__(self, http_status: int, response_body: str, url: str) -> None:
        self.http_status = http_status
        self.response_body = response_body
        self.url = url
        super().__init__(f"Automic API error {http_status} at {url}")


class AutomicTimeoutError(ETLFrameworkError):
    def __init__(self, url: str, attempts: int, timeout_seconds: int) -> None:
        self.url = url
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Automic timeout after {attempts} attempts at {url}")


class ReportNotFoundError(ETLFrameworkError):
    def __init__(self, report_id: str, env_name: str) -> None:
        self.report_id = report_id
        self.env_name = env_name
        super().__init__(f"Report '{report_id}' not found in '{env_name}'")


class BOAPIError(ETLFrameworkError):
    def __init__(self, report_id: str, http_status: int, response_body: str) -> None:
        self.report_id = report_id
        self.http_status = http_status
        self.response_body = response_body
        super().__init__(f"SAP BO API error {http_status} for report '{report_id}'")


class ReportOutputError(ETLFrameworkError):
    def __init__(self, target_path: str, original_os_error: Exception) -> None:
        self.target_path = target_path
        self.original_os_error = original_os_error
        super().__init__(f"Cannot write report to '{target_path}': {original_os_error}")
