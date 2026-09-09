#!/bin/bash
set -e

# 需要排除数据但保留DDL的表（日志类大表）
IGNORE_DATA_TABLES=(
    "taurus_backend.taurus_system_operation_log"
)

# MySQL连接配置
MYSQL_HOST="127.0.0.1"
MYSQL_PORT="3306"
MYSQL_USER="root"
MYSQL_PASS="123456"
MYSQL_DB="taurus_backend"

# mysqldump 公共参数
MYSQLDUMP_COMMON_OPTS=(
    --column-statistics=0
    --skip-lock-tables
    --routines
    --add-drop-table
    --disable-keys
    --extended-insert
    -h"${MYSQL_HOST}"
    -P"${MYSQL_PORT}"
    -u"${MYSQL_USER}"
    -p"${MYSQL_PASS}"
)

# 构造 --ignore-table 参数列表
IGNORE_OPTS=()
for table in "${IGNORE_DATA_TABLES[@]}"; do
    IGNORE_OPTS+=(--ignore-table="${table}")
done

# 获取当前日期
DATE=$(date +%Y%m%d_%H%M%S)

# 导出文件名
DUMP_FILE="taurus_backend.dump.sql"
DATE_DUMP_FILE="taurus_backend.dump.${DATE}.sql"

# 临时文件
SCHEMA_FILE=$(mktemp /tmp/taurus_schema.XXXXXX.sql)
DATA_FILE=$(mktemp /tmp/taurus_data.XXXXXX.sql)

# 清理临时文件
cleanup() {
    rm -f "${SCHEMA_FILE}" "${DATA_FILE}"
}
trap cleanup EXIT

echo "=== 开始导出 taurus_backend 数据库 ==="
echo "排除数据表（仅保留DDL结构）: ${IGNORE_DATA_TABLES[*]}"

# 第一步：导出全部表结构（DDL），包含所有被忽略数据的表
echo "[1/3] 导出表结构..."
/usr/bin/mysqldump "${MYSQLDUMP_COMMON_OPTS[@]}" --no-data "${MYSQL_DB}" > "${SCHEMA_FILE}"
echo "      表结构导出完成: $(wc -l < "${SCHEMA_FILE}") 行"

# 第二步：导出数据，排除指定表的数据
echo "[2/3] 导出数据（排除指定表）..."
/usr/bin/mysqldump "${MYSQLDUMP_COMMON_OPTS[@]}" --no-create-info "${IGNORE_OPTS[@]}" "${MYSQL_DB}" > "${DATA_FILE}"
echo "      数据导出完成: $(wc -l < "${DATA_FILE}") 行"

# 第三步：合并结构和数据
echo "[3/3] 合并导出文件..."
cat "${SCHEMA_FILE}" "${DATA_FILE}" > "${DATE_DUMP_FILE}"

# 创建latest软链接（指向最新导出文件）
ln -sf "${DATE_DUMP_FILE}" "${DUMP_FILE}"

echo ""
echo "=== 导出完成 ==="
echo "输出文件: ${DATE_DUMP_FILE}"
echo "文件大小: $(du -h "${DATE_DUMP_FILE}" | cut -f1)"
echo "latest软链接: ${DUMP_FILE} -> ${DATE_DUMP_FILE}"
echo ""
echo "验证DDL完整性："
for table in "${IGNORE_DATA_TABLES[@]}"; do
    table_name="${table#*.}"
    if grep -q "CREATE TABLE.*\`${table_name}\`" "${DATE_DUMP_FILE}"; then
        echo "  ✓ ${table_name} 表结构存在"
    else
        echo "  ✗ ${table_name} 表结构缺失!"
    fi
    if grep -q "INSERT INTO.*\`${table_name}\`" "${DATE_DUMP_FILE}"; then
        echo "  ⚠ ${table_name} 存在数据插入（应无数据）"
    else
        echo "  ✓ ${table_name} 数据已排除"
    fi
done