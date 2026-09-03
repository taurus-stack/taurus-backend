# gunicorn.conf
# coding:utf-8
# startCommand:gunicorn -c gunicorn.py application.asgi:application
import multiprocessing
# 并行Worker process数, int, cpu数量*2+1 推荐Process count
workers = multiprocessing.cpu_count() * 2 + 1
# 指定每 Process开启的line程数
threads = 3
# 绑定的ip与Port
bind = '0.0.0.0:8000'
# settingDaemon process,将Process交给Page三方管理
daemon = 'false'
# 工作模式Coroutine, defaultYessync模式,推荐use gevent, 此处use与uvicorn配合use uvicorn.workers.UvicornWorker
worker_class = 'uvicorn.workers.UvicornWorker'
# setting最大并发量(每 workerprocessrequest的工作line程数, 正整数, 默认为1)
worker_connections = 10000
# 最大ClientConcurrency量, 默认情况下这 值为1000.此setting将影响gevent和eventlet工作模式
# 每 Worker process将在processmax_requestsrequest后自动re-start该Process
max_requests = 10000
max_requests_jitter = 200
# settingProcessfiledirectory
pidfile = './gunicorn.pid'
# LogLevel, 这 LogLevel指的YesErrorLog的Level, 而访问Log的Levelcannotsetting
loglevel = 'info'
# settinggunicorn访问LogFormat, ErrorLogcannotsetting
access_log_format = '' # worker_class 为 uvicorn.workers.UvicornWorker 时, LogFormat为Django的loggers
# 监听queue
backlog = 512
#Process名
proc_name = 'gunicorn_process'
# settingTimeout120s, 默认为30s.Per自己的需求进行settingtimeout = 120
timeout = 120
# timeoutRestart
graceful_timeout = 300
# 在keep-alivejoin上等待request的seconds, 默认情况下值为2.一般设定在1~5秒between.
keepalive = 3
# HTTPrequest行的最大大小, 此Parameters用于限制HTTPrequest行的allow大小, 默认情况下, 这 值为4094.
# 值Yes0~8190的数字.此Parameterscan防止任何DDOS攻击
limit_request_line = 5120
# 限制HTTPrequest中Request headersField的数量.
#  此Field用于限制Request headersField的数量以防止DDOS攻击, 与limit-request-field-size一起usecan提高Security.
# 默认情况下, 这 值为100, 这 值cannot超过32768
limit_request_fields = 101
# 限制HTTPrequest中Request headers的大小, 默认情况下这 值为8190.
# 值Yes一 整数or0, when该值为0时, 表示将对Request headers大小不做限制
limit_request_field_size = 0
# record到StandardOutput
accesslog = '-'
