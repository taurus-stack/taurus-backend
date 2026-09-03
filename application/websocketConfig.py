# -*- coding: utf-8 -*-
import urllib

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import json

from channels.layers import get_channel_layer
from jwt import InvalidSignatureError
from rest_framework.request import Request

from application import settings
from dvadmin.system.models import MessageCenter, Users
from dvadmin.system.views.message_center import MessageCenterTargetUserSerializer
from dvadmin.utils.serializers import CustomModelSerializer

send_dict = {}


# sendmessage结构体
def set_message(sender, msg_type, msg, unread=0, content_code=None):
    text = {
        'sender': sender,
        'contentType': msg_type,
        'content': msg,
        'unread': unread
    }
    if content_code is not None:
        text['content_code'] = content_code
    return text


# 异步获Cancel息中心的TargetUser
@database_sync_to_async
def _get_message_center_instance(message_id):
    from dvadmin.system.models import MessageCenter
    targets = MessageCenter.objects.filter(id=message_id).values_list('target_user', flat=True)
    return list(targets)


# 异步获取消息中心未读数
@database_sync_to_async
def _get_message_unread(user_id):
    from dvadmin.system.models import MessageCenterTargetUser
    count = MessageCenterTargetUser.objects.filter(
        target_user=user_id,
        is_read=False,
    ).count()
    return count


class DvadminWebSocket(AsyncJsonWebsocketConsumer):
    async def connect(self):
        try:
            import jwt
            self.service_uid = self.scope["url_route"]["kwargs"]["service_uid"]
            decoded_result = jwt.decode(self.service_uid, settings.SECRET_KEY, algorithms=["HS256"])
            if decoded_result:
                self.user_id = decoded_result.get('user_id')
                self.chat_group_name = "user_" + str(self.user_id)
                # 收到join时候process,
                await self.channel_layer.group_add(
                    self.chat_group_name,
                    self.channel_name
                )
                await self.accept()
                # Push notification
                unread_count = await _get_message_unread(self.user_id)
                if unread_count == 0:
                    # sendjoinSuccess
                    msg = set_message('system', 'SYSTEM', 'You are online', content_code='wsOnline')
                    await self.send_json(msg)
                else:
                    msg = set_message(
                        'system',
                        'SYSTEM',
                        "You have unread messages, please check",
                        unread=unread_count,
                        content_code='wsUnreadMessagesCheck'
                    )
                    await self.send_json(msg)
        except InvalidSignatureError:
            await self.disconnect(None)

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)
        print("Connection closed")
        try:
            await self.close(close_code)
        except Exception:
            pass


class MegCenter(DvadminWebSocket):
    """
    Message center
    """

    async def receive(self, text_data):
        # 接受Client的Message, 你process的function
        text_data_json = json.loads(text_data)
        message_id = text_data_json.get('message_id', None)
        user_list = await _get_message_center_instance(message_id)
        for send_user in user_list:
            await self.channel_layer.group_send(
                "user_" + str(send_user),
                {'type': 'push.message', 'json': text_data_json}
            )

    async def push_message(self, event):
        """messagesend"""
        message = event['json']
        await self.send(text_data=json.dumps(message))


def websocket_push(user_id, message):
    username = "user_" + str(user_id)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        username,
        {
            "type": "push.message",
            "json": message
        }
    )