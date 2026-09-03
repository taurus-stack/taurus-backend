import hashlib
import mimetypes

from rest_framework import serializers
from rest_framework.decorators import action

from application import dispatch
from dvadmin.system.models import FileList
from dvadmin.utils.json_response import DetailResponse
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class FileSerializer(CustomModelSerializer):
    url = serializers.SerializerMethodField(read_only=True)

    def get_url(self, instance):
        # return 'media/' + str(instance.url)
        return instance.file_url or (f'media/{str(instance.url)}')

    class Meta:
        model = FileList
        fields = "__all__"

    def create(self, validated_data):
        file_engine = dispatch.get_system_config_values("fileStorageConfig.file_engine") or 'local'
        file_backup = dispatch.get_system_config_values("fileStorageConfig.file_backup")
        file = self.initial_data.get('file')
        file_size = file.size
        validated_data['name'] = str(file)
        validated_data['size'] = file_size
        md5 = hashlib.md5()
        for chunk in file.chunks():
            md5.update(chunk)
        validated_data['md5sum'] = md5.hexdigest()
        validated_data['engine'] = file_engine
        validated_data['mime_type'] = file.content_type
        if file_backup:
            validated_data['url'] = file
        if file_engine == 'oss':
            from dvadmin_cloud_storage.views.aliyun import ali_oss_upload
            file_path = ali_oss_upload(file)
            if file_path:
                validated_data['file_url'] = file_path
            else:
                raise ValueError("Upload failed")
        elif file_engine == 'cos':
            from dvadmin_cloud_storage.views.tencent import tencent_cos_upload
            file_path = tencent_cos_upload(file)
            if file_path:
                validated_data['file_url'] = file_path
            else:
                raise ValueError("Upload failed")
        else:
            validated_data['url'] = file
        # Audit fields
        try:
            request_user = self.request.user
            validated_data['dept_belong_id'] = request_user.dept.id
            validated_data['creator'] = request_user.id
            validated_data['modifier'] = request_user.id
        except:
            pass
        return super().create(validated_data)


class FileViewSet(CustomModelViewSet):
    """
    File management interface
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = FileList.objects.all()
    serializer_class = FileSerializer
    filter_fields = ['name', ]
    permission_classes = []