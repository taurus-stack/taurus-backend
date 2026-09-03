"""
Taurus Executor SDK Client
异步 gRPC Client, 用于与 taurus-executor 通信
"""

import asyncio
import grpc
import os
import threading
from typing import AsyncGenerator, Optional, Tuple
import logging
import pathlib

from .generated.executor.v1 import command_service_pb2
from .generated.executor.v1 import command_service_pb2_grpc

logger = logging.getLogger(__name__)


# ============================================================
# 跨 TaurusClient 探活去重(单例lock + 结果cache
# ------------------------------------------------------------
# 并发场景下(Polling + WebSocket simultaneouslyTrigger多 任务Execution), 同一台 executor host
# 会被多  TaurusClient Instancesimultaneously发出 GetStatus 探活.
# 在 executor 端任何一 Interceptor阻塞 event loop(例如同步 subprocess 调用)都会放大为 N 次, 
# 进一步饿死所有并发探活, 最终整体timeout.therefore host Scope:
#   1. 探活过程中用 asyncio.Lock 串行化
#   2. Success结果 5 秒内直接复用
# ============================================================
_PROBE_LOCKS: dict = {}
_PROBE_LOCKS_GUARD = threading.Lock()
_PROBE_CACHE: dict = {}
_PROBE_CACHE_TTL = 5.0


def _get_host_probe_lock(address: str) -> asyncio.Lock:
    with _PROBE_LOCKS_GUARD:
        lock = _PROBE_LOCKS.get(address)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if lock is not None and running_loop is not None and getattr(lock, '_loop', None) is not running_loop:
            lock = None
        if lock is None:
            lock = asyncio.Lock()
            _PROBE_LOCKS[address] = lock
        return lock


import time as _time_mod


def find_default_certificates() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    自动detectdefaultCertificatepath
    
    detect顺序:
    1. Environment variables TAURUS_CERT_DIR specifieddirectory
    2. currentdirectory下的 tls/ directory(web_ui.py 风格:webui-client.crt/key)
    3. currentdirectory下的 certs/sdk/ directory(SDK 风格:client.crt/key)
    4. 向上find项目rootdirectory下的 tls/ 或 certs/sdk/ directory
    
    Returns:
        Tuple[cert_file, key_file, ca_file]: CertificateFilepath, if未Found则对应位置为 None
    """
    # ClientCertificatemay的File名
    client_cert_names = ["webui-client.crt", "client.crt", "sdk-client.crt"]
    client_key_names = ["webui-client.key", "client.key", "sdk-client.key"]
    ca_cert_names = ["ca.crt", "ca-cert.crt"]
    
    def find_certs_in_dir(cert_dir: pathlib.Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """在指定directory中findCertificateFile"""
        if not cert_dir.exists():
            return None, None, None
        
        # findClientCertificate
        cert_file = None
        for name in client_cert_names:
            path = cert_dir / name
            if path.exists():
                cert_file = str(path)
                break
        
        # findClientPrivate key
        key_file = None
        for name in client_key_names:
            path = cert_dir / name
            if path.exists():
                key_file = str(path)
                break
        
        # find CA Certificate
        ca_file = None
        for name in ca_cert_names:
            path = cert_dir / name
            if path.exists():
                ca_file = str(path)
                break
        
        return cert_file, key_file, ca_file
    
    def has_all_certs(result: Tuple[Optional[str], Optional[str], Optional[str]]) -> bool:
        """checkYesNoFound了所有必需的Certificate"""
        return all(result)
    
    # 1. checkEnvironment variables
    env_cert_dir = os.environ.get('TAURUS_CERT_DIR')
    if env_cert_dir:
        cert_dir = pathlib.Path(env_cert_dir)
        result = find_certs_in_dir(cert_dir)
        if has_all_certs(result):
            return result
    
    # 2. checkcurrentdirectory下的 tls/(web_ui.py 风格)
    current_dir = pathlib.Path.cwd()
    tls_dir = current_dir / "tls"
    result = find_certs_in_dir(tls_dir)
    if has_all_certs(result):
        return result
    
    # 3. checkcurrentdirectory下的 certs/sdk/(SDK 风格)
    sdk_dir = current_dir / "certs" / "sdk"
    result = find_certs_in_dir(sdk_dir)
    if has_all_certs(result):
        return result
    
    # 4. 向上find项目rootdirectory
    search_dir = current_dir
    while search_dir != search_dir.parent:
        # find tls/ directory
        tls_dir = search_dir / "tls"
        result = find_certs_in_dir(tls_dir)
        if has_all_certs(result):
            return result
        
        # find certs/sdk/ directory
        sdk_dir = search_dir / "certs" / "sdk"
        result = find_certs_in_dir(sdk_dir)
        if has_all_certs(result):
            return result
        
        search_dir = search_dir.parent
    
    # 未FoundCertificate
    return None, None, None


class Session:
    """交互式SessionClient"""
    
    def __init__(self, session_id: str, stub, metadata):
        self.session_id = session_id
        self.stub = stub
        self.metadata = metadata
        self.is_active = True
    
    async def execute(
        self,
        command: str,
        timeout: int = 30
    ) -> AsyncGenerator[dict, None]:
        """Execute command in session"""
        if not self.is_active:
            raise RuntimeError("Session is closed")
        
        request = command_service_pb2.SessionCommandRequest(
            session_id=self.session_id,
            command=command,
            timeout_seconds=timeout
        )
        
        response_stream = self.stub.ExecuteInSession(request, metadata=self.metadata)
        
        try:
            async for response in response_stream:
                result = {}
                
                if response.stdout_chunk:
                    result["stdout"] = response.stdout_chunk.decode('utf-8', errors='replace')
                
                if response.stderr_chunk:
                    result["stderr"] = response.stderr_chunk.decode('utf-8', errors='replace')
                
                if response.finished:
                    result["finished"] = True
                    result["exit_code"] = response.exit_code
                
                if response.error_message:
                    result["error"] = response.error_message
                
                yield result
                
        except grpc.aio.AioRpcError as e:
            logger.error(f"gRPC error in session: {e.code()}: {e.details()}")
            yield {"error": f"gRPC error: {e.details()}"}
        except Exception as e:
            logger.error(f"Error in session: {e}", exc_info=True)
            yield {"error": str(e)}
    
    async def close(self) -> bool:
        """Close session"""
        if not self.is_active:
            return False
        
        request = command_service_pb2.SessionCloseRequest(
            session_id=self.session_id
        )
        
        try:
            response = await self.stub.CloseSession(request, metadata=self.metadata)
            self.is_active = False
            logger.info(f"Session {self.session_id} closed: {response.message}")
            return response.success
        except Exception as e:
            logger.error(f"Error closing session: {e}")
            return False


class TaurusClient:
    """
    Taurus Executor gRPC Client
    
    用于与 taurus-executor 进行异步通信, support:
    - CommandExecution(流式Output)
    - ScriptExecution
    - FileUpload/Download
    - directorylist
    - 交互式Session
    - Process控制(pause/Restore/终止)
    """
    
    # gRPC CertificateTargetName(用于 SSL CertificateValidation)
    GRPC_TARGET_NAME = "taurus-grpc-server"

    def __init__(
        self,
        address: str,
        cert_file: Optional[str] = None,
        key_file: Optional[str] = None,
        ca_file: Optional[str] = None,
        role: str = "operator",
        target_name: Optional[str] = None,
        secure: bool = True,
        connect_timeout: int = 5,
    ):
        """
        initialize Taurus Client

        Args:
            address: Serviceserver地址, Format "host:port"
            cert_file: ClientCertificateFilepath(用于 mTLS).if未provide且 secure=True, 将自动detect
            key_file: ClientPrivate keyFilepath(用于 mTLS).if未provide且 secure=True, 将自动detect
            ca_file: CA CertificateFilepath(用于ServiceserverCertificateValidation).if未provide且 secure=True, 将自动detect
            role: ClientRole, 默认 "operator"
            target_name: gRPC SSL TargetName覆盖(默认 "taurus-grpc-server")
            secure: YesNouse安全join(mTLS), 默认 True.设为 False 则use insecure channel
            connect_timeout: join级探活Timeout(秒), 默认 5 秒.用于 GetStatus 探活 RPC 的硬timeout兜底.

        Note:
            if secure=True 但未provideCertificateParameters, SDK 会Attempt自动detectCertificatepath:
            1. Environment variables TAURUS_CERT_DIR specifieddirectory
            2. currentdirectory下的 certs/sdk/ directory
            3. 向上find项目rootdirectory下的 certs/sdk/ directory
        """
        self.address = address
        self.role = role
        self.target_name = target_name or self.GRPC_TARGET_NAME
        self.secure = secure
        self.connect_timeout = max(1, int(connect_timeout))
        self.channel = None
        self.stub = None
        self.file_transfer_stub = None
        self.metadata = None
        
        # if secure=True 但未provideCertificate, Attempt自动detect
        if self.secure:
            if cert_file and key_file and ca_file:
                self.cert_file = cert_file
                self.key_file = key_file
                self.ca_file = ca_file
            else:
                # Attempt自动detectCertificate
                auto_cert, auto_key, auto_ca = find_default_certificates()
                if auto_cert and auto_key and auto_ca:
                    self.cert_file = auto_cert
                    self.key_file = auto_key
                    self.ca_file = auto_ca
                    logger.info(f"Auto-detected certificates: {auto_cert}")
                else:
                    raise ValueError(
                        "cert_file, key_file, and ca_file are required when secure=True. "
                        "Either provide them explicitly or ensure certificates exist in "
                        "certs/sdk/ directory or set TAURUS_CERT_DIR environment variable."
                    )
        else:
            # insecure 模式不requireCertificate
            self.cert_file = None
            self.key_file = None
            self.ca_file = None

    async def __aenter__(self):
        """异步Context管理server入口"""
        # create元数据以传递ClientRole
        metadata = [('x-client-role', self.role)]

        # gRPC 通道option
        options = [
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.keepalive_permit_without_calls', 0),
            ('grpc.http2.max_pings_without_data', 1),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.max_send_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.ssl_target_name_override', self.target_name),
            # join级timeout:3秒内连不上即快速Failed, Avoid阻塞Execution链路
            # Note:grpc.aio 在join/握手phasevia通道option限制timeout
            # deadline cannot直接影响初始 TCP join, 这里加 DNS Parsetimeout等, 
            # simultaneously在 channel_ready_future 里再做额外 3s 硬timeout兜底
            ('grpc.client_idle_timeout_ms', 15000),
        ]
        
        if self.secure:
            # use SSL/TLS 安全join
            with open(self.cert_file, 'rb') as f:
                cert_data = f.read()
            with open(self.key_file, 'rb') as f:
                key_data = f.read()
            with open(self.ca_file, 'rb') as f:
                ca_data = f.read()
            
            credentials = grpc.ssl_channel_credentials(
                root_certificates=ca_data,
                private_key=key_data,
                certificate_chain=cert_data
            )
            
            self.channel = grpc.aio.secure_channel(self.address, credentials, options=options)
            logger.info(f"Using secure connection to {self.address} with role {self.role}")
        else:
            self.channel = grpc.aio.insecure_channel(self.address, options=options)
            logger.info(f"Using insecure connection to {self.address} with role {self.role}")
            
        self.stub = command_service_pb2_grpc.ClientServiceStub(self.channel)
        self.file_transfer_stub = command_service_pb2_grpc.FileTransferStub(self.channel)
        # 存储元数据以供后续调用use
        self.metadata = metadata

        # === join级硬timeout:connect_timeout 秒内探活Failed立刻raised by ===
        # 针对「Host关机 / Process未start / 防火墙 DROP」三种最常见离line场景的分层快速Failed策略:
        #   1. 先查 cache:5 秒内同 host Success探活直接复用(0 网络开销)
        #   2. 再做 TCP Port预detect:asyncio.open_connection 发 SYN, 
        #      Host不在line时通常 1~2 秒内Failed(ICMP host-unreachable / RST), 
        #      不用等 gRPC channel internal TLS 握手的长timeout.
        #   3. 最后走 GetStatus 正式探活, Validation mTLS + Interceptor链完整可用
        import asyncio as _aio
        _t = self.connect_timeout
        _now = _time_mod.monotonic()
        _cache_entry = _PROBE_CACHE.get(self.address)
        if _cache_entry is not None and (_now - _cache_entry[0]) < _PROBE_CACHE_TTL:
            logger.debug(f"[Probe] {self.address} 复用缓存探活结果（TTL 剩余 {_PROBE_CACHE_TTL - (_now - _cache_entry[0]):.1f}s）")
        else:
            # Per host 串行化(同一 host 同一时刻只有 1  探活actually发出去)
            _lock = _get_host_probe_lock(self.address)
            async with _lock:
                _now = _time_mod.monotonic()
                _cache_entry = _PROBE_CACHE.get(self.address)
                if _cache_entry is None or (_now - _cache_entry[0]) >= _PROBE_CACHE_TTL:

                    # ----- 分层 1:TCP Port可达性快速预检 -----
                    try:
                        _host_part, _port_part = self.address.rsplit(":", 1)
                        _tcp_timeout = min(max(1, _t // 2), 3)  # 占总timeout 1/2, 最多 3 秒
                        _tcp_t0 = _time_mod.monotonic()
                        _reader, _writer = await _aio.wait_for(
                            _aio.open_connection(_host_part, int(_port_part)),
                            timeout=_tcp_timeout,
                        )
                        _writer.close()
                        try:
                            await _writer.wait_closed()
                        except Exception:
                            pass
                        logger.debug(
                            f"[Probe] TCP {self.address} 可达，耗时 {_time_mod.monotonic()-_tcp_t0:.2f}s"
                        )
                    except (_aio.TimeoutError, OSError, ConnectionError, ValueError) as tcp_e:
                        try:
                            await self.channel.close()
                        except Exception:
                            pass
                        if isinstance(tcp_e, _aio.TimeoutError):
                            _msg = (
                                f"Failed to connect to executor {self.address}: TCP port {_port_part} no response in {_tcp_timeout}s. Check: 1)Host is on 2)Executor running 3)Port 50051 not blocked by firewall"
                            )
                        else:
                            _msg = (
                                f"Failed to connect to executor {self.address} (TCP port {_port_part} unreachable: {tcp_e.__class__.__name__}). Check if executor is running and port 50051 is listening"
                            )
                        raise ConnectionError(_msg) from tcp_e

                    # ----- 分层 2:gRPC GetStatus 正式探活(Validation mTLS + Interceptor + handler) -----
                    _grpc_timeout_budget = max(1, _t - int(_time_mod.monotonic() - _now) - 1)
                    try:
                        await _aio.wait_for(
                            self.stub.GetStatus(
                                command_service_pb2.StatusRequest(),
                                metadata=self.metadata,
                                timeout=_grpc_timeout_budget,
                            ),
                            timeout=_grpc_timeout_budget + 0.5,
                        )
                    except _aio.TimeoutError as te:
                        try:
                            await self.channel.close()
                        except Exception:
                            pass
                        raise ConnectionError(
                            f"Connection to executor {self.address} timeout (probe not completed in {_t}s). TCP ok but gRPC no response, check if executor is stuck"
                        ) from te
                    except grpc.aio.AioRpcError as rpc_e:
                        try:
                            await self.channel.close()
                        except Exception:
                            pass
                        raise ConnectionError(
                            f"Failed to connect to executor {self.address}: code={rpc_e.code()} detail={rpc_e.details()}"
                        ) from rpc_e
                    # Success:write cache, 后续并发Instance 5 秒内直接Skip
                    _PROBE_CACHE[self.address] = (_time_mod.monotonic(), True)
                else:
                    logger.debug(f"[Probe] {self.address} 抢锁后复用缓存结果（TTL 剩余 {_PROBE_CACHE_TTL - (_now - _cache_entry[0]):.1f}s）")

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步Context管理server出口"""
        if self.channel:
            await self.channel.close()

    async def execute_command(
        self, 
        command: str, 
        args: Optional[list[str]] = None, 
        timeout: int = 30, 
        environment: Optional[dict] = None, 
        merge_streams: bool = False, 
        shell: bool = False, 
        load_profile: str = "false",
        working_directory: Optional[str] = None
    ) -> AsyncGenerator[dict, None]:
        """
        ExecutionCommand并流式returnOutput

        Args:
            command: 要Execution的Command
            args: CommandParameters(when shell=True 时忽略)
            timeout: Execution timeout(秒)
            environment: Environment variables
            merge_streams: if为 True, stderr Merge到 stdout 以保持Output顺序
            shell: if为 True, via /bin/bash -c run, 以便正确Parse shell operation符
            load_profile: 控制 shell 模式下的 bash environmentload:
                          "false"(默认):--noprofile --norc, 干净environment
                          "true":不加flag, load ~/.bashrc
                          "login":--login, 完整 login shell(/etc/profile + ~/.bash_profile 等)
            working_directory: runCommand的directory, 默认为ServiceserverProcess的 CWD
            
        Yields:
            dict: Contains stdout/stderr/finished/exit_code/error 的结果Dictionary
        """
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.CommandRequest(
            command=command, 
            args=args or [], 
            timeout_seconds=timeout, 
            environment=environment or {}, 
            merge_streams=merge_streams, 
            working_directory=working_directory or ""
        )
        
        # viaEnvironment variables传递 shell flag
        if shell:
            request.environment["__TAURUS_USE_SHELL__"] = "true"
        if load_profile and load_profile != "false":
            request.environment["__TAURUS_LOAD_PROFILE__"] = load_profile

        # 发起调用
        response_stream = self.stub.ExecuteCommand(request, metadata=self.metadata)
        
        try:
            async for response in response_stream:
                result = {}

                if response.stdout_chunk:
                    result["stdout"] = response.stdout_chunk.decode('utf-8', errors='replace')

                if response.stderr_chunk:
                    result["stderr"] = response.stderr_chunk.decode('utf-8', errors='replace')

                if response.finished:
                    result["finished"] = True
                    result["exit_code"] = response.exit_code

                if response.error_message:
                    result["error"] = response.error_message

                yield result
                
        except asyncio.CancelledError:
            # 显式Cancel RPC 调用
            response_stream.cancel()
            try:
                async for _ in response_stream:
                    pass  # 消费剩余event直到流actuallyClose
            except grpc.aio.AioRpcError:
                pass  # 忽略Cancel过程中may出现的Exception
            raise
        except grpc.aio.AioRpcError as e:
            logger.error(f"gRPC error during command execution: {e.code()}: {e.details()}")
            yield {"error": f"gRPC error: {e.code()}: {e.details()}"}
            yield {"finished": True, "exit_code": 1}
        except Exception as e:
            logger.error(f"Unexpected error during command execution: {e}", exc_info=True)
            yield {"error": f"unexpected error: {e}"}
            yield {"finished": True, "exit_code": 1}

    async def get_status(self) -> dict:
        """FetchClientStatus"""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
    
        request = command_service_pb2.StatusRequest()
        response = await self.stub.GetStatus(request, metadata=self.metadata)
        return {
            "version": response.version,
            "uptime": response.uptime,
            "hostname": response.hostname,
            "cpu_usage": response.cpu_usage,
            "memory_usage": response.memory_usage,
        }

    async def send_signal(self, pid: int, signal_num: int) -> dict:
        """
        向正在run的Processsendsignal

        Args:
            pid: Process ID(mustYesProcess组领导者)
            signal_num: signal编号(19=SIGSTOP, 18=SIGCONT, 9=SIGKILL)
        """
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.SendSignalRequest(pid=pid, signal=signal_num)
        response = await self.stub.SendSignal(request, metadata=self.metadata)
        return {
            "success": response.success,
            "message": response.message,
        }

    async def list_executions(self) -> list[dict]:
        """List all running commands"""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.ListExecutionsRequest()
        response = await self.stub.ListExecutions(request, metadata=self.metadata)
        return [
            {
                "execution_id": e.execution_id,
                "command": e.command,
                "args": list(e.args),
                "pid": e.pid,
                "status": e.status,
                "started_at": e.started_at,
            }
            for e in response.executions
        ]

    async def upload_file(self, local_path: str, remote_path: str, chunk_size: int = 64 * 1024) -> dict:
        """
        UploadFile到Client

        Args:
            local_path: 要Upload的localFilepath
            remote_path: Client上的remoteFilepath
            chunk_size: 每 块的大小(字节), 默认 64KB
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        total_size = os.path.getsize(local_path)
        file_name = os.path.basename(local_path)

        async def generate_chunks():
            with open(local_path, "rb") as f:
                chunk_index = 0
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    is_last = (f.tell() >= total_size)
                    yield command_service_pb2.UploadRequest(
                        file_path=remote_path,
                        chunk=chunk,
                        is_last=is_last,
                        total_size=total_size,
                        chunk_index=chunk_index,
                    )
                    chunk_index += 1

        response = await self.file_transfer_stub.UploadFile(generate_chunks(), metadata=self.metadata)
        return {
            "success": response.success,
            "message": response.message,
            "bytes_received": response.bytes_received,
        }

    async def download_file(self, remote_path: str, local_path: str, chunk_size: int = 64 * 1024) -> dict:
        """
        从ClientDownloadFile

        Args:
            remote_path: Client上的remoteFilepath
            local_path: Save到的localFilepath
            chunk_size: 每 块的大小(字节), 默认 64KB
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.DownloadRequest(
            file_path=remote_path,
            offset=0,
            chunk_size=chunk_size,
        )

        response_stream = self.file_transfer_stub.DownloadFile(request, metadata=self.metadata)

        total_size = 0
        bytes_received = 0

        # 确保parentdirectory存在
        parent_dir = os.path.dirname(local_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with open(local_path, "wb") as f:
            async for response in response_stream:
                if response.chunk:
                    f.write(response.chunk)
                    bytes_received += len(response.chunk)
                    total_size = response.total_size

        return {
            "success": True,
            "message": f"File downloaded successfully: {local_path}",
            "bytes_received": bytes_received,
            "total_size": total_size,
        }

    async def list_directory(self, path: str) -> list[dict]:
        """
        列出Client上的directory内容

        Args:
            path: remotedirectorypath
        """
        if not self.file_transfer_stub:
            raise RuntimeError("Client not connected. Use async context manager.")

        request = command_service_pb2.ListDirectoryRequest(path=path)
        response = await self.file_transfer_stub.ListDirectory(request, metadata=self.metadata)

        if response.error:
            raise Exception(response.error)

        return [
            {
                "name": e.name,
                "size": e.size,
                "is_dir": e.is_dir,
                "modified_at": e.modified_at,
                "permissions": e.permissions,
                "owner": e.owner,
                "group": e.group,
            }
            for e in response.entries
        ]
    
    async def cancel_current_command(self) -> None:
        """
        Cancelcurrent正在Execution的Command
        Note:这Yes一 Placeholdermethod.actuallyrequire跟踪活动的 RPC 调用并单独Cancel它们.
        """
        logger.info("Cancel command functionality would be implemented here.")

    async def create_session(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        working_directory: Optional[str] = None,
        environment: Optional[dict] = None,
        shell: str = "/bin/bash",
        timeout: int = 3600,
    ) -> Session:
        """
        create交互式Session
        
        Args:
            username: User名(Optional)
            password: 密码(Optional)
            working_directory: Working directory(Optional)
            environment: Environment variables(Optional)
            shell: Shell path, 默认 /bin/bash
            timeout: Sessiontimeout(秒), 默认 3600
        """
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
        
        request = command_service_pb2.SessionRequest(
            username=username or "",
            password=password or "",
            working_directory=working_directory or "",
            environment=environment or {},
            timeout_seconds=timeout,
            shell=shell,
        )
        
        response = await self.stub.CreateSession(request, metadata=self.metadata)
        
        if not response.success:
            raise RuntimeError(f"Failed to create session: {response.message}")
        
        logger.info(f"Session created: {response.session_id}")
        return Session(response.session_id, self.stub, self.metadata)

    async def list_sessions(self) -> list:
        """列出所有活动Session"""
        if not self.stub:
            raise RuntimeError("Client not connected. Use async context manager.")
        
        request = command_service_pb2.ListSessionsRequest()
        response = await self.stub.ListSessions(request, metadata=self.metadata)
        
        return [
            {
                "session_id": s.session_id,
                "username": s.username,
                "working_directory": s.working_directory,
                "created_at": s.created_at,
                "last_active": s.last_active,
                "is_alive": s.is_alive,
            }
            for s in response.sessions
        ]