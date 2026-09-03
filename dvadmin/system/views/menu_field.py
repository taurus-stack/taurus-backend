# -*- coding: utf-8 -*-
from django.apps import apps
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from dvadmin.system.models import  Role, MenuField
from dvadmin.utils.models import get_custom_app_models
from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.json_response import DetailResponse, ErrorResponse, SuccessResponse


class MenuFieldSerializer(CustomModelSerializer):
    """
    Column permission serializer
    """

    class Meta:
        model = MenuField
        fields = '__all__'
        read_only_fields = ['id']


class MenuFieldViewSet(CustomModelViewSet):
    """
    Column permission viewset
    """
    queryset = MenuField.objects.order_by('-model')
    serializer_class = MenuFieldSerializer

    def list(self, request, *args, **kwargs):
        menu = request.query_params.get('menu')
        if  not menu:
            return SuccessResponse([])
        queryset = self.filter_queryset(self.get_queryset().filter(menu=menu))
        serializer = self.get_serializer(queryset, many=True, request=request)
        return SuccessResponse(data=serializer.data, msg="Retrieved successfully")

    def create(self, request, *args, **kwargs):
        payload = request.data
        for model in apps.get_models():
            if payload.get('model') == model.__name__:
                break
        else:
            return ErrorResponse(msg='Model table does not exist')

        if MenuField.objects.filter(menu=payload.get('menu'),model=model.__name__, field_name=payload.get('field_name')).exists():
            return ErrorResponse(msg="'%s' field permission already exists, cannot create duplicate" % payload.get('title'))

        return super().create(request, *args, **kwargs)

    @action(methods=['GET'], detail=False, permission_classes=[IsAuthenticated])
    def get_models(self, request):
        """Fetch models from all project apps"""
        res = []
        for model in get_custom_app_models():
            res.append({
                'app': model['app'],
                'title': model['verbose'],
                'key': model['model']
            })
        return DetailResponse(res)

    @action(methods=['POST'], detail=False, permission_classes=[IsAuthenticated])
    def auto_match_fields(self, request):
        """Auto-match existing fields"""
        menu_id = request.data.get('menu')
        model_name = request.data.get('model')
        if not menu_id or not model_name:
            return ErrorResponse( msg='Invalid parameters')
        for model in get_custom_app_models():
            if model['model'] != model_name:
                continue
            for field in model['fields']:
                if MenuField.objects.filter(
                        menu_id=menu_id, model=model_name, field_name=field['name']
                ).exists():
                    continue
                data = {
                    'menu': menu_id,
                    'model': model_name,
                    'field_name': field['name'],
                    'title': str(field['title']),
                }
                serializer = self.get_serializer(data=data, request=request)
                serializer.is_valid(raise_exception=True)
                serializer.save()
        return SuccessResponse(msg='Match successful')