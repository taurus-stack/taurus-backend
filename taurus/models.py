'''
Author: jinweijie jinweijie0527@outlook.com
Date: 2025-11-25 07:11:31
LastEditors: jinweijie jinweijie0527@outlook.com
LastEditTime: 2025-11-25 07:48:35
FilePath: /backend/taurus/models.py
Description: This is the default setting, please set `customMade`, open koroFileHeader to view configuration for settings: https://github.com/OBKoro1/koro1FileHeader/wiki/Config
'''
from django.db import models
from django.conf import settings
from typing import Optional
import uuid


class Inventory(models.Model):
    """Host inventory model"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    host_pattern = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class Module(models.Model):
    """Module model"""
    MODULE_CHOICES = [
        ('shell', 'Shell'),
        ('copy', 'Copy'),
        ('template', 'Template'),
        ('file', 'File'),
        ('yum', 'Yum'),
        ('service', 'Service'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, choices=MODULE_CHOICES)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Task(models.Model):
    """Task model"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE)
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    arguments = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class TaskResult(models.Model):
    """Task execution result model"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    host = models.CharField(max_length=100)
    result = models.JSONField()
    success = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.task.name} - {self.host}"


from application import settings
from dvadmin.utils.models import CoreModel

table_prefix = settings.TABLE_PREFIX


class Template(CoreModel):
    template_name = models.CharField(max_length=255, verbose_name="Template name")
    script_type = models.CharField(max_length=255, verbose_name="Script type", null=True, blank=True, )
    script_content = models.TextField(verbose_name="Template content", null=True, blank=True)
    editor_theme = models.CharField(max_length=255, verbose_name="Editor theme", null=True, blank=True)
    envs = models.TextField(verbose_name="Environment variables", null=True, blank=True, )
    args = models.TextField(verbose_name="Parameters", null=True, blank=True, )
    timeout = models.IntegerField(verbose_name="Timeout", default=0)
    share = models.BooleanField(verbose_name="Is shared", default=False)
    status = models.IntegerField(verbose_name="Status", null=True, blank=True)

    def __str__(self):
        return self.template_name

    class Meta:
        verbose_name = "Script template"
        db_table = table_prefix + "script_template"
        ordering = ("-create_datetime",)


class Host(CoreModel):
    STATUS_CHOICES = [
        (0, 'Pending approval'),
        (1, 'Approved'),
        (2, 'Rejected'),
        (3, 'Disabled'),
    ]

    CERTIFICATE_STATUS_CHOICES = [
        ('valid', 'Valid'),
        ('revoked', 'Revoked'),
        ('expired', 'Expired'),
    ]

    # Business unique ID (UUID, used for external exposure and client identification)
    host_uuid = models.UUIDField(
        default=uuid.uuid4, 
        unique=True, 
        editable=False,
        verbose_name="Host unique ID",
        help_text="Client uses this UUID for heartbeat and authentication"
    )
    host_name = models.CharField(max_length=255, verbose_name="Host name", null=True, blank=True)
    host_ip = models.CharField(max_length=255, verbose_name="Host IP", null=True, blank=True)
    host_username = models.CharField(max_length=255, verbose_name="Host username", null=True, blank=True)
    host_type = models.CharField(max_length=255, verbose_name="Host type", null=True, blank=True, default="unknown")
    extra_info = models.JSONField(verbose_name="Extra info", default=dict, blank=True, help_text="Host extra info, e.g. CMDB data")
    status = models.IntegerField(verbose_name="Host status", choices=STATUS_CHOICES, default=0)
    supervisor_version = models.CharField(max_length=50, verbose_name="Supervisor version", null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(verbose_name="Last heartbeat time", null=True, blank=True, help_text="Client last heartbeat time")
    online_status = models.IntegerField(verbose_name="Online status", default=0, help_text="0-Offline 1-Online")
    heartbeat_server = models.ForeignKey(
        'HeartbeatServer', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_hosts', verbose_name="Heartbeat server"
    )
    certificate_serial = models.CharField(
        max_length=100, verbose_name="Certificate serial number", null=True, blank=True,
        help_text="Client certificate serial number, used for certificate revocation"
    )
    certificate_status = models.CharField(
        max_length=20, verbose_name="Certificate status", choices=CERTIFICATE_STATUS_CHOICES, default='valid',
        help_text="Current status of client certificate"
    )
    certificate_revoked_at = models.DateTimeField(
        verbose_name="Certificate revocation time", null=True, blank=True,
        help_text="Time when certificate was revoked"
    )
    certificate_revocation_reason = models.CharField(
        max_length=255, verbose_name="Revocation reason", null=True, blank=True,
        help_text="Reason for certificate revocation"
    )
    users = models.ManyToManyField(
        to=settings.AUTH_USER_MODEL,
        related_name='hosts',
        blank=True,
        verbose_name="Authorized users"
    )
    request_signing_secret = models.CharField(
        max_length=255, verbose_name="Request signing key", null=True, blank=True,
        help_text="Shared key for request signing and anti-replay attacks, empty disables signature verification"
    )

    def __str__(self):
        return self.host_name

    class Meta:
        verbose_name = "Host info"
        db_table = table_prefix + "host"
        ordering = ("-create_datetime",)


class RegistrationToken(CoreModel):
    """Client registration token"""
    TOKEN_PREFIX = 'tao_'

    token = models.CharField(max_length=128, unique=True, verbose_name="Registration token hash", help_text="SHA256 hash, not plaintext")
    token_prefix = models.CharField(max_length=16, verbose_name="Token prefix", help_text="Used for display identification, e.g. tao_abc1...")
    name = models.CharField(max_length=100, verbose_name="Token name")
    description = models.TextField(verbose_name="Description", null=True, blank=True)
    expires_at = models.DateTimeField(verbose_name="Expiry time")
    max_uses = models.IntegerField(verbose_name="Max uses", default=1)
    used_count = models.IntegerField(verbose_name="Used count", default=0)
    allowed_ips = models.JSONField(verbose_name="IP whitelist", default=list, blank=True, help_text="Allowed IPs for registration, empty means no restriction")
    auto_approve = models.BooleanField(verbose_name="Auto approve", default=False, help_text="Auto approve host after registration")
    is_active = models.BooleanField(verbose_name="Is enabled", default=True)

    @staticmethod
    def hash_token(plain_token: str) -> str:
        import hashlib
        return hashlib.sha256(plain_token.encode('utf-8')).hexdigest()

    @staticmethod
    def generate_token() -> str:
        import secrets
        return f"{RegistrationToken.TOKEN_PREFIX}{secrets.token_urlsafe(32)}"

    def __str__(self):
        return f"{self.name} ({self.token_prefix}...)"

    class Meta:
        verbose_name = "Registration token"
        db_table = table_prefix + "registration_token"
        ordering = ['-create_datetime']


class HeartbeatServer(CoreModel):
    """Heartbeat server node"""
    name = models.CharField(max_length=100, verbose_name="Server name")
    address = models.CharField(max_length=255, verbose_name="Server address", help_text="e.g. http://hb-node1:8000")
    subnet = models.CharField(max_length=50, verbose_name="Responsible subnet", blank=True, default='',
                              help_text="CIDR format, e.g. 192.168.1.0/24, empty means fallback node")
    max_connections = models.IntegerField(verbose_name="Max connections", default=10000)
    current_connections = models.IntegerField(verbose_name="Current connections", default=0)
    weight = models.IntegerField(verbose_name="Weight", default=100, help_text="Higher weight = higher priority")
    is_active = models.BooleanField(verbose_name="Is enabled", default=True)

    def __str__(self):
        return f"{self.name} ({self.address})"

    @property
    def load_ratio(self) -> float:
        """Load ratio (0~1), lower means more idle"""
        if self.max_connections <= 0:
            return 1.0
        return min(self.current_connections / self.max_connections, 1.0)

    @classmethod
    def assign_for_host(cls, host_ip: str) -> Optional['HeartbeatServer']:
        """
        Assign heartbeat server based on Client IP.

        Assignment strategy:
        1. First match subnet (Client IP in which subnet gets assigned to corresponding server)
        2. If subnet not matched, load balance by weight + load ratio
        3. Return None when no available server (use default address)
        """
        import ipaddress

        active_servers = cls.objects.filter(is_active=True)
        if not active_servers.exists():
            return None

        # Step 1: try subnet matching
        try:
            client_ip = ipaddress.ip_address(host_ip)
        except ValueError:
            client_ip = None

        if client_ip:
            # Find most precise subnet match (longest subnet mask first)
            matched = []
            for server in active_servers:
                if server.subnet:
                    try:
                        network = ipaddress.ip_network(server.subnet, strict=False)
                        if client_ip in network:
                            matched.append((server, network.prefixlen))
                    except ValueError:
                        continue

            if matched:
                # Sort by subnet mask desc (most precise first), same precision by load ratio asc
                matched.sort(key=lambda x: (-x[1], x[0].load_ratio))
                return matched[0][0]

        # Step 2: subnet not matched, load balance by weight + load ratio
        # Only consider fallback nodes without subnet restriction
        fallback_servers = [s for s in active_servers if not s.subnet]
        if not fallback_servers:
            # No fallback nodes, pick least loaded from all nodes
            fallback_servers = list(active_servers)

        # Score = weight * (1 - load_ratio), higher score = higher priority
        best = None
        best_score = -1
        for server in fallback_servers:
            score = server.weight * (1 - server.load_ratio)
            if score > best_score:
                best_score = score
                best = server

        return best

    class Meta:
        verbose_name = "Heartbeat server"
        db_table = table_prefix + "heartbeat_server"
        ordering = ['-weight', 'id']


class ManagedProgram(CoreModel):
    """Managed program config (managed by Supervisor)"""
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='managed_programs', verbose_name="Host")
    name = models.CharField(max_length=100, verbose_name="Program name", help_text="e.g. taurus-executor, taurus-monitor")
    version = models.CharField(max_length=50, verbose_name="Current version")
    status = models.CharField(max_length=20, verbose_name="Running status", default='stopped',
                              choices=[
                                  ('stopped', 'Stopped'),
                                  ('starting', 'Starting'),
                                  ('running', 'Running'),
                                  ('stopping', 'Stopping'),
                                  ('crashed', 'Crashed'),
                                  ('upgrading', 'Upgrading'),
                              ])
    pid = models.IntegerField(verbose_name="Process PID", null=True, blank=True)
    port = models.IntegerField(verbose_name="Service port", null=True, blank=True)
    auto_start = models.BooleanField(verbose_name="Auto start", default=True)
    restart_on_crash = models.BooleanField(verbose_name="Auto restart on crash", default=True)
    config = models.JSONField(verbose_name="Program config", default=dict, blank=True,
                              help_text="Program config (business config passed via env_vars)")
    last_heartbeat_at = models.DateTimeField(verbose_name="Last heartbeat time", null=True, blank=True)

    def __str__(self):
        return f"{self.host.host_name} - {self.name} v{self.version}"

    class Meta:
        verbose_name = "Managed program"
        db_table = table_prefix + "managed_program"
        unique_together = [['host', 'name']]
        ordering = ['name']


class ProgramInstallTemplate(CoreModel):
    """Program install template (program config template decoupled from hosts)"""
    name = models.CharField(max_length=255, verbose_name="Template name", help_text="Template name, e.g. taurus-executor-prod")
    program_name = models.CharField(max_length=100, verbose_name="Program name", help_text="e.g. taurus-executor, taurus-monitor")
    version = models.CharField(max_length=50, verbose_name="Target version")
    config = models.JSONField(verbose_name="Program config", default=dict, blank=True,
                              help_text="Program config (business config passed via env_vars)")
    auto_start = models.BooleanField(verbose_name="Auto start", default=True)
    user = models.CharField(max_length=50, verbose_name="Run user", null=True, blank=True,
                            help_text="Program run user, empty means use current Supervisor user")
    group = models.CharField(max_length=50, verbose_name="Run user group", null=True, blank=True)
    max_retries = models.PositiveIntegerField(verbose_name="Max retries", default=3,
                                              help_text="Max retries allowed after install failure")
    description = models.TextField(verbose_name="Template description", null=True, blank=True,
                                   help_text="Template purpose description")

    def __str__(self):
        return f"{self.name} - {self.program_name} v{self.version}"

    class Meta:
        verbose_name = "Program install template"
        db_table = table_prefix + "program_install_template"
        ordering = ['program_name', '-create_datetime']


class ProgramHostBinding(CoreModel):
    """Host-program template binding (records install status of template on host)"""
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='program_bindings', verbose_name="Host")
    template = models.ForeignKey(ProgramInstallTemplate, on_delete=models.CASCADE, related_name='host_bindings', verbose_name="Install template")
    installed = models.BooleanField(verbose_name="Is installed", default=False)
    installing = models.BooleanField(verbose_name="Is installing", default=False,
                                     help_text="Mark command dispatched but Supervisor not yet reported result, avoid duplicate dispatch")
    dispatched_at = models.DateTimeField(verbose_name="Command dispatch time", null=True, blank=True,
                                         help_text="Used for timeout detection")
    installed_version = models.CharField(max_length=50, verbose_name="Installed version", null=True, blank=True,
                                         help_text="Actually installed version on current host")

    def __str__(self):
        return f"{self.host.host_name} - {self.template.name}"

    class Meta:
        verbose_name = "Host program binding"
        db_table = table_prefix + "program_host_binding"
        unique_together = [['host', 'template']]
        ordering = ['host', 'template']


class ProgramInstallConfig(CoreModel):
    """Program install config (for Server sending install commands to Supervisor)"""
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='program_install_configs', verbose_name="Host")
    template = models.ForeignKey(ProgramInstallTemplate, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='install_configs', verbose_name="Install template")
    program_name = models.CharField(max_length=100, verbose_name="Program name", help_text="e.g. taurus-executor, taurus-monitor")
    version = models.CharField(max_length=50, verbose_name="Target version")
    config = models.JSONField(verbose_name="Program config", default=dict, blank=True,
                              help_text="Program config (business config passed via env_vars)")
    auto_start = models.BooleanField(verbose_name="Auto start", default=True)
    user = models.CharField(max_length=50, verbose_name="Run user", null=True, blank=True,
                            help_text="Program run user, empty means use current Supervisor user")
    group = models.CharField(max_length=50, verbose_name="Run user group", null=True, blank=True)
    installed = models.BooleanField(verbose_name="Is installed", default=False)
    installing = models.BooleanField(verbose_name="Is installing", default=False,
                                     help_text="Mark command dispatched but Supervisor not yet reported result, avoid duplicate dispatch")
    enabled = models.BooleanField(verbose_name="Is enabled", default=True,
                                  help_text="When disabled, stops command dispatch; dispatched commands unaffected")
    max_retries = models.PositiveIntegerField(verbose_name="Max retries", default=3,
                                              help_text="Max retries allowed after install failure")
    dispatched_at = models.DateTimeField(verbose_name="Command dispatch time", null=True, blank=True,
                                         help_text="Used for timeout detection")
    result_message = models.TextField(verbose_name="Execution result", null=True, blank=True,
                                      help_text="Execution result or error info reported by Supervisor")

    def __str__(self):
        return f"{self.host.host_name} - {self.program_name} v{self.version}"

    class Meta:
        verbose_name = "Program install config"
        db_table = table_prefix + "program_install_config"
        ordering = ['program_name']


class ProgramCommand(CoreModel):
    """Program management command (for Server sending program management commands to Supervisor)"""
    ACTION_CHOICES = [
        ('install', 'Install'),
        ('upgrade', 'Upgrade'),
        ('start', 'Start'),
        ('stop', 'Stop'),
        ('restart', 'Restart'),
        ('remove', 'Remove'),
    ]
    
    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Executing'),
        (2, 'Success'),
        (3, 'Failed'),
    ]

    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='program_commands', verbose_name="Host")
    program_name = models.CharField(max_length=100, verbose_name="Program name")
    action = models.CharField(max_length=20, verbose_name="Action type", choices=ACTION_CHOICES)
    target_version = models.CharField(max_length=50, verbose_name="Target version", null=True, blank=True,
                                      help_text="Target version during upgrade")
    config = models.JSONField(verbose_name="Program config", default=dict, blank=True,
                              help_text="Program specific config")
    status = models.IntegerField(verbose_name="Execution status", choices=STATUS_CHOICES, default=0)
    dispatched = models.BooleanField(verbose_name="Is dispatched", default=False,
                                     help_text="Mark command dispatched but Supervisor not yet reported result, avoid duplicate dispatch")
    dispatched_at = models.DateTimeField(verbose_name="Command dispatch time", null=True, blank=True,
                                         help_text="Used for timeout detection")
    max_retries = models.PositiveIntegerField(verbose_name="Max retries", default=3,
                                              help_text="Max retries allowed after execution failure")
    result_message = models.TextField(verbose_name="Execution result", null=True, blank=True)
    executed_at = models.DateTimeField(verbose_name="Execution time", null=True, blank=True)

    def __str__(self):
        return f"{self.host.host_name} - {self.program_name} {self.get_action_display()}"

    class Meta:
        verbose_name = "Program management command"
        db_table = table_prefix + "program_command"
        ordering = ['-create_datetime']


class ProgramInstallPolicy(CoreModel):
    """Program install policy (rule-driven large-scale host management)"""
    POLICY_STATUS_CHOICES = [
        (0, 'Disabled'),
        (1, 'Enabled'),
        (2, 'Paused'),
    ]

    name = models.CharField(max_length=255, verbose_name="Policy name", help_text="Policy name")
    program_name = models.CharField(max_length=100, verbose_name="Program name", help_text="e.g. taurus-executor, taurus-monitor")
    version = models.CharField(max_length=50, verbose_name="Target version", help_text="Program version number")
    config = models.JSONField(verbose_name="Program config", default=dict, blank=True,
                              help_text="Program config (business config passed via env_vars)")
    auto_start = models.BooleanField(verbose_name="Auto start", default=True)
    user = models.CharField(max_length=50, verbose_name="Run user", null=True, blank=True,
                            help_text="Program run user, empty means use current Supervisor user")
    group = models.CharField(max_length=50, verbose_name="Run user group", null=True, blank=True)
    
    # Match rules (JSON format, flexible config)
    match_rules = models.JSONField(verbose_name="Match rules", default=dict, blank=True,
                                   help_text="Host match rules, e.g. {'host_type': 'linux', 'status': 1, 'host_ip_prefix': '10.0.'}")
    
    # Policy status
    status = models.IntegerField(verbose_name="Policy status", choices=POLICY_STATUS_CHOICES, default=0,
                                 help_text="0-Disabled 1-Enabled 2-Paused")
    
    # Auto apply
    auto_apply = models.BooleanField(verbose_name="Auto apply", default=True,
                                     help_text="Auto-match and apply this policy when new host registers")
    
    # Statistics info
    matched_hosts_count = models.IntegerField(verbose_name="Matched hosts count", default=0,
                                              help_text="Current number of hosts matching this policy")
    applied_hosts_count = models.IntegerField(verbose_name="Applied hosts count", default=0,
                                              help_text="Number of hosts with install config created")
    
    # Priority (lower number = higher priority)
    priority = models.IntegerField(verbose_name="Priority", default=100,
                                   help_text="Lower number = higher priority, used for priority judgment on policy conflicts")

    def __str__(self):
        return f"{self.name} - {self.program_name} v{self.version}"

    class Meta:
        verbose_name = "Program install policy"
        db_table = table_prefix + "program_install_policy"
        ordering = ['priority', '-create_datetime']


class HostHeartbeat(CoreModel):
    """Host heartbeat record"""
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='heartbeats', verbose_name="Host")
    timestamp = models.DateTimeField(verbose_name="Heartbeat time")
    supervisor_version = models.CharField(max_length=50, verbose_name="Supervisor version", null=True, blank=True)
    heartbeat_server = models.CharField(max_length=255, verbose_name="Heartbeat server", null=True, blank=True)
    cpu_usage = models.FloatField(verbose_name="CPU usage(%)", null=True, blank=True)
    memory_usage = models.FloatField(verbose_name="Memory usage(%)", null=True, blank=True)
    disk_usage = models.FloatField(verbose_name="Disk usage(%)", null=True, blank=True)
    load_average = models.CharField(max_length=50, verbose_name="System load", null=True, blank=True)
    network_rx_bytes = models.BigIntegerField(verbose_name="Network RX(bytes)", null=True, blank=True)
    network_tx_bytes = models.BigIntegerField(verbose_name="Network TX(bytes)", null=True, blank=True)
    process_count = models.IntegerField(verbose_name="Process count", null=True, blank=True)
    uptime_seconds = models.BigIntegerField(verbose_name="Uptime(seconds)", null=True, blank=True)
    supervisor_status = models.CharField(max_length=20, verbose_name="Supervisor status", default='running')

    def __str__(self):
        return f"{self.host.host_name} - {self.timestamp}"

    class Meta:
        verbose_name = "Host heartbeat record"
        db_table = table_prefix + "host_heartbeat"
        ordering = ['-timestamp']
        constraints = [
            models.UniqueConstraint(fields=['host'], name='unique_host_heartbeat')
        ]


class Editor(CoreModel):
    theme = models.CharField(max_length=255, verbose_name="Editor theme", null=True, blank=True)
    lang = models.CharField(max_length=255, verbose_name="Editor language", null=True, blank=True)
    option = models.TextField(verbose_name="Editor config", null=True, blank=True)

    class Meta:
        verbose_name = "Editor info"
        db_table = table_prefix + "editor"
        ordering = ("-create_datetime",)


class Record(CoreModel):
    uuid = models.CharField(max_length=255, verbose_name="uuid")
    host = models.ForeignKey(to=Host, related_name='records', null=True, on_delete=models.SET_NULL, )
    template = models.ForeignKey(to=Template, related_name='records', null=True,
                                 on_delete=models.SET_NULL, )
    script_type = models.CharField(max_length=255, verbose_name="Script type", null=True, blank=True)
    script_content = models.TextField(verbose_name="Script content", null=True, blank=True)
    editor = models.ForeignKey(to=Editor, related_name='records', null=True, on_delete=models.SET_NULL, )
    envs = models.TextField(verbose_name="Environment variables", null=True, blank=True)
    args = models.TextField(verbose_name="Parameters", null=True, blank=True)
    timeout = models.IntegerField(verbose_name="Timeout", default=0)
    start_datetime = models.DateTimeField(auto_now_add=True, null=True, blank=True, help_text="Start time", )
    end_datetime = models.DateTimeField(auto_now=False, null=True, blank=True, help_text="End time", )
    status = models.IntegerField(verbose_name="Status", null=True, blank=True)
    return_code = models.IntegerField(verbose_name="Return code", default=0)
    archive = models.BooleanField(verbose_name="Is archived", default=False)

    class Meta:
        verbose_name = "Execution record"
        db_table = table_prefix + "record"
        ordering = ("-create_datetime",)


class RecordDetail(CoreModel):
    record = models.ForeignKey(to=Record, related_name='record_details', null=True,
                               on_delete=models.SET_NULL, )
    seq = models.IntegerField(verbose_name="Sequence number")
    stdin = models.TextField(verbose_name="Input", null=True, blank=True)
    stdout = models.TextField(verbose_name="Output", null=True, blank=True)
    stderr = models.TextField(verbose_name="Error", null=True, blank=True)

    class Meta:
        verbose_name = "Execution record detail"
        db_table = table_prefix + "record_detail"
        ordering = ("-create_datetime",)


# ==================== Workflow orchestration related models ====================

class WorkflowCategory(CoreModel):
    """Workflow category directory (supports multi-level tree)"""
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        db_constraint=False, verbose_name="Parent category"
    )
    name = models.CharField(max_length=100, verbose_name="Category name")
    category_code = models.CharField(max_length=64, verbose_name="Category code", null=True, blank=True, help_text="Frontend friendly identifier, e.g. deploy/inspect")
    sort = models.IntegerField(default=0, verbose_name="Sort order")
    is_system = models.BooleanField(default=False, verbose_name="System built-in", help_text="System built-in categories cannot be deleted")
    remark = models.CharField(max_length=500, blank=True, verbose_name="Remark")
    reviewers = models.ManyToManyField(
        to=settings.AUTH_USER_MODEL,
        related_name='reviewable_workflow_categories',
        blank=True,
        verbose_name="Reviewer list",
        help_text="When workflows under this category are submitted for review, review tasks are assigned from these reviewers"
    )

    class Meta:
        db_table = table_prefix + "workflow_category"
        verbose_name = "Workflow category"
        verbose_name_plural = verbose_name
        ordering = ("sort", "id")

    def __str__(self):
        return self.name


class Workflow(CoreModel):
    """Workflow definition"""

    AUTH_TYPE_CHOICES = [
        ('private', 'Private'),
        ('public', 'Public'),
    ]

    STATUS_CHOICES = [
        (0, 'Enabled'),
        (1, 'Disabled'),
        (2, 'Pending approval'),
        (3, 'Archived'),
    ]

    name = models.CharField(max_length=255, verbose_name="Workflow name")
    description = models.TextField(verbose_name="Description", null=True, blank=True)
    category = models.ForeignKey(
        to='WorkflowCategory', on_delete=models.SET_NULL,
        null=True, blank=True, db_constraint=False,
        related_name='workflows', verbose_name="Workflow category"
    )
    hosts = models.ManyToManyField(Host, related_name='workflows', verbose_name="Target hosts")
    global_envs = models.JSONField(verbose_name="Global environment variables", default=dict, blank=True)

    # ===== Align with script library: visibility + status + audit toggle =====
    auth_type = models.CharField(
        max_length=20, choices=AUTH_TYPE_CHOICES, default='private', verbose_name="Auth type"
    )
    # Keep share field (backward compatible); if auth_type=public then share is synced to True
    share = models.BooleanField(verbose_name="Is shared", default=False)
    need_audit = models.BooleanField(default=False, verbose_name="Needs approval")
    custom_approver_ids = models.JSONField(
        default=list, blank=True, verbose_name="Custom approver ID list",
        help_text="Can manually specify review user ID list when saving workflow; on submit if no matching rules then creates single-level specified user review; when non-empty, even if need_audit=False but visibility=public, this approver is used first"
    )
    status = models.IntegerField(verbose_name="Status", choices=STATUS_CHOICES, default=0)

    # ===== Statistics info =====
    exec_count = models.IntegerField(default=0, verbose_name="Execution count")
    last_exec_time = models.DateTimeField(null=True, blank=True, verbose_name="Last execution time")
    last_exec_result = models.CharField(max_length=20, null=True, blank=True, verbose_name="Last execution result")
    next_exec_time = models.DateTimeField(null=True, blank=True, verbose_name="Next execution time")

    # ===== v2 DAG Engine new fields (nullable; old linear workflows all keep defaults) =====
    WORKFLOW_MODE_CHOICES = [
        ('linear', 'Linear mode (legacy, by step_order)'),
        ('dag', 'DAG mode (new, uses workflow.engine)'),
    ]
    workflow_mode = models.CharField(
        max_length=16,
        choices=WORKFLOW_MODE_CHOICES,
        default='linear',
        verbose_name="Execution mode",
        db_index=True,
    )
    # Draft DAG edited in real-time in designer; on "Publish" click snapshot to WorkflowDAGVersion
    graph_definition = models.JSONField(
        verbose_name="DAG draft (nodes + edges, edit state)",
        default=dict,
        blank=True,
    )
    graph_version = models.PositiveIntegerField(
        verbose_name="Draft version number (optimistic lock)",
        default=0,
    )
    # Current published version pointer; null for linear mode
    dag_published_version = models.ForeignKey(
        'taurus.WorkflowDAGVersion',
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name="Current published version",
        null=True,
        blank=True,
        db_column='dag_published_version_id',
        db_index=True,
    )

    # ===== DAG Engine global config =====
    FAIL_STRATEGY_CHOICES = [
        ('stop', 'Stop workflow'),
        ('continue', 'Continue execution'),
    ]
    global_timeout_sec = models.IntegerField(
        verbose_name="Node default timeout seconds (0=no default)",
        default=3600,
        help_text="Applies to node types with timeout needs (command/script/HTTP/approval etc.); if node has individual timeout config, node config takes precedence",
    )
    fail_strategy = models.CharField(
        max_length=20, choices=FAIL_STRATEGY_CHOICES, default='stop',
        verbose_name="Failure strategy",
        help_text="Global failure strategy: stop workflow or continue execution",
    )

    # ===== Workflow scheduled trigger (optional) =====
    has_schedule = models.BooleanField(default=False, verbose_name="Enable scheduled trigger")
    schedule_type = models.CharField(
        max_length=20, choices=[('cron', 'Cron expression'), ('interval', 'Fixed interval'), ('once', 'One-time')],
        null=True, blank=True, verbose_name="Schedule type"
    )
    cron_expression = models.CharField(max_length=100, null=True, blank=True, verbose_name="Cron expression")
    interval_seconds = models.IntegerField(null=True, blank=True, verbose_name="Interval seconds")
    run_once_at = models.DateTimeField(null=True, blank=True, verbose_name="One-time execution time")
    schedule_enabled = models.BooleanField(default=True, verbose_name="Schedule enabled")

    def save(self, *args, **kwargs):
        if self.auth_type == 'public':
            self.share = True
        elif self.auth_type == 'private':
            self.share = False
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Workflow"
        db_table = table_prefix + "workflow"
        ordering = ("-create_datetime",)


class WorkflowStep(CoreModel):
    """Workflow step"""
    FAILURE_STRATEGY_CHOICES = [
        ('abort', 'Abort execution'),
        ('continue', 'Continue execution'),
        ('skip', 'Skip subsequent steps'),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='steps')
    template = models.ForeignKey(Template, on_delete=models.CASCADE, verbose_name="Script template")
    step_name = models.CharField(max_length=255, verbose_name="Step name", null=True, blank=True)
    step_order = models.IntegerField(verbose_name="Execution order", default=0)
    step_envs = models.JSONField(verbose_name="Step environment variables", default=dict, blank=True)
    step_args = models.JSONField(verbose_name="Step parameters", default=list, blank=True)
    on_failure = models.CharField(max_length=20, choices=FAILURE_STRATEGY_CHOICES, default='abort', verbose_name="Failure strategy")
    timeout = models.IntegerField(verbose_name="Timeout(minutes)", default=0)

    def __str__(self):
        return f"{self.workflow.name} - Step {self.step_order}: {self.step_name or self.template.template_name}"

    class Meta:
        verbose_name = "Workflow step"
        db_table = table_prefix + "workflow_step"
        ordering = ['step_order']


class WorkflowExecution(CoreModel):
    """Workflow execution record"""
    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Executing'),
        (2, 'Completed'),
        (3, 'Failed'),
        (4, 'Cancelled'),
    ]

    FAIL_STRATEGY_CHOICES = [
        ('fail_fast', 'Fail fast (default)'),
        ('continue', 'Continue sibling nodes on failure'),
    ]
    TRIGGER_TYPE_CHOICES = [
        ('manual', 'Manual trigger'),
        ('schedule', 'Scheduled task'),
        ('api', 'API call'),
        ('dryrun', 'Dry run'),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='executions')
    status = models.IntegerField(verbose_name="Status", choices=STATUS_CHOICES, default=0)
    start_time = models.DateTimeField(verbose_name="Start time", null=True, blank=True)
    end_time = models.DateTimeField(verbose_name="End time", null=True, blank=True)
    context = models.JSONField(verbose_name="Execution context", default=dict, blank=True)
    error_message = models.TextField(verbose_name="Error message", null=True, blank=True)
    current_step = models.IntegerField(verbose_name="Current execution step (for linear mode)", default=0)

    # ===== v2 DAG Engine new fields (nullable; old linear mode keeps default/null) =====
    dag_version = models.ForeignKey(
        'taurus.WorkflowDAGVersion',
        on_delete=models.CASCADE,
        related_name='executions',
        verbose_name="DAG published version snapshot",
        null=True,
        blank=True,
        db_column='dag_version_id',
        db_index=True,
    )
    trigger_params = models.JSONField(
        verbose_name="Trigger parameters (used for interpolation during rendering)",
        default=dict,
        blank=True,
    )
    current_node_key = models.CharField(
        max_length=128,
        verbose_name="Current execution node key (used in DAG mode; linear mode still uses current_step)",
        null=True,
        blank=True,
        db_index=True,
    )
    fail_strategy = models.CharField(
        max_length=16,
        choices=FAIL_STRATEGY_CHOICES,
        default='fail_fast',
        verbose_name="Failure strategy",
    )
    trigger_type = models.CharField(
        max_length=16,
        choices=TRIGGER_TYPE_CHOICES,
        default='manual',
        verbose_name="Trigger type",
        db_index=True,
    )

    def __str__(self):
        return f"{self.workflow.name} - {self.get_status_display()}"

    class Meta:
        verbose_name = "Workflow execution record"
        db_table = table_prefix + "workflow_execution"
        ordering = ['-create_datetime']


class WorkflowStepExecution(CoreModel):
    """Workflow step execution record"""
    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Running'),
        (2, 'Completed'),
        (3, 'Failed'),
        (4, 'Skipped'),
    ]

    execution = models.ForeignKey(WorkflowExecution, on_delete=models.CASCADE, related_name='step_executions')
    step = models.ForeignKey(WorkflowStep, on_delete=models.CASCADE, related_name='executions')
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='step_executions', verbose_name="Target host")
    status = models.IntegerField(verbose_name="Status", choices=STATUS_CHOICES, default=0)
    start_time = models.DateTimeField(verbose_name="Start time", null=True, blank=True)
    end_time = models.DateTimeField(verbose_name="End time", null=True, blank=True)
    return_code = models.IntegerField(verbose_name="Return code", default=0)
    stdout = models.TextField(verbose_name="Standard output", null=True, blank=True)
    stderr = models.TextField(verbose_name="Standard error", null=True, blank=True)
    error_message = models.TextField(verbose_name="Error message", null=True, blank=True)

    def __str__(self):
        return f"{self.step} - {self.host.host_ip} - {self.get_status_display()}"

    class Meta:
        verbose_name = "Workflow step execution record"
        db_table = table_prefix + "workflow_step_execution"
        ordering = ['step__step_order']


# ==================== Schedule related models ====================

class Schedule(CoreModel):
    """Schedule task"""
    SCHEDULE_TYPE_CHOICES = [
        ('cron', 'Cron expression'),
        ('interval', 'Fixed interval'),
        ('once', 'One-time'),
    ]
    
    TARGET_TYPE_CHOICES = [
        ('script', 'Single script'),
        ('workflow', 'Workflow'),
    ]
    
    STATUS_CHOICES = [
        (0, 'Disabled'),
        (1, 'Enabled'),
    ]
    
    name = models.CharField(max_length=255, verbose_name="Task name")
    description = models.TextField(verbose_name="Description", null=True, blank=True)
    schedule_type = models.CharField(max_length=20, choices=SCHEDULE_TYPE_CHOICES, verbose_name="Schedule type")
    cron_expression = models.CharField(max_length=100, verbose_name="Cron expression", null=True, blank=True)
    interval_seconds = models.IntegerField(verbose_name="Interval seconds", null=True, blank=True)
    run_once_at = models.DateTimeField(verbose_name="One-time execution time", null=True, blank=True)
    
    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES, verbose_name="Target type")
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name='schedules', verbose_name="Script template", null=True, blank=True)
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='schedules', verbose_name="Workflow", null=True, blank=True)
    dag_version = models.ForeignKey(
        'taurus.WorkflowDAGVersion', on_delete=models.SET_NULL,
        related_name='+', verbose_name="DAG version (empty = use current published version)",
        null=True, blank=True, db_constraint=False,
    )
    hosts = models.ManyToManyField(Host, related_name='schedules', verbose_name="Target hosts")
    
    envs = models.JSONField(verbose_name="Environment variables", default=dict, blank=True)
    args = models.JSONField(verbose_name="Arguments", default=list, blank=True)
    
    status = models.IntegerField(verbose_name="Status", choices=STATUS_CHOICES, default=1)
    last_run_time = models.DateTimeField(verbose_name="Last run time", null=True, blank=True)
    next_run_time = models.DateTimeField(verbose_name="Next run time", null=True, blank=True)
    
    celery_task_id = models.CharField(max_length=255, verbose_name="Celery task ID", null=True, blank=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name = "Schedule task"
        db_table = table_prefix + "schedule"
        ordering = ['-create_datetime']


class ScheduleExecution(CoreModel):
    """Schedule task execution record"""
    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Running'),
        (2, 'Completed'),
        (3, 'Failed'),
    ]
    
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='executions')
    status = models.IntegerField(verbose_name="Status", choices=STATUS_CHOICES, default=0)
    start_time = models.DateTimeField(verbose_name="Start time", null=True, blank=True)
    end_time = models.DateTimeField(verbose_name="End time", null=True, blank=True)
    result = models.JSONField(verbose_name="Execution result", default=dict, blank=True)
    error_message = models.TextField(verbose_name="Error message", null=True, blank=True)
    
    def __str__(self):
        return f"{self.schedule.name} - {self.get_status_display()}"
    
    class Meta:
        verbose_name = "Schedule task execution record"
        db_table = table_prefix + "schedule_execution"
        ordering = ['-create_datetime']


class HostLog(CoreModel):
    """Host log record (Supervisor remote log forwarding)"""
    LOG_LEVEL_CHOICES = [
        ('DEBUG', 'DEBUG'),
        ('INFO', 'INFO'),
        ('WARNING', 'WARNING'),
        ('ERROR', 'ERROR'),
        ('CRITICAL', 'CRITICAL'),
    ]
    
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='logs', verbose_name="Associated host")
    program_name = models.CharField(max_length=100, verbose_name="Program name", null=True, blank=True,
                                    help_text="e.g. taurus-executor, taurus-monitor; empty means Supervisor itself")
    log_level = models.CharField(max_length=20, verbose_name="Log level", choices=LOG_LEVEL_CHOICES, default='INFO')
    log_time = models.DateTimeField(verbose_name="Log time", help_text="Time when log was generated")
    message = models.TextField(verbose_name="Log content")
    source_file = models.CharField(max_length=255, verbose_name="Source file", null=True, blank=True)
    source_line = models.IntegerField(verbose_name="Source line number", null=True, blank=True)
    process_id = models.IntegerField(verbose_name="Process ID", null=True, blank=True)
    thread_id = models.BigIntegerField(verbose_name="Thread ID", null=True, blank=True)
    extra_info = models.JSONField(verbose_name="Extra info", default=dict, blank=True)
    
    def __str__(self):
        return f"{self.host.host_name} - {self.log_level} - {self.message[:50]}"
    
    class Meta:
        verbose_name = "Host log"
        db_table = table_prefix + "host_log"
        ordering = ['-log_time', '-create_datetime']
        indexes = [
            models.Index(fields=['host', 'log_time']),
            models.Index(fields=['log_level']),
            models.Index(fields=['program_name']),
        ]


class LogCommand(CoreModel):
    """Log collection control command (used by Server to send log collection commands to Supervisor)"""
    ACTION_CHOICES = [
        ('start', 'Start collection'),
        ('stop', 'Stop collection'),
    ]

    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Running'),
        (2, 'Success'),
        (3, 'Failed'),
    ]

    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='log_commands', verbose_name="Associated host")
    action = models.CharField(max_length=20, verbose_name="Action type", choices=ACTION_CHOICES, default='start')
    min_level = models.CharField(max_length=20, verbose_name="Minimum log level", default='INFO',
                                 help_text="DEBUG/INFO/WARNING/ERROR/CRITICAL")
    programs = models.JSONField(verbose_name="Program name list", default=list, blank=True,
                                help_text="Empty list means collect logs of all programs")
    duration = models.PositiveIntegerField(verbose_name="Collection duration (seconds)", default=1800,
                                           help_text="Auto-stop collection time, 0 means continuous until manual stop")
    status = models.IntegerField(verbose_name="Execution status", choices=STATUS_CHOICES, default=0)
    dispatched = models.BooleanField(verbose_name="Is dispatched", default=False)
    dispatched_at = models.DateTimeField(verbose_name="Dispatch time", null=True, blank=True)
    result_message = models.TextField(verbose_name="Execution result", null=True, blank=True)
    executed_at = models.DateTimeField(verbose_name="Execution time", null=True, blank=True)

    def __str__(self):
        return f"{self.host.host_name} - Log {self.get_action_display()}"

    class Meta:
        verbose_name = "Log collection command"
        db_table = table_prefix + "log_command"


class OpsExecution(CoreModel):
    """Ops center execution task (command/script/file upload/file download)"""
    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Running'),
        (2, 'Completed'),
        (3, 'Failed'),
        (4, 'Interrupted'),
        (5, 'Approving'),
        (6, 'Approval rejected'),
    ]

    EXECUTION_TYPE_CHOICES = [
        ('command', 'Command'),
        ('script', 'Script'),
        ('upload', 'File upload'),
        ('download', 'File download'),
    ]

    execution_id = models.CharField(max_length=64, unique=True, verbose_name="Execution ID")
    batch_id = models.CharField(max_length=64, null=True, blank=True, verbose_name="Batch ID")
    execution_type = models.CharField(max_length=20, choices=EXECUTION_TYPE_CHOICES, verbose_name="Execution type")
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='ops_executions', verbose_name="Target host")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='ops_executions', verbose_name="Execution user")

    # Command/script content
    command = models.CharField(max_length=1000, verbose_name="Command", null=True, blank=True)
    script_type = models.CharField(max_length=20, verbose_name="Script type", null=True, blank=True)
    script_content = models.TextField(verbose_name="Script content", null=True, blank=True)
    # File operation path
    file_path = models.CharField(max_length=1000, verbose_name="File path", null=True, blank=True)
    file_size = models.BigIntegerField(verbose_name="File size (bytes)", null=True, blank=True)
    args = models.JSONField(verbose_name="Arguments", default=list, blank=True)
    working_directory = models.CharField(max_length=500, verbose_name="Working directory", null=True, blank=True)
    timeout_seconds = models.IntegerField(verbose_name="Timeout (seconds)", default=300)
    environment = models.JSONField(verbose_name="Environment variables", default=dict, blank=True)
    use_shell = models.BooleanField(verbose_name="Use shell mode", default=True)
    merge_streams = models.BooleanField(verbose_name="Merge output streams", default=False)
    load_profile = models.CharField(max_length=10, verbose_name="Profile load mode", default="false")
    privileged = models.BooleanField(verbose_name="Privileged execution", default=False)
    su_user = models.CharField(max_length=64, verbose_name="su target user", null=True, blank=True)

    # Execution strategy snapshot (used to restore config when re-running)
    FAIL_STRATEGY_CHOICES = [
        ('stop', 'Stop all on failure'),
        ('continue', 'Skip failure and continue'),
        ('abort', 'Abort (legacy compat)'),
    ]
    EXEC_MODE_CHOICES = [
        ('serial', 'Serial execution'),
        ('parallel', 'Parallel execution'),
        ('pilot', 'Canary release'),
    ]
    exec_mode = models.CharField(max_length=20, choices=EXEC_MODE_CHOICES, default='parallel', verbose_name="Execution mode")
    concurrent = models.IntegerField(verbose_name="Concurrency count", default=10)
    fail_strategy = models.CharField(max_length=20, choices=FAIL_STRATEGY_CHOICES, default='continue', verbose_name="Failure strategy")
    pilot_count = models.IntegerField(verbose_name="Canary verify host count", default=2)
    pilot_success_rate = models.IntegerField(verbose_name="Canary success threshold (%)", default=100)
    need_audit = models.BooleanField(verbose_name="Requires approval", default=False)
    auto_notify = models.BooleanField(verbose_name="Auto notify", default=False)

    # Approval config snapshot (used to restore last reviewer/mode/description when re-running)
    APPROVAL_MODE_CHOICES = [
        ('any', 'OR-sign (any one passes)'),
        ('all', 'AND-sign (all must pass)'),
    ]
    approval_mode = models.CharField(
        max_length=10, choices=APPROVAL_MODE_CHOICES, null=True, blank=True,
        verbose_name="Approval mode (snapshot)"
    )
    approver_ids = models.JSONField(
        default=list, blank=True, verbose_name="OR-sign approver ID list (snapshot)",
        help_text="JSON: [user_id, ...]"
    )
    countersign_ids = models.JSONField(
        default=list, blank=True, verbose_name="AND-sign approver ID list (snapshot)",
        help_text="JSON: [user_id, ...]"
    )
    submit_desc = models.TextField(null=True, blank=True, verbose_name="Approval submission description (snapshot)")

    # Execution status
    status = models.IntegerField(verbose_name="Execution status", choices=STATUS_CHOICES, default=0)
    exit_code = models.IntegerField(verbose_name="Exit code", null=True, blank=True)
    error_message = models.TextField(verbose_name="Error message", null=True, blank=True)

    # Output buffer (JSON format)
    output_buffer = models.JSONField(verbose_name="Output buffer", default=list, blank=True)

    # Timestamps
    started_at = models.DateTimeField(verbose_name="Start time", null=True, blank=True)
    finished_at = models.DateTimeField(verbose_name="End time", null=True, blank=True)

    def __str__(self):
        return f"{self.execution_id} - {self.get_execution_type_display()}"

    class Meta:
        verbose_name = "Ops execution task"
        db_table = table_prefix + "ops_execution"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['execution_id']),
            models.Index(fields=['host', 'status']),
            models.Index(fields=['user', 'create_datetime']),
        ]


class OpsExecutionApproval(CoreModel):
    """Ops execution task approval instance

    When script execution has approval enabled, create an approval instance first,
    then automatically trigger execution after approval passes.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('executing', 'Running'),
        ('done', 'Completed'),
        ('failed', 'Execution failed'),
    ]

    batch_id = models.CharField(max_length=64, verbose_name="Batch ID", null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="Approval status")

    SOURCE_TYPE_CHOICES = [
        ('workflow', 'Workflow approval'),
        ('script', 'Script execution'),
        ('command', 'Command execution'),
    ]
    source_type = models.CharField(
        max_length=20, choices=SOURCE_TYPE_CHOICES, default='script',
        verbose_name="Approval source type"
    )
    related_name = models.CharField(
        max_length=200, blank=True, null=True,
        verbose_name="Related name",
        help_text="Script name / workflow name / command summary"
    )
    related_desc = models.TextField(
        blank=True, null=True,
        verbose_name="Related description",
        help_text="Script description / workflow description / step description"
    )

    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='submitted_exec_approvals',
        verbose_name="Submitter"
    )
    submitter_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Submitter name")
    submit_desc = models.TextField(blank=True, null=True, verbose_name="Submission description")

    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_exec_approvals',
        verbose_name="Approver (final pass/reject operator)"
    )
    approver_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Approver name")
    approve_reason = models.TextField(blank=True, null=True, verbose_name="Final approval opinion")
    approve_time = models.DateTimeField(null=True, blank=True, verbose_name="Final approval time")

    # ========== Multi-approver support (dynamic OR-sign/AND-sign) ==========
    APPROVAL_MODE_CHOICES = [
        ('any', 'OR-sign (any one passes)'),
        ('all', 'AND-sign (all must pass)'),
    ]
    approval_mode = models.CharField(
        max_length=10, choices=APPROVAL_MODE_CHOICES, default='any',
        verbose_name="Multi-approver mode"
    )
    candidate_approvers = models.JSONField(
        default=list, blank=True, verbose_name="Candidate approver list",
        help_text="JSON: [{user_id, username, name}, ...], higher priority than single approver field"
    )
    approval_records = models.JSONField(
        default=list, blank=True, verbose_name="Approval action records",
        help_text="JSON: [{user_id, username, action(approve/reject/delegate/add_sign), reason, operate_time, ...}]"
    )

    finish_time = models.DateTimeField(null=True, blank=True, verbose_name="Finish time")

    # Execution parameter snapshot
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name='exec_approvals', verbose_name="Target host")
    EXECUTION_TYPE_CHOICES = [
        ('command', 'Command'),
        ('script', 'Script'),
        ('upload', 'File upload'),
        ('download', 'File download'),
    ]
    execution_type = models.CharField(max_length=20, choices=EXECUTION_TYPE_CHOICES, default='script', verbose_name="Execution type")
    command = models.CharField(max_length=1000, verbose_name="Command", null=True, blank=True)
    use_shell = models.BooleanField(verbose_name="Use shell mode (command execution)", default=True)
    script_type = models.CharField(max_length=20, verbose_name="Script type", null=True, blank=True)
    script_content = models.TextField(verbose_name="Script content", null=True, blank=True)
    args = models.JSONField(verbose_name="Arguments", default=list, blank=True)
    working_directory = models.CharField(max_length=500, verbose_name="Working directory", null=True, blank=True)
    timeout_seconds = models.IntegerField(verbose_name="Timeout (seconds)", default=300)
    environment = models.JSONField(verbose_name="Environment variables", default=dict, blank=True)
    merge_streams = models.BooleanField(verbose_name="Merge output streams", default=False)
    load_profile = models.CharField(max_length=10, verbose_name="Profile load mode", default="false")
    privileged = models.BooleanField(verbose_name="Privileged execution", default=False)
    su_user = models.CharField(max_length=64, verbose_name="su target user", null=True, blank=True)
    # Execution strategy snapshot
    FAIL_STRATEGY_CHOICES = [
        ('stop', 'Stop all on failure'),
        ('continue', 'Skip failure and continue'),
    ]
    exec_mode = models.CharField(max_length=20, verbose_name="Execution mode", default="parallel")
    concurrency = models.IntegerField(verbose_name="Concurrency count", default=10)
    fail_strategy = models.CharField(max_length=20, choices=FAIL_STRATEGY_CHOICES, default='continue', verbose_name="Failure strategy")
    pilot_count = models.IntegerField(verbose_name="Canary verify host count", default=2)
    pilot_success_rate = models.IntegerField(verbose_name="Canary success threshold (%)", default=100)
    auto_notify = models.BooleanField(verbose_name="Auto notify on completion", default=False)
    target_hosts_count = models.IntegerField(verbose_name="Target host count", default=1)

    # Execution record created after approval passes
    ops_execution = models.ForeignKey(
        OpsExecution, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approval_records',
        verbose_name="Related execution record"
    )

    def __str__(self):
        return f"{self.host.host_ip if self.host_id else '?'} - {self.get_status_display()}"

    class Meta:
        db_table = table_prefix + "ops_execution_approval"
        verbose_name = "Execution task approval"
        verbose_name_plural = verbose_name
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['submitter']),
            models.Index(fields=['batch_id']),
        ]


# ========================== Script library management module ==========================

class ScriptCategory(CoreModel):
    """Script category directory"""
    CATEGORY_TYPE_CHOICES = [
        ('system', 'System category'),
        ('custom', 'User category'),
    ]

    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        db_constraint=False, verbose_name="Parent category"
    )
    name = models.CharField(max_length=100, verbose_name="Category name")
    category_type = models.CharField(
        max_length=20, choices=CATEGORY_TYPE_CHOICES,
        default='custom', verbose_name="Category type"
    )
    sort = models.IntegerField(default=0, verbose_name="Sort order")
    is_system = models.BooleanField(default=False, verbose_name="System built-in")
    is_virtual = models.BooleanField(default=False, verbose_name="Virtual directory")
    remark = models.CharField(max_length=500, blank=True, verbose_name="Remark")
    reviewers = models.ManyToManyField(
        to=settings.AUTH_USER_MODEL,
        related_name='reviewable_categories',
        blank=True,
        verbose_name="Reviewer list",
        help_text="When scripts under this category are submitted for review, assign review tasks from these reviewers"
    )

    class Meta:
        db_table = table_prefix + "script_category"
        verbose_name = "Script category"
        ordering = ("sort", "id")

    def __str__(self):
        return self.name


class Script(CoreModel):
    """Script library management - script main table"""

    SCRIPT_TYPE_CHOICES = [
        ('Shell', 'Shell'),
        ('Python3', 'Python3'),
        ('PowerShell', 'PowerShell'),
        ('Bat', 'Bat'),
        ('SQL', 'SQL'),
    ]

    AUTH_TYPE_CHOICES = [
        ('private', 'Private'),
        ('public', 'Public'),
        ('preset', 'Preset'),
    ]

    STATUS_CHOICES = [
        (0, 'Normal enabled'),
        (1, 'Disabled'),
        (2, 'Pending approval'),
        (3, 'Archived'),
    ]

    FAIL_STRATEGY_CHOICES = [
        ('stop', 'Stop execution'),
        ('continue', 'Continue execution'),
    ]

    name = models.CharField(max_length=200, verbose_name="Script name")
    script_type = models.CharField(
        max_length=20, choices=SCRIPT_TYPE_CHOICES, verbose_name="Script type"
    )
    category = models.ForeignKey(
        to='ScriptCategory', on_delete=models.SET_NULL,
        null=True, blank=True, db_constraint=False,
        verbose_name="Script category"
    )
    auth_type = models.CharField(
        max_length=20, choices=AUTH_TYPE_CHOICES, default='private', verbose_name="Authorization type"
    )
    tags = models.CharField(max_length=255, blank=True, null=True, verbose_name="Tags",
                           help_text="Multiple tags separated by commas")
    desc = models.TextField(blank=True, null=True, verbose_name="Script description")
    content = models.TextField(verbose_name="Script content")
    current_version = models.CharField(max_length=50, default='V1.0', verbose_name="Current version")

    # Execution config
    timeout = models.IntegerField(default=300, verbose_name="Timeout (seconds)")
    concurrent = models.IntegerField(default=10, verbose_name="Concurrency count")
    fail_strategy = models.CharField(
        max_length=20, choices=FAIL_STRATEGY_CHOICES,
        default='continue', verbose_name="Failure strategy"
    )

    # Security config
    open_risk_check = models.BooleanField(default=True, verbose_name="Enable risk detection")
    need_audit = models.BooleanField(default=False, verbose_name="Requires approval")
    log_retention = models.IntegerField(default=3650, verbose_name="Log retention (days)")

    # Parameter config (JSON storage)
    script_params = models.JSONField(
        default=list, blank=True, verbose_name="Script parameters",
        help_text="Format: [{key, value, desc}]"
    )
    script_envs = models.JSONField(
        default=list, blank=True, verbose_name="Environment variables",
        help_text="Format: [{key, value, desc}] — injected into process as environment variables during execution"
    )

    # Status
    status = models.IntegerField(
        verbose_name="Status", choices=STATUS_CHOICES, default=0
    )

    # Statistics
    exec_count = models.IntegerField(default=0, verbose_name="Execution count")
    last_exec_time = models.DateTimeField(null=True, blank=True, verbose_name="Last execution time")

    # Official script related
    is_official = models.BooleanField(default=False, verbose_name="Is official built-in")
    source = models.CharField(max_length=50, default='user', verbose_name="Source",
                              help_text="user/user_upload/official_import")
    source_url = models.CharField(max_length=500, blank=True, default='', verbose_name="Source URL")
    license_type = models.CharField(max_length=50, blank=True, default='', verbose_name="License type")
    supported_systems = models.CharField(max_length=255, blank=True, default='', verbose_name="Supported systems",
                                         help_text="Multiple systems separated by commas, e.g. CentOS 7+,Ubuntu 20.04+")
    risk_level = models.CharField(max_length=20, blank=True, default='', verbose_name="Risk level",
                                  help_text="low/medium/high, cached after calculated by detection engine")
    usage_count = models.IntegerField(default=0, verbose_name="Usage count")
    official_version = models.CharField(max_length=50, blank=True, default='', verbose_name="Official version")
    changelog = models.TextField(blank=True, default='', verbose_name="Update notes")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Script library"
        db_table = table_prefix + "script"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['script_type']),
            models.Index(fields=['category']),
            models.Index(fields=['status']),
            models.Index(fields=['name']),
        ]


class ScriptVersion(CoreModel):
    """Script version management"""

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='versions', verbose_name="Script"
    )
    version = models.CharField(max_length=50, verbose_name="Version number")
    content = models.TextField(verbose_name="Script content")
    desc = models.TextField(blank=True, null=True, verbose_name="Update notes")
    is_current = models.BooleanField(default=False, verbose_name="Is current version")

    def __str__(self):
        return f"{self.script.name} - {self.version}"

    class Meta:
        verbose_name = "Script version"
        db_table = table_prefix + "script_version"
        ordering = ['-create_datetime']


class ScriptPermission(CoreModel):
    """Script permission configuration"""

    SUBJECT_TYPE_CHOICES = [
        ('user', 'User'),
        ('role', 'Role'),
    ]

    AUTH_LEVEL_CHOICES = [
        ('view', 'View'),
        ('exec', 'Execute'),
        ('edit', 'Edit'),
    ]

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='permissions', verbose_name="Script"
    )
    subject_type = models.CharField(
        max_length=20, choices=SUBJECT_TYPE_CHOICES,
        default='user', verbose_name="Subject type"
    )
    subject_id = models.CharField(max_length=100, verbose_name="Subject ID")
    auth_level = models.CharField(
        max_length=20, choices=AUTH_LEVEL_CHOICES,
        default='view', verbose_name="Permission level"
    )
    grant_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='script_permissions_granted',
        verbose_name="Grantor"
    )

    def __str__(self):
        return f"{self.script.name} - {self.subject_type} - {self.auth_level}"

    class Meta:
        verbose_name = "Script permission config"
        db_table = table_prefix + "script_permission"
        unique_together = ('script', 'subject_type', 'subject_id', 'auth_level')


class ScriptTask(CoreModel):
    """Script schedule task"""

    SCHEDULE_TYPE_CHOICES = [
        ('cron', 'Cron expression'),
        ('interval', 'Fixed interval'),
        ('once', 'One-time'),
    ]

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='tasks', verbose_name="Script"
    )
    name = models.CharField(max_length=200, verbose_name="Task name")
    description = models.TextField(blank=True, null=True, verbose_name="Task description")

    # Schedule config
    schedule_type = models.CharField(
        max_length=20, choices=SCHEDULE_TYPE_CHOICES,
        default='cron', verbose_name="Schedule type"
    )
    cron_expression = models.CharField(
        max_length=100, blank=True, null=True,
        verbose_name="Cron expression", help_text="Format: 0 0 2 * * ?"
    )
    interval_seconds = models.IntegerField(
        null=True, blank=True, verbose_name="Interval seconds",
        help_text="Only effective when schedule type is fixed interval"
    )
    run_once_at = models.DateTimeField(
        null=True, blank=True, verbose_name="One-time execution time"
    )

    # Target and execution config
    hosts = models.JSONField(
        default=list, blank=True, verbose_name="Target host list",
        help_text="Format: [host_uuid, host_uuid]"
    )
    timeout = models.IntegerField(default=300, verbose_name="Timeout (seconds)")
    fail_notify = models.BooleanField(default=True, verbose_name="Failure notification")
    enabled = models.BooleanField(default=True, verbose_name="Is enabled")

    # Runtime parameters
    envs = models.JSONField(
        default=dict, blank=True, verbose_name="Environment variables",
        help_text="Format: {key: value}"
    )
    args = models.JSONField(
        default=list, blank=True, verbose_name="Execution arguments",
        help_text="Format: [arg1, arg2]"
    )

    # Execution info
    exec_count = models.IntegerField(default=0, verbose_name="Execution count")
    last_exec_time = models.DateTimeField(null=True, blank=True, verbose_name="Last execution time")
    last_exec_result = models.CharField(
        max_length=20, null=True, blank=True,
        choices=[('success', 'Success'), ('fail', 'Fail')],
        verbose_name="Last execution result"
    )
    next_exec_time = models.DateTimeField(null=True, blank=True, verbose_name="Next execution time")

    @property
    def host_count(self):
        return len(self.hosts) if self.hosts else 0

    @property
    def schedule_type_display(self):
        return dict(self.SCHEDULE_TYPE_CHOICES).get(self.schedule_type, self.schedule_type)

    @property
    def creator_name(self):
        return self.creator.username if self.creator else '-'

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Script schedule task"
        db_table = table_prefix + "script_task"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['script']),
            models.Index(fields=['enabled']),
            models.Index(fields=['schedule_type']),
        ]


class ScriptTaskExecution(CoreModel):
    """Script schedule task execution record"""

    STATUS_CHOICES = [
        (0, 'Pending'),
        (1, 'Running'),
        (2, 'Success'),
        (3, 'Failed'),
    ]

    TRIGGER_TYPE_CHOICES = [
        ('schedule', 'Schedule trigger'),
        ('manual', 'Manual execution'),
        ('api', 'API trigger'),
        ('manual_debug', 'Debug trigger'),
        ('compensate', 'Compensate trigger'),
    ]

    task = models.ForeignKey(
        ScriptTask, on_delete=models.CASCADE,
        related_name='executions', verbose_name="Schedule task"
    )
    status = models.IntegerField(
        verbose_name="Status", choices=STATUS_CHOICES, default=0
    )
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="Start time")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="End time")
    duration = models.IntegerField(
        null=True, blank=True, verbose_name="Execution duration (seconds)"
    )
    trigger_type = models.CharField(
        max_length=20, default='schedule',
        choices=TRIGGER_TYPE_CHOICES,
        verbose_name="Trigger mode"
    )
    result = models.JSONField(
        default=dict, blank=True, verbose_name="Execution result"
    )
    error_message = models.TextField(blank=True, null=True, verbose_name="Error message")
    executed_hosts = models.JSONField(
        default=list, blank=True, verbose_name="Executed host list"
    )

    @property
    def status_display(self):
        return dict(self.STATUS_CHOICES).get(self.status, str(self.status))

    @property
    def trigger_type_display(self):
        return dict(self.TRIGGER_TYPE_CHOICES).get(
            self.trigger_type, self.trigger_type
        )

    @property
    def success_host_count(self):
        if self.result and isinstance(self.result, dict):
            return len(self.result.get('success_hosts', []))
        return 0

    @property
    def failed_host_count(self):
        if self.result and isinstance(self.result, dict):
            return len(self.result.get('failed_hosts', []))
        return 0

    @property
    def creator_name(self):
        return self.creator.username if self.creator else '-'

    def __str__(self):
        return f"{self.task.name} - {self.get_status_display()}"

    class Meta:
        verbose_name = "Script schedule task execution record"
        db_table = table_prefix + "script_task_execution"
        ordering = ['-create_datetime']


class ScriptAudit(CoreModel):
    """Script audit log"""

    OPER_TYPE_CHOICES = [
        ('create', 'Create'),
        ('edit', 'Edit'),
        ('delete', 'Delete'),
        ('exec', 'Execute'),
        ('rollback', 'Rollback'),
        ('status', 'Status change'),
        ('copy', 'Copy'),
    ]

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='audits', verbose_name="Script"
    )
    script_version = models.CharField(max_length=50, blank=True, null=True, verbose_name="Script version")
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='script_audits',
        verbose_name="Operator"
    )
    operator_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Operator name")
    oper_type = models.CharField(
        max_length=20, choices=OPER_TYPE_CHOICES,
        verbose_name="Operation type"
    )
    detail = models.TextField(blank=True, null=True, verbose_name="Operation detail")
    client_ip = models.CharField(max_length=100, blank=True, null=True, verbose_name="Client IP")

    def __str__(self):
        return f"{self.script.name} - {self.oper_type}"

    class Meta:
        verbose_name = "Script audit log"
        db_table = table_prefix + "script_audit"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['script', 'oper_type']),
            models.Index(fields=['operator']),
        ]


class ScriptApprove(CoreModel):
    """Script approval record"""

    RISK_LEVEL_CHOICES = [
        ('low', 'Low risk'),
        ('medium', 'Medium risk'),
        ('high', 'High risk'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='approvals', verbose_name="Script"
    )
    script_version = models.CharField(max_length=50, blank=True, null=True, verbose_name="Script version")
    risk_level = models.CharField(
        max_length=20, choices=RISK_LEVEL_CHOICES,
        default='low', verbose_name="Risk level"
    )
    risk_points = models.JSONField(
        default=list, blank=True, verbose_name="Risk point list"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Approval status"
    )
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='script_approvals_submitted',
        verbose_name="Submitter"
    )
    submitter_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Submitter name")
    submit_desc = models.TextField(blank=True, null=True, verbose_name="Submission description")
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='script_approvals_approved',
        verbose_name="Approver"
    )
    approver_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Approver name")
    approve_time = models.DateTimeField(null=True, blank=True, verbose_name="Approval time")
    approve_reason = models.TextField(blank=True, null=True, verbose_name="Approval reason")

    def __str__(self):
        return f"{self.script.name} - {self.status}"

    class Meta:
        verbose_name = "Script approval record"
        db_table = table_prefix + "script_approve"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['script', 'status']),
            models.Index(fields=['submitter']),
        ]


class ScriptApprovalRule(CoreModel):
    """Script approval rule
    
    Match from top to bottom by priority, the first matching rule takes effect.
    Supports multi-condition groups, AND within group, OR between groups.
    """

    name = models.CharField(max_length=100, verbose_name="Rule name")
    description = models.TextField(blank=True, verbose_name="Rule description")

    condition_groups = models.JSONField(
        default=list, blank=True, verbose_name="Condition group list",
        help_text="""
        Conditions within a group are AND relationships, between groups are OR relationships (any condition group match means rule match).
        Supported condition fields per group (empty means no restriction):
        - category_ids: [1, 2, 3]         # Category ID list (including subcategories)
        - script_types: ["Shell", "SQL"]  # Script type list
        - risk_levels: ["high", "medium"] # Risk level list
        - min_risk_points: 3              # Minimum risk point count (greater than or equal)
        - auth_types: ["public", "preset"]    # Authorization type list
        - submitter_roles: ["dev"]        # Submitter role code list
        Example: [{"category_ids": [5], "risk_levels": ["high"]}, {"script_types": ["SQL"]}]
        """
    )

    priority = models.IntegerField(
        default=100, verbose_name="Priority",
        help_text="Lower number means higher priority, match from top to bottom by priority"
    )

    is_active = models.BooleanField(default=True, verbose_name="Is enabled")

    def __str__(self):
        return self.name

    class Meta:
        db_table = table_prefix + "script_approval_rule"
        verbose_name = "Script approval rule"
        ordering = ['priority', 'id']
        indexes = [
            models.Index(fields=['is_active', 'priority']),
        ]


class ScriptApprovalNode(CoreModel):
    """Approval node definition (review steps under a rule)"""

    APPROVER_TYPE_CHOICES = [
        ('category_reviewer', 'Category reviewer'),
        ('specific_users', 'Specific users'),
        ('role', 'Specific role'),
        ('submitter_manager', 'Submitter manager'),
    ]

    APPROVAL_MODE_CHOICES = [
        ('any', 'OR-sign'),
        ('all', 'AND-sign'),
        ('first', 'First-sign'),
    ]

    rule = models.ForeignKey(
        ScriptApprovalRule, on_delete=models.CASCADE,
        related_name='nodes', verbose_name="Associated rule"
    )

    node_name = models.CharField(
        max_length=100, verbose_name="Node name",
        help_text="e.g.: Level 1 review, Security review, DBA review, etc."
    )

    approver_type = models.CharField(
        max_length=30, choices=APPROVER_TYPE_CHOICES,
        verbose_name="Approver type"
    )

    approver_config = models.JSONField(
        default=dict, blank=True, verbose_name="Approver config",
        help_text="""
        category_reviewer: {}                         # No extra config needed
        specific_users: {"user_ids": [1, 2, 3]}       # Specific user ID list
        role: {"role_codes": ["dba", "security"]}     # Specific role code list
        submitter_manager: {"levels": 1}              # Manager levels up (1 = direct report)
        """
    )

    approval_mode = models.CharField(
        max_length=10, choices=APPROVAL_MODE_CHOICES,
        default='any', verbose_name="Approval mode"
    )

    step_order = models.IntegerField(
        default=1, verbose_name="Execution order",
        help_text="Lower number executes earlier"
    )

    def __str__(self):
        return f"{self.rule.name} - {self.node_name}"

    class Meta:
        db_table = table_prefix + "script_approval_node"
        verbose_name = "Approval node"
        ordering = ['step_order', 'id']
        indexes = [
            models.Index(fields=['rule', 'step_order']),
        ]


class ScriptApprovalInstance(CoreModel):
    """Approval process instance
    
    Process instance generated when a script is submitted for review,
    copies the node structure from the matched rule as a snapshot for this approval.
    """

    STATUS_CHOICES = [
        ('pending', 'Under review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    script = models.ForeignKey(
        Script, on_delete=models.CASCADE,
        related_name='approval_instances', verbose_name="Script"
    )
    script_version = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Script version"
    )

    rule = models.ForeignKey(
        ScriptApprovalRule, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="Matched rule"
    )
    rule_name = models.CharField(
        max_length=100, blank=True, verbose_name="Rule name snapshot"
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Process status"
    )

    current_node_index = models.IntegerField(
        default=0, verbose_name="Current approval node index"
    )

    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='submitted_approvals',
        verbose_name="Submitter"
    )
    submitter_name = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Submitter name"
    )
    submit_desc = models.TextField(blank=True, null=True, verbose_name="Submission description")

    risk_level = models.CharField(
        max_length=20, blank=True, null=True, verbose_name="Risk level"
    )
    risk_points = models.JSONField(
        default=list, blank=True, verbose_name="Risk point list snapshot"
    )

    finish_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Finish time"
    )

    def __str__(self):
        return f"{self.script.name} - {self.get_status_display()}"

    class Meta:
        db_table = table_prefix + "script_approval_instance"
        verbose_name = "Approval process instance"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['script', 'status']),
            models.Index(fields=['submitter']),
            models.Index(fields=['status']),
        ]


class ScriptApprovalNodeExecution(CoreModel):
    """Approval node execution record"""

    STATUS_CHOICES = [
        ('pending', 'Pending review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('skipped', 'Skipped'),
    ]

    instance = models.ForeignKey(
        ScriptApprovalInstance, on_delete=models.CASCADE,
        related_name='node_executions', verbose_name="Associated process instance"
    )

    node_name = models.CharField(max_length=100, verbose_name="Node name")
    approver_type = models.CharField(max_length=30, verbose_name="Approver type")
    approver_config = models.JSONField(
        default=dict, blank=True, verbose_name="Approver config snapshot"
    )
    approval_mode = models.CharField(max_length=10, verbose_name="Approval mode")
    step_order = models.IntegerField(verbose_name="Node order")

    candidate_approvers = models.JSONField(
        default=list, blank=True, verbose_name="Candidate approver list",
        help_text="Format: [{user_id, username, name}]"
    )

    approval_records = models.JSONField(
        default=list, blank=True, verbose_name="Approval action records",
        help_text="Format: [{user_id, username, action: approve/reject/delegate/add_sign, reason, operate_time, target_user}]"
    )

    delegate_records = models.JSONField(
        default=list, blank=True, verbose_name="Delegate records",
        help_text="Format: [{from_user_id, from_username, to_user_id, to_username, reason, operate_time}]"
    )

    add_sign_records = models.JSONField(
        default=list, blank=True, verbose_name="Add-sign records",
        help_text="Format: [{operator_id, operator_username, added_user_ids, reason, operate_time}]"
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Node status"
    )

    finish_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Finish time"
    )

    def __str__(self):
        return f"{self.instance.script.name} - {self.node_name} - {self.get_status_display()}"

    class Meta:
        db_table = table_prefix + "script_approval_node_exec"
        verbose_name = "Approval node execution record"
        ordering = ['step_order', 'id']
        indexes = [
            models.Index(fields=['instance', 'step_order']),
            models.Index(fields=['status']),
        ]


class ScriptCheckRule(CoreModel):
    """Script check custom rule"""

    SEVERITY_CHOICES = [
        ('error', 'Error'),
        ('warning', 'Warning'),
        ('info', 'Info'),
    ]

    MATCH_TYPE_CHOICES = [
        ('regex', 'Regex match'),
        ('keyword', 'Keyword match'),
    ]

    SCOPE_CHOICES = [
        ('all', 'All script types'),
        ('shell', 'Shell script'),
        ('python', 'Python script'),
        ('sql', 'SQL script'),
        ('powershell', 'PowerShell script'),
        ('javascript', 'JavaScript script'),
    ]

    name = models.CharField(max_length=100, verbose_name="Rule name")
    rule_key = models.CharField(max_length=50, unique=True, verbose_name="Rule identifier")
    description = models.CharField(max_length=255, blank=True, default='', verbose_name="Problem description")
    pattern = models.CharField(max_length=500, verbose_name="Match rule")
    match_type = models.CharField(max_length=20, choices=MATCH_TYPE_CHOICES, default='keyword', verbose_name="Match mode")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='warning', verbose_name="Severity level")
    scope = models.CharField(max_length=30, choices=SCOPE_CHOICES, default='all', verbose_name="Applicable script type")
    fix_suggestion = models.CharField(max_length=500, blank=True, default='', verbose_name="Fix suggestion")
    is_active = models.BooleanField(default=True, verbose_name="Is enabled")
    sort_order = models.IntegerField(default=100, verbose_name="Sort order")
    is_builtin = models.BooleanField(default=False, verbose_name="Is built-in")
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name="Remark")

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.name}"

    class Meta:
        db_table = table_prefix + "script_check_rule"
        verbose_name = "Script check rule"
        verbose_name_plural = verbose_name
        ordering = ['sort_order', '-id']
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['severity']),
            models.Index(fields=['scope']),
        ]


from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=OpsExecution)
def _sync_approval_on_execution_finish(sender, instance, **kwargs):
    if instance.execution_type != 'script':
        return
    if instance.status not in (2, 3):
        return
    try:
        from django.utils import timezone as _tz
        approvals = OpsExecutionApproval.objects.filter(
            ops_execution=instance,
            status='executing'
        )
        if not approvals.exists():
            return
        new_status = 'done' if instance.status == 2 else 'failed'
        now = _tz.now()
        approvals.update(status=new_status, finish_time=now, update_datetime=now)
    except Exception:
        pass


# ========================== Workflow orchestration approval records ==========================

class WorkflowApprove(CoreModel):
    """Workflow orchestration approval record"""

    RISK_LEVEL_CHOICES = [
        ('low', 'Low risk'),
        ('medium', 'Medium risk'),
        ('high', 'High risk'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE,
        related_name='approvals', verbose_name="Workflow"
    )
    workflow_version = models.CharField(max_length=50, blank=True, null=True, verbose_name="Flow version")
    risk_level = models.CharField(
        max_length=20, choices=RISK_LEVEL_CHOICES,
        default='low', verbose_name="Risk level"
    )
    risk_points = models.JSONField(
        default=list, blank=True, verbose_name="Risk point list"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Approval status"
    )
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='workflow_approvals_submitted',
        verbose_name="Submitter"
    )
    submitter_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Submitter name")
    submit_desc = models.TextField(blank=True, null=True, verbose_name="Submission description")
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_approvals_approved',
        verbose_name="Approver"
    )
    approver_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Approver name")
    approve_time = models.DateTimeField(null=True, blank=True, verbose_name="Approval time")
    approve_reason = models.TextField(blank=True, null=True, verbose_name="Approval reason")

    def __str__(self):
        return f"{self.workflow.name} - {self.status}"

    class Meta:
        verbose_name = "Workflow orchestration approval record"
        db_table = table_prefix + "workflow_approve"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['workflow', 'status']),
            models.Index(fields=['submitter']),
            models.Index(fields=['approver']),
        ]


class WorkflowApprovalRule(CoreModel):
    """Workflow approval rule

    Match from top to bottom by priority, the first matching rule takes effect.
    Supports multi-condition groups, AND within group, OR between groups.
    """

    name = models.CharField(max_length=100, verbose_name="Rule name")
    description = models.TextField(blank=True, verbose_name="Rule description")

    condition_groups = models.JSONField(
        default=list, blank=True, verbose_name="Condition group list",
        help_text="""
        Conditions within a group are AND relationships, between groups are OR relationships (any condition group match means rule match).
        Supported condition fields per group (empty means no restriction):
        - category_ids: [1, 2, 3]         # Category ID list (including subcategories)
        - workflow_modes: ["dag", "linear"]  # Flow mode list
        - risk_levels: ["high", "medium"] # Risk level list
        - min_risk_points: 3              # Minimum risk point count (greater than or equal)
        - auth_types: ["public", "private"]    # Authorization type list
        - submitter_roles: ["dev"]        # Submitter role code list
        Example: [{"category_ids": [5], "risk_levels": ["high"]}, {"workflow_modes": ["dag"]}]
        """
    )

    priority = models.IntegerField(
        default=100, verbose_name="Priority",
        help_text="Lower number means higher priority, match from top to bottom by priority"
    )

    is_active = models.BooleanField(default=True, verbose_name="Is enabled")

    def __str__(self):
        return self.name

    class Meta:
        db_table = table_prefix + "workflow_approval_rule"
        verbose_name = "Workflow approval rule"
        ordering = ['priority', 'id']
        indexes = [
            models.Index(fields=['is_active', 'priority']),
        ]


class WorkflowApprovalNode(CoreModel):
    """Workflow approval node definition (review steps under a rule)"""

    APPROVER_TYPE_CHOICES = [
        ('category_reviewer', 'Category reviewer'),
        ('specific_users', 'Specific users'),
        ('role', 'Specific role'),
        ('submitter_manager', 'Submitter manager'),
    ]

    APPROVAL_MODE_CHOICES = [
        ('any', 'OR-sign'),
        ('all', 'AND-sign'),
        ('first', 'First-sign'),
    ]

    rule = models.ForeignKey(
        WorkflowApprovalRule, on_delete=models.CASCADE,
        related_name='nodes', verbose_name="Associated rule"
    )

    node_name = models.CharField(
        max_length=100, verbose_name="Node dot name",
        help_text="e.g. Primary Review, Security Review, DBA Review"
    )

    approver_type = models.CharField(
        max_length=30, choices=APPROVER_TYPE_CHOICES,
        verbose_name="Reviewer type"
    )

    approver_config = models.JSONField(
        default=dict, blank=True, verbose_name="Approver config",
        help_text="""
        category_reviewer: {}                         # 无需额外Config
        specific_users: {"user_ids": [1, 2, 3]}       # 指定UserIDlist
        role: {"role_codes": ["dba", "security"]}     # 指定Roleencodelist
        submitter_manager: {"levels": 1}              # 向上几级主管(1=直属)
        """
    )

    approval_mode = models.CharField(
        max_length=10, choices=APPROVAL_MODE_CHOICES,
        default='any', verbose_name="Review mode"
    )

    step_order = models.IntegerField(
        default=1, verbose_name="Execution order",
        help_text="Lower number executes first"
    )

    def __str__(self):
        return f"{self.rule.name} - {self.node_name}"

    class Meta:
        db_table = table_prefix + "workflow_approval_node"
        verbose_name = "Workflow approval node"
        ordering = ['step_order', 'id']
        indexes = [
            models.Index(fields=['rule', 'step_order']),
        ]


class WorkflowApprovalInstance(CoreModel):
    """Workflow审核流程Instance

    某entriesWorkflowCommit审核后Generate的流程Instance, 
    从Matched ruleCopy节.结构, 作为该次审核的Snapshot.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE,
        related_name='approval_instances', verbose_name="Workflow"
    )
    workflow_version = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Workflow version"
    )

    rule = models.ForeignKey(
        WorkflowApprovalRule, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="Matched rule"
    )
    rule_name = models.CharField(
        max_length=100, blank=True, verbose_name="Rule name snapshot"
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Workflow status"
    )

    current_node_index = models.IntegerField(
        default=0, verbose_name="Current approval node index"
    )

    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='workflow_submitted_approvals',
        verbose_name="Submitter"
    )
    submitter_name = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Submitter name"
    )
    submit_desc = models.TextField(blank=True, null=True, verbose_name="Submit description")

    risk_level = models.CharField(
        max_length=20, blank=True, null=True, verbose_name="Risk level"
    )
    risk_points = models.JSONField(
        default=list, blank=True, verbose_name="Risk list snapshot"
    )

    finish_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Complete time"
    )

    def __str__(self):
        return f"{self.workflow.name} - {self.get_status_display()}"

    class Meta:
        db_table = table_prefix + "workflow_approval_instance"
        verbose_name = "Workflow approval flow instance"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['workflow', 'status']),
            models.Index(fields=['submitter']),
            models.Index(fields=['status']),
        ]


class WorkflowApprovalNodeExecution(CoreModel):
    """Workflow approval nodeExecutionrecord"""

    STATUS_CHOICES = [
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('skipped', 'Skipped'),
    ]

    instance = models.ForeignKey(
        WorkflowApprovalInstance, on_delete=models.CASCADE,
        related_name='node_executions', verbose_name="Parent workflow instance"
    )

    node_name = models.CharField(max_length=100, verbose_name="Node dot name")
    approver_type = models.CharField(max_length=30, verbose_name="Reviewer type")
    approver_config = models.JSONField(
        default=dict, blank=True, verbose_name="Approver config snapshot"
    )
    approval_mode = models.CharField(max_length=10, verbose_name="Review mode")
    step_order = models.IntegerField(verbose_name="Node order")

    candidate_approvers = models.JSONField(
        default=list, blank=True, verbose_name="Candidate reviewer list",
        help_text="Format: [{user_id, username, name}]"
    )

    approval_records = models.JSONField(
        default=list, blank=True, verbose_name="Approval operation record",
        help_text="Format: [{user_id, username, action: approve/reject/delegate/add_sign, reason, operate_time, target_user}]"
    )

    delegate_records = models.JSONField(
        default=list, blank=True, verbose_name="Transfer record",
        help_text="Format: [{from_user_id, from_username, to_user_id, to_username, reason, operate_time}]"
    )

    add_sign_records = models.JSONField(
        default=list, blank=True, verbose_name="Add-signrecord",
        help_text="Format: [{operator_id, operator_username, added_user_ids, reason, operate_time}]"
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default='pending', verbose_name="Node status"
    )

    finish_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Complete time"
    )

    def __str__(self):
        return f"{self.instance.workflow.name} - {self.node_name} - {self.get_status_display()}"

    class Meta:
        db_table = table_prefix + "workflow_approval_node_exec"
        verbose_name = "Workflow approval node execution record"
        ordering = ['step_order', 'id']
        indexes = [
            models.Index(fields=['instance', 'step_order']),
            models.Index(fields=['status']),
        ]


# ============================================================
# ===== 分享Permission系统:PermissionDefinitionDictionary =====
# ============================================================
class SharePermissionDef(CoreModel):
    """分享PermissionDefinition表(Dictionary):所有Optional的细GranularityPermission项"""
    RESOURCE_TYPE_CHOICES = [
        ('script', 'Script'),
        ('workflow', 'Workflow'),
    ]
    CATEGORY_CHOICES = [
        ('view', 'View'),
        ('edit', 'Edit'),
        ('execute', 'Execute'),
        ('manage', 'Manage'),
    ]

    resource_type = models.CharField(max_length=20, choices=RESOURCE_TYPE_CHOICES, verbose_name="Resource type")
    perm_code = models.CharField(max_length=100, verbose_name="Permissionencode")
    perm_name = models.CharField(max_length=100, verbose_name="Permission name")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name="Category")
    description = models.CharField(max_length=500, blank=True, verbose_name="Description")
    sort = models.IntegerField(default=0, verbose_name="Order")
    is_active = models.BooleanField(default=True, verbose_name="Is enabled")

    def __str__(self):
        return f"[{self.get_resource_type_display()}] {self.perm_name} ({self.perm_code})"

    class Meta:
        db_table = table_prefix + "share_permission_def"
        unique_together = [('resource_type', 'perm_code')]
        verbose_name = "Share permission definition"
        ordering = ("resource_type", "category", "sort")


# ============================================================
# ===== 分享Permission系统:直接分享(User/Role/Dept)=====
# ============================================================
class ScriptSharePermission(CoreModel):
    """Script直接分享Permission:User/Role/Dept + Permission项list"""
    SUBJECT_TYPE_CHOICES = [
        ('user', 'User'),
        ('role', 'Role'),
        ('dept', 'Department'),
    ]

    script = models.ForeignKey(Script, on_delete=models.CASCADE, related_name='share_permissions', verbose_name="Script")
    subject_type = models.CharField(max_length=20, choices=SUBJECT_TYPE_CHOICES, default='user', verbose_name="Principal type")
    subject_id = models.CharField(max_length=100, verbose_name="Subject ID (user/role/dept ID)")
    subject_name_cache = models.CharField(max_length=200, blank=True, verbose_name="Subject name cache (avoids repeated joins)")

    permissions = models.JSONField(default=list, verbose_name="Permission code list (JSON array)")

    expire_time = models.DateTimeField(null=True, blank=True, verbose_name="Expiry time (None=permanent)")
    grant_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='script_share_granted',
        verbose_name="Authorizer"
    )
    remark = models.CharField(max_length=500, blank=True, verbose_name="Remark")

    @property
    def creator_name(self):
        return self.grant_user.username if self.grant_user else ''

    def __str__(self):
        return f"Script share:{self.script_id}->{self.get_subject_type_display()}:{self.subject_id}"

    class Meta:
        db_table = table_prefix + "script_share_permission"
        unique_together = [('script', 'subject_type', 'subject_id')]
        verbose_name = "Script share permission"
        indexes = [
            models.Index(fields=['subject_type', 'subject_id']),
            models.Index(fields=['expire_time']),
        ]


class WorkflowSharePermission(CoreModel):
    """Workflow直接分享Permission:User/Role/Dept + Permission项list"""
    SUBJECT_TYPE_CHOICES = [
        ('user', 'User'),
        ('role', 'Role'),
        ('dept', 'Department'),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='share_permissions', verbose_name="Workflow")
    subject_type = models.CharField(max_length=20, choices=SUBJECT_TYPE_CHOICES, default='user', verbose_name="Principal type")
    subject_id = models.CharField(max_length=100, verbose_name="Subject ID")
    subject_name_cache = models.CharField(max_length=200, blank=True, verbose_name="Subject name cache")

    permissions = models.JSONField(default=list, verbose_name="Permission code list (JSON array)")

    expire_time = models.DateTimeField(null=True, blank=True, verbose_name="Expiry time (None=permanent)")
    grant_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workflow_share_granted',
        verbose_name="Authorizer"
    )
    remark = models.CharField(max_length=500, blank=True, verbose_name="Remark")

    @property
    def creator_name(self):
        return self.grant_user.username if self.grant_user else ''

    def __str__(self):
        return f"Workflow share:{self.workflow_id}->{self.get_subject_type_display()}:{self.subject_id}"

    class Meta:
        db_table = table_prefix + "workflow_share_permission"
        unique_together = [('workflow', 'subject_type', 'subject_id')]
        verbose_name = "Workflow share permission"
        indexes = [
            models.Index(fields=['subject_type', 'subject_id']),
            models.Index(fields=['expire_time']),
        ]


# ============================================================
# ===== 分享Permission系统:link分享 =====
# ============================================================
class ShareLink(CoreModel):
    """分享link:via URL + Token temporaryauthorization访问(supportScript & Workflow)"""
    RESOURCE_TYPE_CHOICES = [
        ('script', 'Script'),
        ('workflow', 'Workflow'),
    ]
    ACCESS_SCOPE_CHOICES = [
        ('authenticated', 'Authenticated users only'),
        ('anyone', 'Anyone with link'),
    ]

    resource_type = models.CharField(max_length=20, choices=RESOURCE_TYPE_CHOICES, verbose_name="Resource type")
    resource_id = models.CharField(max_length=100, verbose_name="Resource ID (ScriptID/WorkflowID)")

    share_token = models.CharField(max_length=64, unique=True, verbose_name="Share token (URL parameter)")
    permissions = models.JSONField(default=list, verbose_name="Permission code list (JSON array)")

    # ---- 有效期 & 访问限制 ----
    expire_time = models.DateTimeField(null=True, blank=True, verbose_name="Expiry time (None=permanent)")
    max_access_count = models.IntegerField(default=0, verbose_name="Max access count (0=unlimited)")
    current_access_count = models.IntegerField(default=0, verbose_name="Current access count")
    access_scope = models.CharField(
        max_length=20, choices=ACCESS_SCOPE_CHOICES,
        default='authenticated', verbose_name="Access scope"
    )

    # ---- Optional绑定:if指定了绑定主体, 则只有该主体 + Token才能访问 ----
    bind_subject_type = models.CharField(max_length=20, null=True, blank=True, verbose_name="Bound principal type (user/role/dept)")
    bind_subject_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="Bound subject ID")

    is_active = models.BooleanField(default=True, verbose_name="Enabled (can be manually revoked)")
    create_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='share_links_created',
        verbose_name="Creator"
    )
    remark = models.CharField(max_length=500, blank=True, verbose_name="Remark")

    def __str__(self):
        return f"Share link[{self.share_token[:8]}...]:{self.resource_type}#{self.resource_id}"

    class Meta:
        db_table = table_prefix + "share_link"
        verbose_name = "Share link"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['share_token']),
            models.Index(fields=['resource_type', 'resource_id']),
            models.Index(fields=['expire_time']),
            models.Index(fields=['is_active']),
        ]


class ShareLinkAccessLog(CoreModel):
    """分享link访问Log(用于audit)"""
    share_link = models.ForeignKey(ShareLink, on_delete=models.CASCADE, related_name='access_logs', verbose_name="Parent share link")
    access_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='share_link_accesses',
        verbose_name="Accessing user (empty if not logged in)"
    )
    client_ip = models.CharField(max_length=100, blank=True, verbose_name="ClientIP")
    user_agent = models.CharField(max_length=500, blank=True, verbose_name="Browser user-agent")
    access_success = models.BooleanField(default=True, verbose_name="Success status (fail if expired/over-limit)")
    fail_reason = models.CharField(max_length=200, blank=True, verbose_name="Failure reason")

    def __str__(self):
        status = "Success" if self.access_success else f"Failed:{self.fail_reason}"
        return f"Link access:{self.share_link_id} - {status}"

    class Meta:
        db_table = table_prefix + "share_link_access_log"
        verbose_name = "Share link access log"
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['share_link', 'create_datetime']),
            models.Index(fields=['access_user']),
        ]


# ===== v2 DAG Engine:Register WorkflowDAGVersion / WorkflowNodeExecution 到 taurus app(Meta.app_label='taurus')
# must放在File末尾, AvoidCircular import(taurus.workflow.models internal用字符串 FK Reference taurus.Workflow)
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution  # noqa: F401  isort:skip


class ContactLead(CoreModel):
    """Contact lead submitted from public portal (taurus-portal) contact form."""
    SCALE_CHOICES = [
        ('startup', '初创 (≤20人)'),
        ('small', '小型 (20-100人)'),
        ('mid', '中型 (100-500人)'),
        ('large', '大型 (500-2000人)'),
        ('enterprise', '超大型 (2000人+)'),
    ]
    name = models.CharField(max_length=64, verbose_name="联系人姓名")
    company = models.CharField(max_length=128, verbose_name="公司名称", blank=True, default='')
    phone = models.CharField(max_length=32, verbose_name="联系电话", blank=True, default='')
    email = models.CharField(max_length=128, verbose_name="邮箱")
    scale = models.CharField(
        max_length=16,
        verbose_name="企业规模",
        choices=SCALE_CHOICES,
        default='small',
    )
    message = models.TextField(verbose_name="需求描述", blank=True, default='')
    source = models.CharField(
        max_length=32,
        verbose_name="线索来源",
        default='portal',
        help_text="portal(官网) / promotion / referral / other",
    )
    ip = models.GenericIPAddressField(verbose_name="提交IP", null=True, blank=True, unpack_ipv4=True)
    user_agent = models.CharField(max_length=512, verbose_name="UA", blank=True, default='')

    class Meta:
        db_table = table_prefix + "contact_lead"
        verbose_name = "Contact lead"
        verbose_name_plural = verbose_name
        ordering = ['-create_datetime']
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['source', 'create_datetime']),
        ]

    def __str__(self):
        return f"{self.name} <{self.email or '-'}> @ {self.company or '-'}"