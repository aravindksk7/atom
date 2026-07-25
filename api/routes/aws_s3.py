from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    FormatValidationOut,
    ObjectMetadataOut,
    PartitionSchemeOut,
    RowCountOut,
    S3MetadataRequest,
    S3PartitionsRequest,
    S3RowCountRequest,
    S3ValidateFormatRequest,
)
from api.services.audit_service import AuditService
from api.services.aws_s3_service import AwsS3Service
from etl_framework.exceptions import AWSError, SchemaValidationError
from etl_framework.repository.repository import ConfigRepository

router = APIRouter(tags=["aws-s3"])


def get_aws_s3_service(db: Session = Depends(get_session)) -> AwsS3Service:
    return AwsS3Service(ConfigRepository(db))


def _handle(fn, *args):
    try:
        return fn(*args)
    except SchemaValidationError as exc:
        raise HTTPException(status_code=400, detail={
            "error_type": "schema_validation",
            "message": str(exc),
            "missing_in_target": exc.missing_in_target,
            "extra_in_target": exc.extra_in_target,
        }) from exc
    except AWSError as exc:
        raise HTTPException(status_code=400, detail={
            "error_type": type(exc).__name__,
            "message": str(exc),
            "bucket": getattr(exc, "bucket", None),
            "key": getattr(exc, "key", None),
        }) from exc


@router.post("/metadata", response_model=ObjectMetadataOut)
def s3_metadata(body: S3MetadataRequest, request: Request,
                service: AwsS3Service = Depends(get_aws_s3_service),
                db: Session = Depends(get_session)):
    result = _handle(service.metadata, body.config_id, body.bucket, body.key)
    AuditService(db).log(request, "aws_s3.check", "aws_s3", body.bucket,
                         {"op": "metadata", "key": body.key})
    return result


@router.post("/row-count", response_model=RowCountOut)
def s3_row_count(body: S3RowCountRequest, request: Request,
                 service: AwsS3Service = Depends(get_aws_s3_service),
                 db: Session = Depends(get_session)):
    result = _handle(service.row_count, body.config_id, body.bucket, body.key, body.fmt)
    AuditService(db).log(request, "aws_s3.check", "aws_s3", body.bucket,
                         {"op": "row_count", "key": body.key, "fmt": body.fmt})
    return result


@router.post("/partitions", response_model=PartitionSchemeOut)
def s3_partitions(body: S3PartitionsRequest, request: Request,
                  service: AwsS3Service = Depends(get_aws_s3_service),
                  db: Session = Depends(get_session)):
    result = _handle(service.partitions, body.config_id, body.bucket, body.prefix)
    AuditService(db).log(request, "aws_s3.check", "aws_s3", body.bucket,
                         {"op": "partitions", "prefix": body.prefix})
    return result


@router.post("/validate-format", response_model=FormatValidationOut)
def s3_validate_format(body: S3ValidateFormatRequest, request: Request,
                       service: AwsS3Service = Depends(get_aws_s3_service),
                       db: Session = Depends(get_session)):
    result = _handle(service.validate_format, body.config_id, body.bucket,
                     body.key, body.fmt, body.expected_schema)
    AuditService(db).log(request, "aws_s3.check", "aws_s3", body.bucket,
                         {"op": "validate_format", "key": body.key, "fmt": body.fmt})
    return result
