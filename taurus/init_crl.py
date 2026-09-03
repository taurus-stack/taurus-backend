#!/usr/bin/env python
"""
CRL initialization script
Used to initialize Certificate Revocation List (CRL) environment
"""
import os
import sys
import django

# Set up Django environment
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

from django.conf import settings
from taurus.crl_manager import CRLManager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def initialize_crl():
    """Initialize CRL environment"""
    logger.info("Starting CRL environment initialization...")
    
    # Check if CA certificate exists
    ca_cert_path = getattr(settings, 'CA_CERT_PATH', None)
    ca_key_path = getattr(settings, 'CA_KEY_PATH', None)
    
    if not ca_cert_path or not ca_key_path:
        logger.error("CA certificate path not configured")
        return False
    
    if not os.path.exists(ca_cert_path) or not os.path.exists(ca_key_path):
        logger.error(f"CA certificate file does not exist: cert={ca_cert_path}, key={ca_key_path}")
        return False
    
    logger.info(f"CA certificate path: {ca_cert_path}")
    logger.info(f"CA private key path: {ca_key_path}")
    
    # Create CRL manager
    crl_manager = CRLManager()
    
    # Ensure OpenSSL config file exists
    config_path = crl_manager._ensure_openssl_config()
    logger.info(f"OpenSSL config file: {config_path}")
    
    # Generate initial CRL file
    success = crl_manager.generate_crl()
    
    if success:
        logger.info("CRL environment initialized successfully")
        logger.info(f"CRL file path: {crl_manager.get_crl_path()}")
        return True
    else:
        logger.error("CRL environment initialization failed")
        return False


if __name__ == '__main__':
    success = initialize_crl()
    sys.exit(0 if success else 1)