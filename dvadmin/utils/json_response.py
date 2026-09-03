# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/2 14:43
@Remark: Custom JsonResponse file
"""

from rest_framework.response import Response


class SuccessResponse(Response):
    """
    Standard success response, SuccessResponse(data) or SuccessResponse(data=data)
    (1) Default code returns 2000, does not support specifying other return codes
    """

    def __init__(self, data=None, msg='success', status=None, template_name=None, headers=None, exception=False,
                 content_type=None,page=1,limit=1,total=1):
        std_data = {
            "code": 2000,
            "page": page,
            "limit": limit,
            "total": total,
            "data": data,
            "msg": msg
        }
        super().__init__(std_data, status, template_name, headers, exception, content_type)


class DetailResponse(Response):
    """
    Response for interfaces without pagination info, mainly used for single data query
    (1) Default code returns 2000, does not support specifying other return codes
    """

    def __init__(self, data=None, msg='success', status=None, template_name=None, headers=None, exception=False,
                 content_type=None,):
        std_data = {
            "code": 2000,
            "data": data,
            "msg": msg
        }
        super().__init__(std_data, status, template_name, headers, exception, content_type)


class ErrorResponse(Response):
    """
    Standard error response, ErrorResponse(msg='xxx')
    (1) Default error code returns 400, or specify another code: ErrorResponse(code=xxx)
    """

    def __init__(self, data=None, msg='error', code=400, status=None, template_name=None, headers=None,
                 exception=False, content_type=None):
        std_data = {
            "code": code,
            "data": data,
            "msg": msg
        }
        super().__init__(std_data, status, template_name, headers, exception, content_type)