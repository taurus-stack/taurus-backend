"""
gRPC Client utility class for communicating with taurus-executor
"""
import logging
import grpc
from typing import Optional, Generator, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Command execution result"""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error_message: str = ""


class ExecutorClient:
    """
    taurus-executor gRPC client wrapper
    
    Supported functionality:
    - Execute single command (ExecuteCommand)
    - File upload (UploadFile)
    """
    
    DEFAULT_PORT = 50051
    DEFAULT_TIMEOUT = 300  # 5 minutes
    
    def __init__(self, host_ip: str, port: int = None, use_tls: bool = False,
                 cert_path: str = None, key_path: str = None, ca_path: str = None):
        """
        Initialize executor client
        
        Args:
            host_ip: Host IP where executor resides
            port: gRPC port, default 50051
            use_tls: Whether to use TLS
            cert_path: Client certificate path (mTLS)
            key_path: Client private key path (mTLS)
            ca_path: CA certificate path
        """
        self.host_ip = host_ip
        self.port = port or self.DEFAULT_PORT
        self.use_tls = use_tls
        self.cert_path = cert_path
        self.key_path = key_path
        self.ca_path = ca_path
        self._channel = None
    
    def _get_channel(self) -> grpc.Channel:
        """Get gRPC channel"""
        if self._channel is not None:
            return self._channel
        
        target = f"{self.host_ip}:{self.port}"
        
        if self.use_tls:
            # Read certificates
            with open(self.ca_path, 'rb') as f:
                ca_cert = f.read()
            
            if self.cert_path and self.key_path:
                with open(self.cert_path, 'rb') as f:
                    cert = f.read()
                with open(self.key_path, 'rb') as f:
                    key = f.read()
                credentials = grpc.ssl_channel_credentials(
                    root_certificates=ca_cert,
                    private_key=key,
                    certificate_chain=cert
                )
            else:
                credentials = grpc.ssl_channel_credentials(root_certificates=ca_cert)
            
            self._channel = grpc.secure_channel(target, credentials)
        else:
            self._channel = grpc.insecure_channel(target)
        
        return self._channel
    
    def close(self):
        """Close channel"""
        if self._channel:
            self._channel.close()
            self._channel = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def execute_command(self, command: str, args: list = None, 
                        working_directory: str = None, timeout_seconds: int = None,
                        environment: dict = None, load_profile: str = "false") -> Generator[dict, None, None]:
        """
        Execute command and streamingly return output
        
        Args:
            command: Command
            args: Command argument list
            working_directory: Working directory
            timeout_seconds: Timeout in seconds
            environment: Environment variables
            load_profile: Control bash environment loading: "false" (default) / "true" / "login"
            
        Yields:
            dict: {'type': 'stdout'|'stderr'|'finished', 'data': bytes|int}
        """
        from taurus.utils.generated.executor.v1 import command_service_pb2 as pb2
        from taurus.utils.generated.executor.v1 import command_service_pb2_grpc
        
        channel = self._get_channel()
        stub = pb2_grpc.ClientServiceStub(channel)
        
        request = pb2.CommandRequest(
            command=command,
            args=args or [],
            working_directory=working_directory or "",
            timeout_seconds=timeout_seconds or self.DEFAULT_TIMEOUT,
            environment=environment or {},
            merge_streams=False
        )
        if load_profile and load_profile != "false":
            request.environment["__TAURUS_LOAD_PROFILE__"] = load_profile
        
        try:
            timeout = (timeout_seconds or self.DEFAULT_TIMEOUT) + 10
            response_stream = stub.ExecuteCommand(request, timeout=timeout)
            
            for response in response_stream:
                if response.HasField('stdout_chunk'):
                    yield {'type': 'stdout', 'data': response.stdout_chunk}
                elif response.HasField('stderr_chunk'):
                    yield {'type': 'stderr', 'data': response.stderr_chunk}
                
                if response.finished:
                    yield {'type': 'finished', 'data': response.exit_code}
                    break
                
                if response.error_message:
                    yield {'type': 'error', 'data': response.error_message}
                    
        except grpc.RpcError as e:
            logger.error(f"gRPC call failed: {e.code()} - {e.details()}")
            yield {'type': 'error', 'data': f"gRPC error: {e.details()}"}
        except Exception as e:
            logger.error(f"Command execution exception: {e}")
            yield {'type': 'error', 'data': str(e)}
    
    def execute_command_sync(self, command: str, args: list = None,
                             working_directory: str = None, timeout_seconds: int = None,
                             environment: dict = None, load_profile: str = "false") -> CommandResult:
        """
        Synchronously execute command and return complete result
        
        Returns:
            CommandResult: Command execution result
        """
        result = CommandResult()
        
        for chunk in self.execute_command(command, args, working_directory, 
                                          timeout_seconds, environment, load_profile):
            if chunk['type'] == 'stdout':
                result.stdout += chunk['data'].decode('utf-8', errors='replace')
            elif chunk['type'] == 'stderr':
                result.stderr += chunk['data'].decode('utf-8', errors='replace')
            elif chunk['type'] == 'finished':
                result.exit_code = chunk['data']
            elif chunk['type'] == 'error':
                result.error_message = chunk['data']
        
        return result
    
    def upload_file(self, file_path: str, content_generator, total_size: int) -> Tuple[bool, str]:
        """
        Upload file to executor
        
        Args:
            file_path: Target file path
            content_generator: File content generator, yields bytes
            total_size: Total file size
            
        Returns:
            Tuple[bool, str]: (success, message)
        """
        from taurus.utils.generated.executor.v1 import command_service_pb2 as pb2
        from taurus.utils.generated.executor.v1 import command_service_pb2_grpc
        
        channel = self._get_channel()
        stub = pb2_grpc.FileTransferStub(channel)
        
        def request_generator():
            chunk_index = 0
            for chunk in content_generator:
                is_last = False
                # Note: cannot predict whether this is the last chunk here, controlled by caller
                yield pb2.UploadRequest(
                    file_path=file_path,
                    chunk=chunk,
                    is_last=False,
                    total_size=total_size,
                    chunk_index=chunk_index
                )
                chunk_index += 1
            
            # Send final empty chunk to mark end
            yield pb2.UploadRequest(
                file_path=file_path,
                chunk=b'',
                is_last=True,
                total_size=total_size,
                chunk_index=chunk_index
            )
        
        try:
            response = stub.UploadFile(request_generator(), timeout=self.DEFAULT_TIMEOUT)
            return response.success, response.message
        except grpc.RpcError as e:
            logger.error(f"File upload failed: {e.code()} - {e.details()}")
            return False, f"gRPC error: {e.details()}"
        except Exception as e:
            logger.error(f"File upload exception: {e}")
            return False, str(e)


def get_executor_client(host, use_tls: bool = False) -> ExecutorClient:
    """
    Create ExecutorClient based on Host object
    
    Args:
        host: Host model instance
        use_tls: Whether to use TLS
        
    Returns:
        ExecutorClient instance
    """
    host_ip = host.host_ip or 'localhost'
    return ExecutorClient(host_ip=host_ip, use_tls=use_tls)