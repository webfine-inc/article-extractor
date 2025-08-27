import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = max(2, multiprocessing.cpu_count() * 2 + 1)
threads = 2
timeout = 60
graceful_timeout = 30
worker_class = "gthread"
accesslog = "-"
errorlog = "-"
loglevel = "info"
