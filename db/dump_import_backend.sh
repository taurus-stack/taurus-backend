#!/bin/bash
# 导入最新的数据库备份文件 (taurus_backend.dump.sql -> taurus_backend.dump.{date}.sql)
# 如需导入特定日期的备份，请将下面的 taurus_backend.dump.sql 替换为具体文件名

mysql -h127.0.0.1 -uroot -p123456 <<EOF
drop database if exists taurus_backend;
create database taurus_backend;
use taurus_backend;
source taurus_backend.dump.sql;
EOF