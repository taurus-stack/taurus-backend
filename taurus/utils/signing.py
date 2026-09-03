"""
Request signature and anti-replay attack utility
"""
import hashlib
import hmac
import time

from django.core.cache import cache


def generate_signature(host_id: str, secret: str, timestamp: int, nonce: str, body: str = '') -> str:
    """
    Generate request signature
    
    Args:
        host_id: Host UUID
        secret: Shared secret key
        timestamp: Request timestamp (seconds)
        nonce: Random string (anti-replay)
        body: Request body (optional, used for integrity check)
    
    Returns:
        HMAC-SHA256 signature (hexadecimal)
    """
    message = f"{host_id}:{timestamp}:{nonce}:{body}"
    return hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def verify_signature(
    host_id: str,
    secret: str,
    timestamp: int,
    nonce: str,
    signature: str,
    body: str = '',
    max_age: int = 300,
) -> tuple[bool, str]:
    """
    Validate request signature
    
    Args:
        host_id: Host UUID
        secret: Shared secret key
        timestamp: Request timestamp (seconds)
        nonce: Random string
        signature: Client-provided signature
        body: Request body
        max_age: Signature max validity period (seconds), default 5 minutes
    
    Returns:
        (is valid, error message)
    """
    # 1. Check timestamp (anti-replay: signature must be within validity period)
    current_time = int(time.time())
    if abs(current_time - timestamp) > max_age:
        return False, f"Signature expired (timestamp: {timestamp}, current: {current_time})"
    
    # 2. Check if nonce has been used (anti-replay)
    nonce_key = f"request_nonce:{nonce}"
    if cache.get(nonce_key):
        return False, f"Nonce already used: {nonce}"
    
    # 3. Validate signature
    expected_signature = generate_signature(host_id, secret, timestamp, nonce, body)
    if not hmac.compare_digest(signature, expected_signature):
        return False, "Signature mismatch"
    
    # 4. Record nonce as used (validity period = max_age)
    cache.set(nonce_key, True, timeout=max_age)
    
    return True, ""