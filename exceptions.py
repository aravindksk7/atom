from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

from etl_framework.exceptions import (
    ETLFrameworkError,
    SchemaValidationError,
    ConfigurationError,
    RepositoryError
)

logger = logging.getLogger("api.exceptions")

def configure_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SchemaValidationError)
    async def schema_validation_handler(request: Request, exc: SchemaValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "SchemaValidationError",
                "message": str(exc),
                "details": {
                    "missing_in_target": exc.missing_in_target,
                    "extra_in_target": exc.extra_in_target
                }
            }
        )

    @app.exception_handler(ConfigurationError)
    async def config_error_handler(request: Request, exc: ConfigurationError):
        return JSONResponse(
            status_code=400,
            content={"error": "ConfigurationError", "message": str(exc)}
        )

    @app.exception_handler(ETLFrameworkError)
    async def generic_etl_handler(request: Request, exc: ETLFrameworkError):
        if isinstance(exc, RepositoryError):
            logger.error(f"Repository Error: {exc.original_error}", exc_info=True)
            return JSONResponse(status_code=500, content={"error": "RepositoryError", "message": "A database error occurred."})
        
        return JSONResponse(
            status_code=400,
            content={"error": "ETLFrameworkError", "message": str(exc)}
        )