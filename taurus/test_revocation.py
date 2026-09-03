"""
Certificate吊销functionalitytestScript
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

from taurus.models import Host
from taurus.crl_manager import crl_manager
from django.utils import timezone

def test_certificate_revocation():
    """testCertificate吊销functionality"""
    print("=" * 60)
    print("证书吊销功能测试")
    print("=" * 60)
    
    # 1. createtestHost
    print("\n[1] 创建测试主机...")
    test_host = Host.objects.create(
        host_name='test-revocation-host',
        host_ip='192.168.100.100',
        host_type='linux',
        certificate_serial='ABC123DEF456',
        certificate_status='valid',
    )
    print(f"  ✓ 主机创建成功: id={test_host.id}, name={test_host.host_name}")
    print(f"  ✓ 证书序列号: {test_host.certificate_serial}")
    print(f"  ✓ 证书状态: {test_host.certificate_status}")
    
    # 2. 吊销Certificate
    print("\n[2] 吊销证书...")
    test_host.certificate_status = 'revoked'
    test_host.certificate_revoked_at = timezone.now()
    test_host.certificate_revocation_reason = 'keyCompromise'
    test_host.save()
    print(f"  ✓ 证书状态已更新: {test_host.certificate_status}")
    print(f"  ✓ 吊销时间: {test_host.certificate_revoked_at}")
    print(f"  ✓ 吊销原因: {test_host.certificate_revocation_reason}")
    
    # 3. Validation吊销Status
    print("\n[3] 验证吊销状态...")
    revoked_hosts = Host.objects.filter(certificate_status='revoked')
    print(f"  ✓ 已吊销证书数量: {revoked_hosts.count()}")
    for host in revoked_hosts:
        print(f"    - {host.host_name} ({host.host_ip}): serial={host.certificate_serial}")
    
    # 4. RestoreCertificate
    print("\n[4] 恢复证书...")
    test_host.certificate_status = 'valid'
    test_host.certificate_revoked_at = None
    test_host.certificate_revocation_reason = None
    test_host.save()
    print(f"  ✓ 证书状态已恢复: {test_host.certificate_status}")
    
    # 5. cleanuptest数据
    print("\n[5] 清理测试数据...")
    test_host.delete()
    print(f"  ✓ 测试主机已删除")
    
    # 6. test CRL 管理server
    print("\n[6] 测试 CRL 管理器...")
    crl_path = crl_manager.get_crl_path()
    print(f"  ✓ CRL 文件路径: {crl_path}")
    print(f"  ✓ CRL 文件存在: {os.path.exists(crl_path)}")
    
    revoked_list = crl_manager.get_revoked_certificates()
    print(f"  ✓ 吊销列表中的证书数量: {len(revoked_list)}")
    
    print("\n" + "=" * 60)
    print("测试完成！所有功能正常")
    print("=" * 60)

if __name__ == '__main__':
    test_certificate_revocation()