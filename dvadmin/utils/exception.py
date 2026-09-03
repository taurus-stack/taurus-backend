# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/2 16:06
@Remark: Custom exception handling
"""
import logging
import traceback

from django.db.models import ProtectedError
from django.http import Http404
from rest_framework.exceptions import APIException as DRFAPIException, AuthenticationFailed, NotAuthenticated, PermissionDenied as DRFPermissionDenied
from rest_framework.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_400_BAD_REQUEST,
)
from rest_framework.views import set_rollback, exception_handler

from dvadmin.utils.json_response import ErrorResponse

logger = logging.getLogger(__name__)


class CustomAuthenticationFailed(NotAuthenticated):
    # Set status_code attribute to 400
    status_code = 400

def CustomExceptionHandler(ex, context):
    """
    Unified exception interception handling
    Purpose: (1) Replace all 500 exception responses with standardized error returns
             (2) Display accurate error information
    :param ex:
    :param context:
    :return:
    """
    msg = ''
    code = 4000
    http_status = HTTP_400_BAD_REQUEST
    response = exception_handler(ex, context)
    if isinstance(ex, AuthenticationFailed):
        http_status = HTTP_401_UNAUTHORIZED
        if response and response.data.get('detail') == "Given token not valid for any token type":
            code = 401
            msg = ex.detail
        elif response and response.data.get('detail') == "Token is blacklisted":
            return ErrorResponse(status=HTTP_401_UNAUTHORIZED)
        else:
            code = 401
            msg = ex.detail
    elif isinstance(ex,Http404):
        http_status = HTTP_404_NOT_FOUND
        code = 404
        msg = "Resource does not exist or no access permission"
    elif isinstance(ex, DRFPermissionDenied):
        http_status = HTTP_403_FORBIDDEN
        code = 403
        msg = ex.detail
    elif isinstance(ex, DRFAPIException):
        set_rollback()
        http_status = getattr(ex, 'status_code', HTTP_400_BAD_REQUEST) or HTTP_400_BAD_REQUEST
        msg = ex.detail
        if isinstance(msg,dict):
            for k, v in msg.items():
                for i in v:
                    msg = "%s:%s" % (k, i)
    elif isinstance(ex, ProtectedError):
        set_rollback()
        msg = "Deletion failed: this data has related bindings with other data"
    # elif isinstance(ex, DatabaseError):
    #     set_rollback()
    #     msg = "Interface server error, please contact administrator"
    elif isinstance(ex, Exception):
        logger.exception(traceback.format_exc())
        msg = str(ex)
    return ErrorResponse(msg=msg, code=code, status=http_status)